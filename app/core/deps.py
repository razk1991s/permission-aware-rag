"""תלויות FastAPI: זהות, הרשאות ותיעוד.

עיקרון מרכזי (ADR 0002): ההרשאות נפתרות פעם אחת, בתחילת הבקשה, מתוך
ה-JWT ומסד הנתונים. הן נכנסות ל-CurrentUser ולא עוברות דרך שום פרמטר
שגורם חיצוני — משתמש, מסמך או מודל — יכול להשפיע עליו.
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


# ---------------------------------------------------------------- תיעוד
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
    """כותב שורה ל-audit_log. הטבלה append-only — אין כאן עדכון או מחיקה."""
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


# ---------------------------------------------------------------- הרשאות
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
    """אילו מסמכים המשתמש רשאי לקרוא. זו נקודת האמת היחידה.

    admin אינו מקבל כאן יחס מיוחד: ה-ACL שלו נובע משורות אמיתיות בטבלה,
    שנוצרות באינג'סט. כך אין מסלול עוקף שצריך לזכור לתחזק.
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
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "נדרש טוקן גישה")

    try:
        payload = decode_access_token(authorization.split(" ", 1)[1].strip())
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "הטוקן פג תוקף") from None
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "טוקן לא תקין") from None

    user_id = int(payload["sub"])
    row = (await conn.execute(USER_SQL, {"user_id": user_id})).first()
    if row is None or not row.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "המשתמש אינו פעיל")

    # התפקידים נלקחים מהמסד ולא מה-JWT: טוקן שהונפק לפני שינוי תפקיד
    # לא יעניק הרשאה שכבר נשללה.
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
    """תלות שדורשת לפחות אחד מהתפקידים. כל דחייה נרשמת ל-audit_log."""

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
            raise HTTPException(status.HTTP_403_FORBIDDEN, "אין לך הרשאה לפעולה זו")
        return user

    return _guard
