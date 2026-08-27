"""Service entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import actions, auth, chat, documents, traces, ui
from app.config import settings
from app.db import dispose_engine, get_engine, ping, run_migrations
from app.llm.gateway import get_gateway

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
)
log = logging.getLogger("meridian")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.is_dev:
        if settings.jwt_secret == "dev-only-change-me":
            raise RuntimeError("JWT_SECRET must be configured outside dev")
        if settings.llm_provider == "stub" or settings.embedding_provider == "stub":
            raise RuntimeError("The stub provider is not allowed outside development")

    applied = await run_migrations()
    if applied:
        log.info("applied migrations: %s", ", ".join(applied))
    log.info("provider=%s generation=%s", settings.llm_provider, settings.generation_model)
    yield
    await dispose_engine()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Enterprise RAG knowledge platform: hybrid retrieval with reranking, "
        "SQL-enforced document access control, a restricted-tool agent, "
        "procedure-driven approval gates, and automated evaluation."
    ),
    lifespan=lifespan,
)

# --- CORS: development only, for ng serve only ---
# In production Angular is served from the same origin, so CORS is disabled.
# An open production origin list is exactly the kind of setting left by mistake.
if settings.is_dev:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# כל ה-API יושב תחת /api — והשורש שייך כולו ל-SPA.
#
# הגרסה הראשונה רשמה כל נתיב פעמיים, גם ישירות (/traces) וגם תחת
# /api, כדי שסקריפטים לא יצטרכו קידומת. זה נשבר: /traces ו-/documents
# הם גם נתיבי Angular, ולכן רענון דף על /traces/<uuid> נתפס על ידי
# ה-API והחזיר 401 JSON במקום את האפליקציה. מרחב שמות אחד לשני
# צרכנים שונים הוא באג שממתין לקרות — הקידומת היא הגבול.
for module in (auth, documents, chat, actions, traces, ui):
    app.include_router(module.router, prefix="/api")

health = APIRouter(tags=["health"])


@health.get("/health")
async def health_check() -> dict:
    """בדיקת בריאות אמיתית: מסד נתונים ומודלים, לא רק 'השרת חי'."""
    db_ok = await ping()
    llm_ok = await get_gateway().health()
    return {
        "status": "ok" if (db_ok and llm_ok) else "degraded",
        "database": "up" if db_ok else "down",
        "llm": "up" if llm_ok else "down",
        "provider": settings.llm_provider,
        "generation_model": settings.generation_model,
        "embedding_provider": settings.embedding_provider,
        "environment": settings.environment,
    }


@health.get("/stats")
async def stats() -> dict:
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM documents)                          AS documents,
                        (SELECT count(*) FROM documents WHERE status='active')    AS active_documents,
                        (SELECT count(*) FROM chunks)                             AS chunks,
                        (SELECT count(*) FROM chunks WHERE embedding IS NOT NULL) AS embedded_chunks,
                        (SELECT count(*) FROM document_acl)                       AS acl_rows,
                        (SELECT count(*) FROM users)                              AS users,
                        (SELECT count(*) FROM traces)                             AS traces,
                        (SELECT count(*) FROM agent_actions)                      AS actions
                    """
                )
            )
        ).first()
    return {**dict(row._mapping), "llm_usage": get_gateway().usage()}


# /health ו-/stats הם היוצאים מן הכלל: הם נרשמים גם בשורש, כי אין
# להם נתיב מקביל ב-Angular, ו-HEALTHCHECK של Docker ו-probes של
# אורקסטרטור מצפים למצוא אותם שם. העותק תחת /api הוא מה שהממשק קורא.
app.include_router(health)
app.include_router(health, prefix="/api", include_in_schema=False)

# חייב להיות אחרון: mount על "/" תופס כל בקשה שלא הותאמה לפניו.
ui.mount_spa(app)
