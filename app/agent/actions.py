"""Write actions and approval gates.

This module turns the system from read-only into a system that writes,
so it is governed by three principles:

1. An action requiring approval is stopped and persisted, not executed and later canceled.
2. The approval tier is derived from the procedure, and its citation is stored with the request.
3. Authority is checked when the decision is made, not when the request is created.

Implementation note (ADR 0008): instead of the LangGraph checkpointer, the
state required to complete an action is stored in our `agent_actions` table.
The PostgreSQL checkpoint package uses psycopg while the application uses
asyncpg, so maintaining two drivers is not worthwhile at this scale.
Explicit persisted actions are also easier to inspect in SQL.
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


# ------------------------------------------------------------- Action definitions
@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    required_roles: tuple[str, ...]
    amount_field: str | None       # Field that determines the approval tier


ACTION_SPECS: dict[str, ActionSpec] = {
    "create_refund": ActionSpec(
        "create_refund",
        "Create a customer refund request",
        ("support", "finance", "admin"),
        amount_field="amount",
    ),
}


class ActionNotAllowed(PermissionError):
    pass


# ------------------------------------------------------------------ Request
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
        raise ActionNotAllowed(f"Unknown action type: {action_type}")

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
            result={"reason": "You are not authorized to request this action"},
        )

    amount = float(payload.get(spec.amount_field or "", 0) or 0)
    tier = await resolve_approval_tier(conn, user_id=user_id, amount=amount, action_type=action_type)

    # A user who already has the required role is approved and executed immediately.
    auto = tier.role in roles or "admin" in roles
    if auto:
        record = await _insert(
            conn, thread_id=str(uuid.uuid4()), action_type=action_type, payload=payload,
            status=ActionStatus.PENDING, tier=tier, user_id=user_id, trace_uuid=trace_uuid,
        )
        return await _execute_and_close(conn, record, approver_id=user_id, note="Automatic approval by authority")

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


# ------------------------------------------------------------------ Decision
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
        raise ActionNotAllowed("Request not found")
    if row.status != ActionStatus.PENDING:
        raise ActionNotAllowed(f"Request already handled (status {row.status})")

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


# ------------------------------------------------------------------ Execution
async def _execute_and_close(
    conn: AsyncConnection, record: ActionRecord, *, approver_id: int, note: str | None
) -> ActionRecord:
    """Execute the action. Operational data is written here and nowhere else."""
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
            raise ValueError(f"Customer not found: {p.get('customer_name')!r}")

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
                    "reason": p.get("reason") or "Created by the agent",
                },
            )
        ).first()
        return {"refund_request_id": row.id, "customer_id": customer.id}

    raise ValueError(f"Action is not implemented: {record.action_type}")


# ------------------------------------------------------------------ Listing
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
