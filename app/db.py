"""חיבור למסד הנתונים והרצת מיגרציות.

הפרויקט לא משתמש ב-ORM: כל השאילתות נכתבות ב-SQL מפורש. הסיבה היא
ADR 0001 ו-ADR 0002 — הליבה של המערכת היא שאילתה אחת שמסננת הרשאות
ומדרגת באותו מהלך, ושכבת הפשטה שמסתירה אותה מזיקה יותר משהיא עוזרת.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.config import ROOT, settings

log = logging.getLogger(__name__)

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.db_echo,
            pool_size=10,
            max_overflow=5,
            pool_pre_ping=True,
        )
    return _engine


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


async def get_conn() -> AsyncIterator[AsyncConnection]:
    """תלות ל-FastAPI: חיבור בתוך טרנזקציה, נסגר בסוף הבקשה."""
    engine = get_engine()
    async with engine.begin() as conn:
        yield conn


# ---------------------------------------------------------------- מיגרציות
MIGRATIONS_DIR: Path = ROOT / "migrations"

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


async def run_migrations() -> list[str]:
    """מריץ כל קובץ .sql בתיקיית migrations לפי סדר שמות, פעם אחת בלבד.

    מספיק ל-30 מיגרציות ולפרויקט בגודל הזה. אם הפרויקט יגדל לכדי שינויי
    סכמה שמחייבים downgrade — כאן המקום להחליף ל-Alembic.
    """
    applied: list[str] = []
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text(_TRACKING_TABLE))
        done = {
            row[0]
            for row in (await conn.execute(text("SELECT filename FROM schema_migrations"))).all()
        }

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            log.info("applying migration %s", path.name)
            # asyncpg מריץ כל פקודה כ-prepared statement, ופרוטוקול זה
            # אינו תומך בכמה פקודות במחרוזת אחת. לכן פונים ישירות לחיבור
            # הנהג, שמשתמש ב-simple query protocol ומריץ קובץ שלם.
            raw = await conn.get_raw_connection()
            await raw.driver_connection.execute(path.read_text(encoding="utf-8"))
            await conn.execute(
                text("INSERT INTO schema_migrations (filename) VALUES (:f)"), {"f": path.name}
            )
            applied.append(path.name)
    return applied


async def ping() -> bool:
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("db ping failed: %s", exc)
        return False
