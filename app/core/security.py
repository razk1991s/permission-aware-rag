"""גיבוב סיסמאות והנפקת טוקנים."""

from __future__ import annotations

import datetime as dt
import uuid

import bcrypt
import jwt

from app.config import settings


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # hash פגום או בפורמט אחר — נכשל סגור, בלי לחשוף את הסיבה למשתמש.
        return False


def create_access_token(*, user_id: int, email: str, roles: list[str]) -> str:
    now = dt.datetime.now(dt.UTC)
    payload = {
        "sub": str(user_id),
        "email": email,
        "roles": sorted(roles),
        "iat": now,
        "exp": now + dt.timedelta(minutes=settings.jwt_ttl_minutes),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """מפענח ומאמת. זורק jwt.PyJWTError בכל כשל — כולל פקיעה."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
