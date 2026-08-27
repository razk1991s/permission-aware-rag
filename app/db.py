"""Database connections and migration execution.

The project does not use an ORM: all queries are explicit SQL. This follows
ADR 0001 and ADR 0002; the core retrieval query enforces authorization and
ranking together, and an abstraction that hides it would be counterproductive.
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
    """FastAPI dependency: provide a transactional connection per request."""
    engine = get_engine()
    async with engine.begin() as conn:
        yield conn


# ---------------------------------------------------------------- Migrations
MIGRATIONS_DIR: Path = ROOT / "migrations"

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


async def run_migrations() -> list[str]:
    """Run each SQL file in migrations once, in filename order.

    This is sufficient for a project of this size. If schema changes require
    downgrades, replace this implementation with Alembic.
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
            # asyncpg sends each command as a prepared statement, which does
            # not support multiple commands in one string. Use the raw driver
            # connection and its simple query protocol for the complete file.
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
