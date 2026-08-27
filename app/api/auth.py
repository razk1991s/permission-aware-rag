"""התחברות וזהות."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.deps import ConnDep, UserDep, audit
from app.core.security import create_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    # במכוון str ולא EmailStr: התחברות שנכשלת צריכה להחזיר 401 אחיד,
    # ולא 422 שמסגיר שהכתובת בכלל לא בפורמט תקין. חוץ מזה, EmailStr
    # פוסל דומיינים שמורים כמו meridian.local שמשמשים בסביבת הדמו.
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    roles: list[str]


class MeResponse(BaseModel):
    id: int
    email: str
    display_name: str | None
    roles: list[str]
    allowed_documents: int


LOGIN_SQL = text(
    """
    SELECT u.id, u.email, u.password_hash, u.is_active,
           COALESCE(array_agg(r.name) FILTER (WHERE r.name IS NOT NULL), '{}') AS roles
    FROM users u
    LEFT JOIN user_roles ur ON ur.user_id = u.id
    LEFT JOIN roles r ON r.id = ur.role_id
    WHERE lower(u.email) = lower(:email)
    GROUP BY u.id
    """
)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, conn: ConnDep) -> TokenResponse:
    row = (await conn.execute(LOGIN_SQL, {"email": body.email})).first()

    # אותה תשובה בדיוק למשתמש לא קיים ולסיסמה שגויה, כדי לא לאפשר
    # מיפוי כתובות דוא"ל קיימות.
    if row is None or not row.is_active or not verify_password(body.password, row.password_hash):
        await audit(
            conn,
            actor_id=row.id if row else None,
            action="login",
            outcome="blocked",
            resource=body.email,
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "פרטי התחברות שגויים")

    await audit(conn, actor_id=row.id, action="login", outcome="allowed")
    roles = list(row.roles)
    return TokenResponse(
        access_token=create_access_token(user_id=row.id, email=row.email, roles=roles),
        roles=sorted(roles),
    )


@router.get("/me", response_model=MeResponse)
async def me(user: UserDep) -> MeResponse:
    return MeResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        roles=sorted(user.roles),
        allowed_documents=len(user.allowed_doc_ids),
    )
