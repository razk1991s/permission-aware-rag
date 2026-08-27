"""Agent tools.

Core principle (ADR 0002): identity and authorization come from state, not
model-generated arguments. `user_id` and `allowed_doc_ids` are absent from
tool schemas, so the model cannot provide them.

A blocked tool returns a ToolResult with status='blocked' rather than raising,
so the agent can report the denial instead of silently ignoring it.
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
    """Everything the tool needs and the model must not control."""

    conn: AsyncConnection
    user_id: int
    roles: frozenset[str]
    allowed_doc_ids: frozenset[int]


# ------------------------------------------------------------------ Wrapper
def requires_roles(*roles: str):
    """Enforce authorization at the tool boundary, not in the prompt.

    This layer cannot be bypassed by a poisoned document: even if the model is
    persuaded to invoke a tool, the check runs before execution and outside text influence.
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
                    fn.__name__, "blocked", message="The user is not authorized to use this tool"
                )
            started = time.perf_counter()
            result = await fn(ctx, **kwargs)
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            return result

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return deco


# ------------------------------------------------------------------ Tool 1
async def search_documents(
    ctx: ToolContext, *, query: str, domain: str | None = None, top_k: int | None = None
) -> ToolResult:
    """Search organizational procedures and return chunks with source citations."""
    started = time.perf_counter()
    result = await retrieve(
        ctx.conn,
        user_id=ctx.user_id,          # From state, not from the model.
        question=query,
        domain=domain,
        top_k=top_k or settings.retrieval_top_k,
        use_understanding=False,      # The agent already rewrote the query.
    )
    latency = int((time.perf_counter() - started) * 1000)

    if not result.candidates:
        return ToolResult("search_documents", "empty",
                          message="No authorized chunks match the query", latency_ms=latency)

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


# ------------------------------------------------------------------ Tool 2
@requires_roles("finance", "support", "admin")
async def query_database(ctx: ToolContext, *, query_name: str, params: dict | None = None) -> ToolResult:
    """Run a catalog query; accepts only a name and parameters, never SQL."""
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
                  message=f"Not authorized to run query {query_name}")

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
    for row in data:  # Decimal and datetime are not directly serializable.
        for k, v in list(row.items()):
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
            elif hasattr(v, "quantize"):
                row[k] = float(v)

    return ToolResult(
        "query_database",
        "ok" if data else "empty",
        data=data,
        message=None if data else "Query completed successfully but returned no rows",
    )


# ------------------------------------------------------------------ Tool 3
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
            raise ValueError("Exponent is too large")
        return _SAFE_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("Expression is not allowed")


async def calculate(ctx: ToolContext, *, expression: str) -> ToolResult:
    """Perform arithmetic using a restricted syntax tree, never eval."""
    if len(expression) > 200:
        return ToolResult("calculate", "failed", message="Expression is too long")
    try:
        value = _safe_eval(ast.parse(expression, mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError) as exc:
        return ToolResult("calculate", "failed", message=f"Could not calculate: {exc}")
    return ToolResult("calculate", "ok", data={"expression": expression, "result": value})


# ------------------------------------------------------------------ Tool 4
_ALLOWED_ENDPOINTS = {
    "customer_profile": "/internal/customers/{id}",
    "card_status": "/internal/cards/{id}/status",
}


@requires_roles("finance", "support", "admin")
async def call_internal_api(ctx: ToolContext, *, endpoint: str, params: dict | None = None) -> ToolResult:
    """Call an internal API from an approved list; currently a mock."""
    if endpoint not in _ALLOWED_ENDPOINTS:
        return ToolResult(
            "call_internal_api",
            "blocked",
            message=f"Endpoint is not approved. Allowed: {sorted(_ALLOWED_ENDPOINTS)}",
        )
    return ToolResult(
        "call_internal_api",
        "ok",
        data={"endpoint": endpoint, "params": params or {}, "mock": True},
        message="Mock response; real integration is outside project scope",
    )


# ------------------------------------------------------------------ Tool 5
async def escalate_to_human(ctx: ToolContext, *, reason: str) -> ToolResult:
    """Explicitly mark that the system does not know; always prefer this to guessing."""
    await audit(
        ctx.conn,
        actor_id=ctx.user_id,
        actor_type="agent",
        action="escalate_to_human",
        outcome="allowed",
        detail={"reason": reason[:500]},
    )
    return ToolResult("escalate_to_human", "ok", data={"reason": reason},
                      message="Request marked for human review")


# ------------------------------------------------------------------ Registration
TOOLS = {
    "search_documents": search_documents,
    "query_database": query_database,
    "calculate": calculate,
    "call_internal_api": call_internal_api,
    "escalate_to_human": escalate_to_human,
}

# Tool schemas as seen by the model.
# Notice what is absent: user_id, roles, and allowed_doc_ids. This is intentional.
TOOL_SCHEMAS = [
    {
        "name": "search_documents",
        "description": "Search organizational procedures and return source citations.",
        "parameters": {
            "query": "str - query text",
            "domain": "str? - finance | hr | public",
            "top_k": "int? - number of chunks to return",
        },
    },
    {
        "name": "query_database",
        "description": "Run a prepared catalog query against operational data.",
        "parameters": {"query_name": "str - catalog name", "params": "object - parameters"},
    },
    {
        "name": "calculate",
        "description": "Perform simple arithmetic.",
        "parameters": {"expression": "str - for example '4200 * 0.028'"},
    },
    {
        "name": "escalate_to_human",
        "description": "Use when there is not enough information to answer.",
        "parameters": {"reason": "str"},
    },
]


def tool_schemas_for(roles: set[str]) -> list[dict]:
    """Show the model only what it may use, including the filtered query catalog."""
    schemas = [dict(s) for s in TOOL_SCHEMAS]
    for s in schemas:
        if s["name"] == "query_database":
            s["available_queries"] = catalog_for_prompt(roles)
    return schemas
