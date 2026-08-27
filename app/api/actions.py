"""‎/actions - action requests and approval gates."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.agent.actions import (
    ACTION_SPECS,
    ActionNotAllowed,
    decide_action,
    list_actions,
    request_action,
)
from app.agent.approval import resolve_approval_tier
from app.core.deps import ConnDep, UserDep, require_roles

router = APIRouter(prefix="/actions", tags=["actions"])


class ActionRequest(BaseModel):
    action_type: str = Field(examples=["create_refund"])
    payload: dict = Field(examples=[{"customer_name": "דנה לוי", "amount": 4200, "reason": "חיוב כפול"}])
    trace_uuid: str | None = None


class DecisionRequest(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=1000)


@router.get("/specs")
async def action_specs(user: UserDep) -> list[dict]:
    return [
        {
            "action_type": s.name,
            "description": s.description,
            "required_roles": list(s.required_roles),
            "can_request": bool(user.roles.intersection(s.required_roles)),
        }
        for s in ACTION_SPECS.values()
    ]


@router.get("/preview")
async def preview_tier(amount: float, user: UserDep, conn: ConnDep) -> dict:
    """Return who must approve an amount without creating a request.

    Useful for demos and tests: demonstrates that the threshold comes from
    the procedure document rather than application code.
    """
    tier = await resolve_approval_tier(conn, user_id=user.id, amount=amount)
    return {
        "amount": amount,
        "tier": tier.name,
        "required_role": tier.role,
        "policy_citation": tier.citation,
        "reason": tier.reason,
        "source": tier.source,
        "auto_approved_for_you": tier.role in user.roles or user.is_admin,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_action(body: ActionRequest, user: UserDep, conn: ConnDep) -> dict:
    try:
        record = await request_action(
            conn,
            user_id=user.id,
            roles=set(user.roles),
            action_type=body.action_type,
            payload=body.payload,
            trace_uuid=body.trace_uuid,
        )
    except ActionNotAllowed as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return record.to_dict()


@router.get("")
async def get_actions(
    conn: ConnDep,
    user: Annotated[object, Depends(require_roles("finance", "hr", "support", "admin"))],
    status_filter: str | None = None,
    limit: int = 50,
) -> list[dict]:
    return await list_actions(conn, status=status_filter, limit=min(limit, 200))


@router.post("/{action_id}/decision")
async def decide(action_id: int, body: DecisionRequest, user: UserDep, conn: ConnDep) -> dict:
    try:
        record = await decide_action(
            conn,
            action_id=action_id,
            approver_id=user.id,
            approver_roles=set(user.roles),
            approve=body.approve,
            note=body.note,
        )
    except ActionNotAllowed as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return record.to_dict()
