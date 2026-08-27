"""FastAPI dependencies: identity, authorization, and audit logging.

Core principle (ADR 0002): authorization is resolved once at request start
from the JWT and database. It is stored in CurrentUser and is not passed
through a parameter that a user, document, or model can influence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.security import decode_access_token
from app.db import get_conn

ConnDep = Annotated[AsyncConnection, Depends(get_conn)]


@dataclass(frozen=True)
class CurrentUser:
    id: int
    email: str
    display_name: str | None
    roles: frozenset[str]
    allowed_doc_ids: frozenset[int] = field(default_factory=frozenset)

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    def has_any(self, *roles: str) -> bool:
        return bool(self.roles.intersection(roles))


# ---------------------------------------------------------------- Audit
async def audit(
    conn: AsyncConnection,
    *,
    actor_id: int | None,
    action: str,
    outcome: str,
    resource: str | None = None,
    actor_type: str = "user",
    detail: dict | None = None,
) -> None:
    """Write one row to audit_log. The table is append-only."""
    await conn.execute(
        text(
            """INSERT INTO audit_log (actor_id, actor_type, action, resource, outcome, detail)
               VALUES (:actor_id, :actor_type, :action, :resource, :outcome, CAST(:detail AS jsonb))"""
        ),
        {
            "actor_id": actor_id,
            "actor_type": actor_type,
            "action": action,
            "resource": resource,
            "outcome": outcome,
            "detail": json.dumps(detail or {}, ensure_ascii=False),
        },
    )


# ---------------------------------------------------------------- Authorization
ALLOWED_DOCS_SQL = text(
    """
    SELECT DISTINCT a.document_id
    FROM document_acl a
    JOIN user_roles ur ON ur.role_id = a.role_id
    WHERE ur.user_id = :user_id
      AND a.permission = 'read'
    """
)


async def resolve_allowed_doc_ids(conn: AsyncConnection, user_id: int) -> frozenset[int]:
    """Return the documents the user may read; this is the single source of truth.

    Admin receives no special bypass here: its ACL also comes from real table
    rows created during ingestion, leaving no separate bypass to maintain.
    """
    rows = await conn.execute(ALLOWED_DOCS_SQL, {"user_id": user_id})
    return frozenset(r[0] for r in rows.all())


USER_SQL = text(
    """
    SELECT u.id, u.email, u.display_name, u.is_active,
           COALESCE(array_agg(r.name) FILTER (WHERE r.name IS NOT NULL), '{}') AS roles
    FROM users u
    LEFT JOIN user_roles ur ON ur.user_id = u.id
    LEFT JOIN roles r ON r.id = ur.role_id
    WHERE u.id = :user_id
    GROUP BY u.id
    """
)


async def get_current_user(
    conn: ConnDep,
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Access token required")

    try:
        payload = decode_access_token(authorization.split(" ", 1)[1].strip())
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Access token expired") from None
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid access token") from None

    user_id = int(payload["sub"])
    row = (await conn.execute(USER_SQL, {"user_id": user_id})).first()
    if row is None or not row.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User is inactive")

    # Roles come from the database rather than the JWT, so an old token cannot
    # restore access that was revoked.
    roles = frozenset(row.roles)
    allowed = await resolve_allowed_doc_ids(conn, user_id)
    return CurrentUser(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        roles=roles,
        allowed_doc_ids=allowed,
    )


UserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_roles(*required: str):
    """Require at least one role; every denial is recorded in audit_log."""

    async def _guard(user: UserDep, conn: ConnDep) -> CurrentUser:
        if not user.has_any(*required):
            await audit(
                conn,
                actor_id=user.id,
                action="access_denied",
                outcome="blocked",
                resource=",".join(required),
                detail={"user_roles": sorted(user.roles)},
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not authorized for this action")
        return user

    return _guard
