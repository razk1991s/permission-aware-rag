"""כלי הסוכן.

עיקרון מרכזי (ADR 0002): הזהות וההרשאות מגיעות מה-state ולא מהארגומנטים
שהמודל מייצר. `user_id` ו-`allowed_doc_ids` אינם קיימים בסכמות הכלים —
המודל לא יכול להעביר אותם כי הוא לא רואה אותם.

כלי שנחסם מחזיר ToolResult עם status='blocked' ולא זורק חריגה, כדי
שהסוכן יידע לדווח על החסימה נכון במקום להתעלם ממנה.
"""

from __future__ import annotations

import ast
import logging
import operator
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.agent.query_catalog import (
    InvalidQueryParams,
    QueryNotFound,
    catalog_for_prompt,
    get_spec,
    validate_params,
)
from app.config import settings
from app.core.deps import audit
from app.retrieval.pipeline import retrieve

log = logging.getLogger(__name__)


@dataclass
class ToolResult:
    tool: str
    status: str                       # ok | blocked | failed | empty
    data: Any = None
    message: str | None = None
    citations: list[dict] = field(default_factory=list)
    latency_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "status": self.status,
            "message": self.message,
            "data": self.data,
            "latency_ms": self.latency_ms,
        }


@dataclass
class ToolContext:
    """כל מה שהכלי צריך ושהמודל לא שולט בו."""

    conn: AsyncConnection
    user_id: int
    roles: frozenset[str]
    allowed_doc_ids: frozenset[int]


# ------------------------------------------------------------------ עוטף
def requires_roles(*roles: str):
    """אכיפה בעוטף הכלי, לא בפרומפט.

    זו השכבה שמסמך מורעל לא יכול לעקוף: גם אם המודל שוכנע להפעיל את
    הכלי, הבדיקה רצה לפני הביצוע ומחוץ להשפעת הטקסט.
    """

    def deco(fn):
        async def wrapper(ctx: ToolContext, **kwargs) -> ToolResult:
            if not ctx.roles.intersection(roles):
                await audit(
                    ctx.conn,
                    actor_id=ctx.user_id,
                    actor_type="agent",
                    action=f"tool:{fn.__name__}",
                    outcome="blocked",
                    detail={"required": list(roles), "held": sorted(ctx.roles)},
                )
                return ToolResult(
                    fn.__name__, "blocked", message="למשתמש אין הרשאה להפעיל כלי זה"
                )
            started = time.perf_counter()
            result = await fn(ctx, **kwargs)
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            return result

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return deco


# ------------------------------------------------------------------ כלי 1
async def search_documents(
    ctx: ToolContext, *, query: str, domain: str | None = None, top_k: int | None = None
) -> ToolResult:
    """חיפוש בנהלים הארגוניים. מחזיר קטעים עם ציטוט מקור."""
    started = time.perf_counter()
    result = await retrieve(
        ctx.conn,
        user_id=ctx.user_id,          # ⬅ מה-state, לא מהמודל
        question=query,
        domain=domain,
        top_k=top_k or settings.retrieval_top_k,
        use_understanding=False,      # הסוכן כבר ניסח; אין צורך בסבב נוסף
    )
    latency = int((time.perf_counter() - started) * 1000)

    if not result.candidates:
        return ToolResult("search_documents", "empty",
                          message="לא נמצאו קטעים מורשים התואמים לשאילתה", latency_ms=latency)

    return ToolResult(
        "search_documents",
        "ok",
        data=[
            {
                "chunk_id": c.chunk_id,
                "doc_id": c.doc_id,
                "citation": c.citation,
                "section_path": c.section_path,
                "score": round(c.best_score, 4),
                "content": c.content,
            }
            for c in result.candidates
        ],
        citations=[{"doc_id": c.doc_id, "citation": c.citation} for c in result.candidates],
        latency_ms=latency,
    )


# ------------------------------------------------------------------ כלי 2
@requires_roles("finance", "support", "admin")
async def query_database(ctx: ToolContext, *, query_name: str, params: dict | None = None) -> ToolResult:
    """מריץ שאילתה מתוך הקטלוג. אינו מקבל SQL — רק שם ופרמטרים."""
    try:
        spec = get_spec(query_name)
    except QueryNotFound as exc:
        return ToolResult("query_database", "failed", message=str(exc))

    if not ctx.roles.intersection(spec.required_roles):
        await audit(
            ctx.conn,
            actor_id=ctx.user_id,
            actor_type="agent",
            action=f"query:{query_name}",
            outcome="blocked",
            detail={"required": list(spec.required_roles)},
        )
        return ToolResult("query_database", "blocked",
                          message=f"אין הרשאה להריץ את השאילתה {query_name}")

    try:
        bound = validate_params(spec, params or {})
    except InvalidQueryParams as exc:
        return ToolResult("query_database", "failed", message=str(exc))

    if "limit" in spec.params:
        bound["limit"] = min(int(bound["limit"]), spec.max_rows)

    rows = (await ctx.conn.execute(text(spec.sql), bound)).all()
    await audit(
        ctx.conn,
        actor_id=ctx.user_id,
        actor_type="agent",
        action=f"query:{query_name}",
        outcome="allowed",
        detail={"params": bound, "rows": len(rows)},
    )

    data = [dict(r._mapping) for r in rows]
    for row in data:  # Decimal ו-datetime אינם ניתנים לסריאליזציה ישירה
        for k, v in list(row.items()):
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
            elif hasattr(v, "quantize"):
                row[k] = float(v)

    return ToolResult(
        "query_database",
        "ok" if data else "empty",
        data=data,
        message=None if data else "השאילתה רצה בהצלחה ולא החזירה שורות",
    )


# ------------------------------------------------------------------ כלי 3
_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 8 or abs(left) > 1e6):
            raise ValueError("חזקה גדולה מדי")
        return _SAFE_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("ביטוי לא מורשה")


async def calculate(ctx: ToolContext, *, expression: str) -> ToolResult:
    """חישוב אריתמטי. עובד על עץ תחביר מוגבל, לא על eval."""
    if len(expression) > 200:
        return ToolResult("calculate", "failed", message="הביטוי ארוך מדי")
    try:
        value = _safe_eval(ast.parse(expression, mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError) as exc:
        return ToolResult("calculate", "failed", message=f"לא ניתן לחשב: {exc}")
    return ToolResult("calculate", "ok", data={"expression": expression, "result": value})


# ------------------------------------------------------------------ כלי 4
_ALLOWED_ENDPOINTS = {
    "customer_profile": "/internal/customers/{id}",
    "card_status": "/internal/cards/{id}/status",
}


@requires_roles("finance", "support", "admin")
async def call_internal_api(ctx: ToolContext, *, endpoint: str, params: dict | None = None) -> ToolResult:
    """קורא ל-API פנימי מתוך רשימה מאושרת. mock בשלב זה."""
    if endpoint not in _ALLOWED_ENDPOINTS:
        return ToolResult(
            "call_internal_api",
            "blocked",
            message=f"נקודת קצה שאינה ברשימה המאושרת. מותר: {sorted(_ALLOWED_ENDPOINTS)}",
        )
    return ToolResult(
        "call_internal_api",
        "ok",
        data={"endpoint": endpoint, "params": params or {}, "mock": True},
        message="תשובת mock — האינטגרציה האמיתית מחוץ לגבולות הפרויקט",
    )


# ------------------------------------------------------------------ כלי 5
async def escalate_to_human(ctx: ToolContext, *, reason: str) -> ToolResult:
    """מסמן במפורש שהמערכת אינה יודעת. תמיד עדיף על ניחוש."""
    await audit(
        ctx.conn,
        actor_id=ctx.user_id,
        actor_type="agent",
        action="escalate_to_human",
        outcome="allowed",
        detail={"reason": reason[:500]},
    )
    return ToolResult("escalate_to_human", "ok", data={"reason": reason},
                      message="הפנייה סומנה להעברה לגורם אנושי")


# ------------------------------------------------------------------ רישום
TOOLS = {
    "search_documents": search_documents,
    "query_database": query_database,
    "calculate": calculate,
    "call_internal_api": call_internal_api,
    "escalate_to_human": escalate_to_human,
}

# סכמות הכלים כפי שהמודל רואה אותן.
# שים לב למה שחסר: user_id, roles, allowed_doc_ids. זה מכוון.
TOOL_SCHEMAS = [
    {
        "name": "search_documents",
        "description": "חיפוש בנהלים הארגוניים. מחזיר קטעים עם ציטוט מקור.",
        "parameters": {
            "query": "str — מה לחפש, בעברית",
            "domain": "str? — finance | hr | public",
            "top_k": "int? — כמה קטעים להחזיר",
        },
    },
    {
        "name": "query_database",
        "description": "מריץ שאילתה מוכנה מהקטלוג על הנתונים התפעוליים.",
        "parameters": {"query_name": "str — שם מהקטלוג", "params": "object — פרמטרים"},
    },
    {
        "name": "calculate",
        "description": "חישוב אריתמטי פשוט.",
        "parameters": {"expression": "str — למשל '4200 * 0.028'"},
    },
    {
        "name": "escalate_to_human",
        "description": "כשאין מספיק מידע כדי לענות.",
        "parameters": {"reason": "str"},
    },
]


def tool_schemas_for(roles: set[str]) -> list[dict]:
    """מציג למודל רק את מה שמותר לו — כולל קטלוג השאילתות המסונן."""
    schemas = [dict(s) for s in TOOL_SCHEMAS]
    for s in schemas:
        if s["name"] == "query_database":
            s["available_queries"] = catalog_for_prompt(roles)
    return schemas
