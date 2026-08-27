"""‎/traces and /admin/metrics observability endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text

from app.core.deps import ConnDep, UserDep, require_roles

router = APIRouter(tags=["observability"])


@router.get("/traces")
async def list_traces(
    user: UserDep, conn: ConnDep, limit: int = 50, only_refused: bool = False
) -> list[dict]:
    """Users see their own traces; admins see all traces."""
    rows = await conn.execute(
        text(
            """
            SELECT t.trace_uuid, t.question, t.route, t.refused, t.stop_reason,
                   t.groundedness, t.hallucination_flag, t.latency_ms,
                   t.prompt_tokens, t.completion_tokens, t.created_at,
                   u.email AS user_email
            FROM traces t
            LEFT JOIN users u ON u.id = t.user_id
            WHERE (:is_admin OR t.user_id = :user_id)
              AND (:only_refused = false OR t.refused)
            ORDER BY t.created_at DESC
            LIMIT :limit
            """
        ),
        {
            "is_admin": user.is_admin,
            "user_id": user.id,
            "only_refused": only_refused,
            "limit": min(limit, 200),
        },
    )
    out = []
    for r in rows.all():
        m = dict(r._mapping)
        m["trace_uuid"] = str(m["trace_uuid"])
        m["created_at"] = m["created_at"].isoformat()
        out.append(m)
    return out


@router.get("/traces/{trace_uuid}")
async def get_trace(trace_uuid: str, user: UserDep, conn: ConnDep) -> dict:
    """Return the complete pipeline for one question, including stage scores."""
    row = (
        await conn.execute(
            text(
                """
                SELECT t.*, u.email AS user_email
                FROM traces t LEFT JOIN users u ON u.id = t.user_id
                WHERE t.trace_uuid = CAST(:id AS uuid)
                  AND (:is_admin OR t.user_id = :user_id)
                """
            ),
            {"id": trace_uuid, "is_admin": user.is_admin, "user_id": user.id},
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trace not found")

    m = dict(row._mapping)
    m["trace_uuid"] = str(m["trace_uuid"])
    for key in ("created_at",):
        if m.get(key):
            m[key] = m[key].isoformat()
    m.pop("id", None)
    m.pop("user_id", None)
    if m.get("session_id"):
        m["session_id"] = str(m["session_id"])
    if m.get("groundedness") is not None:
        m["groundedness"] = float(m["groundedness"])
    if m.get("estimated_cost") is not None:
        m["estimated_cost"] = float(m["estimated_cost"])
    return m


@router.get("/admin/metrics")
async def metrics(
    conn: ConnDep,
    user: Annotated[object, Depends(require_roles("admin"))],
    days: int = 30,
) -> dict:
    """Dashboard aggregates computed in one query without looping over the table."""
    row = (
        await conn.execute(
            text(
                """
                SELECT
                    count(*)                                                   AS requests,
                    count(*) FILTER (WHERE refused)                            AS refusals,
                    count(*) FILTER (WHERE hallucination_flag)                 AS hallucinations,
                    count(*) FILTER (WHERE stop_reason <> 'completed')         AS abnormal_stops,
                    round(avg(latency_ms))                                     AS avg_latency_ms,
                    percentile_disc(0.5) WITHIN GROUP (ORDER BY latency_ms)    AS p50_latency_ms,
                    percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms)   AS p95_latency_ms,
                    coalesce(sum(prompt_tokens + completion_tokens), 0)        AS total_tokens,
                    coalesce(sum(estimated_cost), 0)                           AS total_cost,
                    round(avg(groundedness) FILTER (WHERE groundedness IS NOT NULL), 3)
                                                                               AS avg_groundedness
                FROM traces
                WHERE created_at > now() - make_interval(days => :days)
                """
            ),
            {"days": days},
        )
    ).first()

    by_stop = await conn.execute(
        text(
            """SELECT stop_reason, count(*) AS n FROM traces
               WHERE created_at > now() - make_interval(days => :days)
               GROUP BY stop_reason ORDER BY n DESC"""
        ),
        {"days": days},
    )
    blocked = await conn.execute(
        text(
            """SELECT action, count(*) AS n FROM audit_log
               WHERE outcome = 'blocked' AND created_at > now() - make_interval(days => :days)
               GROUP BY action ORDER BY n DESC LIMIT 10"""
        ),
        {"days": days},
    )
    actions = await conn.execute(
        text("SELECT status, count(*) AS n FROM agent_actions GROUP BY status ORDER BY n DESC")
    )

    m = {k: (float(v) if hasattr(v, "quantize") else v) for k, v in dict(row._mapping).items()}
    return {
        "window_days": days,
        **m,
        "by_stop_reason": {r.stop_reason: r.n for r in by_stop.all()},
        "blocked_by_action": {r.action: r.n for r in blocked.all()},
        "actions_by_status": {r.status: r.n for r in actions.all()},
    }
