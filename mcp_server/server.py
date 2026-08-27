"""שרת MCP — חשיפת הכלים של המערכת ללקוחות חיצוניים.

הרעיון: אותו ידע ארגוני, אותן הרשאות, אבל נגיש מכל כלי שתומך בפרוטוקול —
Claude Desktop, Cursor, או כל לקוח MCP אחר.

**הנקודה הקריטית:** אותו מסלול authz בדיוק. הזהות נגזרת מטוקן שהלקוח
מציג, ו-`allowed_doc_ids` מחושב מהמסד — בדיוק כמו ב-API. אם ה-MCP היה
עוקף את ה-ACL, היינו בונים דלת אחורית לכל מה שהשקענו בו.

הרצה:
    python -m mcp_server.server                      # stdio
    MCP_TOKEN=<jwt> python -m mcp_server.server

חיבור מ-Claude Desktop (claude_desktop_config.json):
    {"mcpServers": {"meridian": {
        "command": "python", "args": ["-m", "mcp_server.server"],
        "cwd": "/path/to/enterprise-rag",
        "env": {"MCP_TOKEN": "<jwt>", "DATABASE_URL": "..."}}}}
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.query_catalog import get_spec, validate_params  # noqa: E402
from app.core.security import decode_access_token  # noqa: E402
from app.db import dispose_engine, get_engine  # noqa: E402
from app.retrieval.pipeline import retrieve  # noqa: E402

log = logging.getLogger("mcp.meridian")


class NotAuthenticated(RuntimeError):
    pass


def identity_from_env() -> tuple[int, set[str]]:
    """הזהות מגיעה מהטוקן בלבד — לא מפרמטר של כלי ולא ממשתנה חופשי."""
    token = os.getenv("MCP_TOKEN")
    if not token:
        raise NotAuthenticated(
            "לא הוגדר MCP_TOKEN. הפק טוקן דרך POST /api/auth/login והגדר אותו בסביבה."
        )
    payload = decode_access_token(token)
    return int(payload["sub"]), set(payload.get("roles") or [])


def build_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("meridian-knowledge")

    @mcp.tool()
    async def search_company_docs(query: str, domain: str | None = None, top_k: int = 5) -> dict:
        """חיפוש בנהלים הארגוניים של Meridian Credit.

        מחזיר קטעים רלוונטיים עם ציטוט מקור. ההרשאות נגזרות מהטוקן של
        המתחבר — לא ניתן לבקש מסמכים שאינם מורשים.
        """
        user_id, _roles = identity_from_env()
        async with get_engine().begin() as conn:
            result = await retrieve(
                conn,
                user_id=user_id,
                question=query,
                domain=domain,
                top_k=min(max(top_k, 1), 10),
                use_understanding=False,
            )
        return {
            "query": query,
            "results": [
                {
                    "citation": c.citation,
                    "doc_id": c.doc_id,
                    "section": c.section_path,
                    "score": round(c.best_score, 4),
                    "content": c.content,
                }
                for c in result.candidates
            ],
        }

    @mcp.tool()
    async def run_catalog_query(query_name: str, params: dict | None = None) -> dict:
        """מריץ שאילתה מתוך קטלוג השאילתות המאושר.

        אינו מקבל SQL. השאילתות הזמינות: open_refunds_older_than,
        count_open_refunds_older_than, refund_status_by_id, refunds_by_status.
        """
        user_id, roles = identity_from_env()
        spec = get_spec(query_name)
        if not roles.intersection(spec.required_roles):
            return {"status": "blocked", "message": f"אין הרשאה להריץ {query_name}"}
        bound = validate_params(spec, params or {})

        from sqlalchemy import text

        async with get_engine().begin() as conn:
            rows = (await conn.execute(text(spec.sql), bound)).all()
        data = []
        for r in rows:
            row = dict(r._mapping)
            for k, v in list(row.items()):
                if hasattr(v, "isoformat"):
                    row[k] = v.isoformat()
                elif hasattr(v, "quantize"):
                    row[k] = float(v)
            data.append(row)
        return {"status": "ok", "rows": data}

    @mcp.tool()
    async def whoami() -> dict:
        """מציג את הזהות וההרשאות שהטוקן הנוכחי מקנה."""
        user_id, roles = identity_from_env()
        from app.core.deps import resolve_allowed_doc_ids

        async with get_engine().begin() as conn:
            allowed = await resolve_allowed_doc_ids(conn, user_id)
        return {"user_id": user_id, "roles": sorted(roles), "allowed_documents": len(allowed)}

    return mcp


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    try:
        build_server().run()
    finally:
        with_loop = asyncio.new_event_loop()
        with_loop.run_until_complete(dispose_engine())
        with_loop.close()


if __name__ == "__main__":
    main()
