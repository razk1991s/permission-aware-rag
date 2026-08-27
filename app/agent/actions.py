"""פעולות כתיבה ושערי אישור.

זה החלק שהופך את המערכת ממערכת שקוראת למערכת שכותבת — ולכן זה גם החלק
שדורש ממשל. שלושה עקרונות:

1. פעולה שדורשת אישור **נעצרת ונשמרת**, ולא מבוצעת ואז מבוטלת.
2. דרג האישור נגזר מהנוהל, והציטוט שהוביל אליו נשמר עם הבקשה — כדי
   שהמאשר יוכל לאמת את ההיגיון ולא רק לסמוך עליו.
3. הסמכות נבדקת **בזמן ההחלטה**, לא בזמן הבקשה: מי שאיבד תפקיד לא
   יכול לאשר בקשה שהמתינה מאז.

הערה על מימוש (ADR 0008): במקום checkpointer של LangGraph, המצב הדרוש
להשלמת הפעולה נשמר בטבלה `agent_actions` שלנו. הסיבה מעשית —
langgraph-checkpoint-postgres מבוסס psycopg, והמערכת כולה על asyncpg,
ושני דרייברים לאותו מסד הם מחיר תפעולי שלא משתלם בקנה המידה הזה.
הפעולות שנשמרות כאן הן פשוטות ומוגדרות היטב, ולכן טבלה מפורשת גם
קריאה יותר וגם ניתנת לתחקור ב-SQL.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.agent.approval import ApprovalTier, can_approve, resolve_approval_tier
from app.core.deps import audit

log = logging.getLogger(__name__)


class ActionStatus:
    COMPLETED = "completed"
    PENDING = "pending_approval"
    BLOCKED = "blocked"
    REJECTED = "rejected"
    RECOMMENDED = "recommended"
    FAILED = "failed"


ALL_STATUSES = (
    ActionStatus.COMPLETED,
    ActionStatus.PENDING,
    ActionStatus.BLOCKED,
    ActionStatus.REJECTED,
    ActionStatus.RECOMMENDED,
    ActionStatus.FAILED,
)


@dataclass
class ActionRecord:
    id: int
    thread_id: str
    action_type: str
    payload: dict
    status: str
    required_role: str | None
    policy_citation: str | None
    requested_by: int | None
    approved_by: int | None = None
    decision_note: str | None = None
    result: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------- הגדרת פעולות
@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    required_roles: tuple[str, ...]
    amount_field: str | None       # השדה שקובע את דרג האישור


ACTION_SPECS: dict[str, ActionSpec] = {
    "create_refund": ActionSpec(
        "create_refund",
        "פתיחת בקשת זיכוי ללקוח",
        ("support", "finance", "admin"),
        amount_field="amount",
    ),
}


class ActionNotAllowed(PermissionError):
    pass


# ------------------------------------------------------------------ בקשה
async def request_action(
    conn: AsyncConnection,
    *,
    user_id: int,
    roles: set[str],
    action_type: str,
    payload: dict[str, Any],
    trace_uuid: str | None = None,
) -> ActionRecord:
    spec = ACTION_SPECS.get(action_type)
    if spec is None:
        raise ActionNotAllowed(f"סוג פעולה לא מוכר: {action_type}")

    if not roles.intersection(spec.required_roles):
        await audit(
            conn, actor_id=user_id, action=f"action:{action_type}", outcome="blocked",
            detail={"required": list(spec.required_roles)},
        )
        return await _insert(
            conn,
            thread_id=str(uuid.uuid4()),
            action_type=action_type,
            payload=payload,
            status=ActionStatus.BLOCKED,
            tier=None,
            user_id=user_id,
            trace_uuid=trace_uuid,
            result={"reason": "אין הרשאה לבקש פעולה זו"},
        )

    amount = float(payload.get(spec.amount_field or "", 0) or 0)
    tier = await resolve_approval_tier(conn, user_id=user_id, amount=amount, action_type=action_type)

    # מי שכבר מחזיק בתפקיד המאשר — הפעולה מאושרת אוטומטית ומבוצעת מיד.
    auto = tier.role in roles or "admin" in roles
    if auto:
        record = await _insert(
            conn, thread_id=str(uuid.uuid4()), action_type=action_type, payload=payload,
            status=ActionStatus.PENDING, tier=tier, user_id=user_id, trace_uuid=trace_uuid,
        )
        return await _execute_and_close(conn, record, approver_id=user_id, note="אישור אוטומטי בסמכות")

    record = await _insert(
        conn, thread_id=str(uuid.uuid4()), action_type=action_type, payload=payload,
        status=ActionStatus.PENDING, tier=tier, user_id=user_id, trace_uuid=trace_uuid,
    )
    await audit(
        conn, actor_id=user_id, action=f"action:{action_type}", outcome="allowed",
        resource=str(record.id),
        detail={"status": record.status, "tier": tier.name, "citation": tier.citation},
    )
    return record


async def _insert(
    conn: AsyncConnection,
    *,
    thread_id: str,
    action_type: str,
    payload: dict,
    status: str,
    tier: ApprovalTier | None,
    user_id: int,
    trace_uuid: str | None,
    result: dict | None = None,
) -> ActionRecord:
    row = (
        await conn.execute(
            text(
                """
                INSERT INTO agent_actions
                    (trace_uuid, thread_id, action_type, payload, status, requested_by,
                     required_role, policy_citation, result)
                VALUES
                    (CAST(:trace_uuid AS uuid), :thread_id, :action_type,
                     CAST(:payload AS jsonb), :status, :requested_by,
                     :required_role, :policy_citation, CAST(:result AS jsonb))
                RETURNING id
                """
            ),
            {
                "trace_uuid": trace_uuid,
                "thread_id": thread_id,
                "action_type": action_type,
                "payload": json.dumps(payload, ensure_ascii=False),
                "status": status,
                "requested_by": user_id,
                "required_role": tier.role if tier else None,
                "policy_citation": (
                    f"{tier.citation} — {tier.reason}" if tier else None
                ),
                "result": json.dumps(result, ensure_ascii=False) if result else None,
            },
        )
    ).first()

    return ActionRecord(
        id=row.id,
        thread_id=thread_id,
        action_type=action_type,
        payload=payload,
        status=status,
        required_role=tier.role if tier else None,
        policy_citation=f"{tier.citation} — {tier.reason}" if tier else None,
        requested_by=user_id,
        result=result,
    )


# ------------------------------------------------------------------ החלטה
async def decide_action(
    conn: AsyncConnection,
    *,
    action_id: int,
    approver_id: int,
    approver_roles: set[str],
    approve: bool,
    note: str | None = None,
) -> ActionRecord:
    row = (
        await conn.execute(
            text(
                """SELECT id, thread_id, action_type, payload, status, requested_by,
                          required_role, policy_citation
                   FROM agent_actions WHERE id = :id FOR UPDATE"""
            ),
            {"id": action_id},
        )
    ).first()
    if row is None:
        raise ActionNotAllowed("הבקשה לא נמצאה")
    if row.status != ActionStatus.PENDING:
        raise ActionNotAllowed(f"הבקשה כבר טופלה (סטטוס {row.status})")

    tier = ApprovalTier(
        name=row.required_role or "committee",
        role=row.required_role or "admin",
        max_amount=None,
        citation=row.policy_citation or "",
        reason="",
        source="stored",
    )
    allowed, why = can_approve(
        approver_roles=approver_roles, tier=tier, is_requester=(approver_id == row.requested_by)
    )
    if not allowed:
        await audit(
            conn, actor_id=approver_id, action="action:decide", outcome="blocked",
            resource=str(action_id), detail={"reason": why},
        )
        raise ActionNotAllowed(why)

    record = ActionRecord(
        id=row.id,
        thread_id=row.thread_id,
        action_type=row.action_type,
        payload=row.payload if isinstance(row.payload, dict) else json.loads(row.payload),
        status=row.status,
        required_role=row.required_role,
        policy_citation=row.policy_citation,
        requested_by=row.requested_by,
    )

    if not approve:
        await conn.execute(
            text(
                """UPDATE agent_actions
                   SET status = :status, approved_by = :by, decided_at = now(), decision_note = :note
                   WHERE id = :id"""
            ),
            {"status": ActionStatus.REJECTED, "by": approver_id, "note": note, "id": action_id},
        )
        await audit(
            conn, actor_id=approver_id, action="action:reject", outcome="allowed",
            resource=str(action_id), detail={"note": note},
        )
        record.status = ActionStatus.REJECTED
        record.approved_by = approver_id
        record.decision_note = note
        return record

    return await _execute_and_close(conn, record, approver_id=approver_id, note=note)


# ------------------------------------------------------------------ ביצוע
async def _execute_and_close(
    conn: AsyncConnection, record: ActionRecord, *, approver_id: int, note: str | None
) -> ActionRecord:
    """מבצע את הפעולה בפועל. כאן, ורק כאן, נכתב משהו לנתונים התפעוליים."""
    try:
        result = await _perform(conn, record)
        status = ActionStatus.COMPLETED
    except Exception as exc:  # noqa: BLE001
        log.exception("action %s failed", record.id)
        result, status = {"error": str(exc)}, ActionStatus.FAILED

    await conn.execute(
        text(
            """UPDATE agent_actions
               SET status = :status, approved_by = :by, decided_at = now(),
                   decision_note = :note, result = CAST(:result AS jsonb)
               WHERE id = :id"""
        ),
        {
            "status": status,
            "by": approver_id,
            "note": note,
            "result": json.dumps(result, ensure_ascii=False),
            "id": record.id,
        },
    )
    await audit(
        conn,
        actor_id=approver_id,
        action=f"action:{record.action_type}:execute",
        outcome="allowed" if status == ActionStatus.COMPLETED else "error",
        resource=str(record.id),
        detail=result,
    )
    record.status = status
    record.approved_by = approver_id
    record.decision_note = note
    record.result = result
    return record


async def _perform(conn: AsyncConnection, record: ActionRecord) -> dict:
    if record.action_type == "create_refund":
        p = record.payload
        customer = (
            await conn.execute(
                text("SELECT id FROM customers WHERE full_name = :n LIMIT 1"),
                {"n": p.get("customer_name")},
            )
        ).first()
        if customer is None:
            raise ValueError(f"לא נמצא לקוח בשם {p.get('customer_name')!r}")

        row = (
            await conn.execute(
                text(
                    """INSERT INTO refund_requests (customer_id, amount, reason, status)
                       VALUES (:cid, :amount, :reason, 'open')
                       RETURNING id"""
                ),
                {
                    "cid": customer.id,
                    "amount": float(p["amount"]),
                    "reason": p.get("reason") or "נפתח דרך הסוכן",
                },
            )
        ).first()
        return {"refund_request_id": row.id, "customer_id": customer.id}

    raise ValueError(f"אין מימוש לפעולה {record.action_type}")


# ------------------------------------------------------------------ קריאה
async def list_actions(
    conn: AsyncConnection, *, status: str | None = None, limit: int = 50
) -> list[dict]:
    rows = await conn.execute(
        text(
            """
            SELECT a.id, a.thread_id, a.action_type, a.payload, a.status, a.required_role,
                   a.policy_citation, a.decision_note, a.result, a.created_at, a.decided_at,
                   ru.email AS requested_by_email, au.email AS approved_by_email
            FROM agent_actions a
            LEFT JOIN users ru ON ru.id = a.requested_by
            LEFT JOIN users au ON au.id = a.approved_by
            WHERE (CAST(:status AS text) IS NULL OR a.status = :status)
            ORDER BY a.created_at DESC
            LIMIT :limit
            """
        ),
        {"status": status, "limit": limit},
    )
    out = []
    for r in rows.all():
        m = dict(r._mapping)
        for key in ("created_at", "decided_at"):
            if m.get(key) is not None:
                m[key] = m[key].isoformat()
        out.append(m)
    return out
