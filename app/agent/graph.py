"""גרף הסוכן — מכונת מצבים מפורשת עם LangGraph.

הזרימה:

    understand → route ─┬─ knowledge → retrieve ────────┐
                        ├─ data      → plan_tools ──────┤
                        ├─ hybrid    → retrieve →       │
                        │             extract_policy →  │
                        │             plan_tools ───────┤
                        └─ chitchat ────────────────────┤
                                                        ▼
                                                    generate → finalize

גבולות מפורשים (settings.max_tool_calls, max_wall_clock_seconds) נאכפים
בכל צומת שמפעיל כלי. לולאה אינה נגמרת בשקט: `stop_reason` הוא שדה חובה
בכל טרייס, ובחריגה הוא מקבל ערך שאפשר לחפש לפיו.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import AsyncConnection

from app.agent.state import AgentState
from app.agent.tools import TOOLS, ToolContext, tool_schemas_for
from app.config import settings
from app.llm.gateway import LLMGateway, get_gateway
from app.rag.answer import generate_answer
from app.retrieval.pipeline import retrieve, understand

log = logging.getLogger(__name__)

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string"},
                    "args": {"type": "object"},
                    "why": {"type": "string"},
                },
                "required": ["tool", "args"],
            },
        }
    },
    "required": ["calls"],
}

PLAN_PROMPT = """בחר אילו כלים להפעיל כדי לענות על השאלה. החזר JSON בלבד.

הכלים הזמינים לך:
{tools}

עובדות שכבר חולצו מהנהלים (השתמש בהן כפרמטרים, אל תמציא מספרים):
{facts}

הנחיות:
- הפעל לכל היותר {budget} כלים.
- ל-query_database השתמש רק בשמות שאילתה מהרשימה available_queries.
- אם חסר לך סף או מספר שאמור להגיע מנוהל — אל תנחש. השתמש ב-escalate_to_human.

השאלה: {question}"""

# ספים שאפשר לחלץ מנוהל, והשמות שהם מקבלים ב-policy_facts
_THRESHOLD_PATTERNS = [
    ("breach_days", re.compile(r"בתוך\s+(\d+)\s+ימי\s+עסקים")),
    ("escalation_days", re.compile(r"בתוך\s+(\d+)\s+יום\s+ממועד\s+האישור\s+תועבר")),
    ("compensation_days", re.compile(r"בתוך\s+(\d+)\s+יום\s+ממועד\s+האישור\s+תזכה")),
    ("compensation_amount", re.compile(r"פיצוי\s+אוטומטי\s+בסך\s+([\d,]+)")),
]


# ------------------------------------------------------------------ צמתים
def build_graph(conn: AsyncConnection, gateway: LLMGateway | None = None):
    gateway = gateway or get_gateway()

    def _budget_exceeded(state: AgentState) -> str | None:
        if state.get("tool_calls", 0) >= settings.max_tool_calls:
            return "tool_budget_exceeded"
        if time.perf_counter() - state.get("started_at", 0) > settings.max_wall_clock_seconds:
            return "wall_clock_exceeded"
        return None

    # ---------------------------------------------------------- understand
    async def node_understand(state: AgentState) -> dict:
        t0 = time.perf_counter()
        u = await understand(state["question"], gateway=gateway, user_id=state["user_id"])
        return {
            "intent": u.intent,
            "domain_hint": u.domain_hint,
            "queries": u.queries,
            "stage_latencies": {
                **state.get("stage_latencies", {}),
                "understanding": int((time.perf_counter() - t0) * 1000),
            },
        }

    # ---------------------------------------------------------- retrieve
    async def node_retrieve(state: AgentState) -> dict:
        t0 = time.perf_counter()
        result = await retrieve(
            conn,
            user_id=state["user_id"],
            question=state["question"],
            domain=state.get("domain_hint"),
            gateway=gateway,
            use_understanding=False,     # כבר בוצע בצומת understand
        )
        result.understanding.queries = state.get("queries") or [state["question"]]
        return {
            "retrieval": result,
            "stage_latencies": {
                **state.get("stage_latencies", {}),
                "retrieval": int((time.perf_counter() - t0) * 1000),
                **result.stage_latencies,
            },
        }

    # ---------------------------------------------------------- policy facts
    async def node_extract_policy(state: AgentState) -> dict:
        """מחלץ ספים מספריים מהקטעים שנשלפו.

        זה הצומת שהופך שאלה משולבת לאמיתית: הסף 14 מגיע מהמסמך, ומשם
        הוא נכנס כפרמטר לשאילתה — במקום להיות מקודד בקוד.
        """
        result = state.get("retrieval")
        facts: dict[str, Any] = dict(state.get("policy_facts") or {})
        if result is None:
            return {"policy_facts": facts}

        for cand in result.candidates:
            for key, pattern in _THRESHOLD_PATTERNS:
                if key in facts:
                    continue
                m = pattern.search(cand.content)
                if m:
                    facts[key] = {
                        "value": float(m.group(1).replace(",", "")),
                        "source": cand.citation,
                        "doc_id": cand.doc_id,
                        "chunk_id": cand.chunk_id,
                    }
        return {"policy_facts": facts}

    # ---------------------------------------------------------- plan tools
    async def node_plan_tools(state: AgentState) -> dict:
        stop = _budget_exceeded(state)
        if stop:
            return {"stop_reason": stop}

        remaining = settings.max_tool_calls - state.get("tool_calls", 0)
        roles = set(state.get("roles") or [])
        facts_summary = {
            k: v["value"] if isinstance(v, dict) else v
            for k, v in (state.get("policy_facts") or {}).items()
        }

        try:
            resp = await gateway.complete(
                task="routing",
                messages=[
                    {
                        "role": "user",
                        "content": PLAN_PROMPT.format(
                            tools=json.dumps(tool_schemas_for(roles), ensure_ascii=False, indent=2),
                            facts=json.dumps(facts_summary, ensure_ascii=False) or "{}",
                            budget=remaining,
                            question=state["question"],
                        ),
                    }
                ],
                user_id=state["user_id"],
                json_schema=PLAN_SCHEMA,
            )
            plan = json.loads(resp.text).get("calls") or []
        except Exception as exc:  # noqa: BLE001
            log.warning("tool planning failed: %s", exc)
            plan = []

        ctx = ToolContext(
            conn=conn,
            user_id=state["user_id"],
            roles=frozenset(roles),
            allowed_doc_ids=frozenset(state.get("allowed_doc_ids") or []),
        )

        results: list[dict] = []
        seen: dict[str, int] = {}
        calls_made = 0

        for call in plan[:remaining]:
            name = call.get("tool")
            fn = TOOLS.get(name)
            if fn is None:
                results.append(
                    {"tool": name, "status": "failed", "message": "כלי לא מוכר", "data": None}
                )
                continue

            signature = f"{name}:{json.dumps(call.get('args') or {}, sort_keys=True, ensure_ascii=False)}"
            seen[signature] = seen.get(signature, 0) + 1
            if seen[signature] > settings.max_same_tool_calls:
                results.append(
                    {"tool": name, "status": "blocked",
                     "message": "אותו כלי עם אותם פרמטרים הופעל יותר מדי פעמים", "data": None}
                )
                continue

            try:
                result = await fn(ctx, **(call.get("args") or {}))
            except TypeError as exc:
                results.append(
                    {"tool": name, "status": "failed", "message": f"ארגומנטים שגויים: {exc}",
                     "data": None}
                )
                continue
            except Exception as exc:  # noqa: BLE001
                log.exception("tool %s crashed", name)
                results.append(
                    {"tool": name, "status": "failed", "message": str(exc), "data": None}
                )
                continue

            calls_made += 1
            results.append(result.to_dict())

            if _budget_exceeded({**state, "tool_calls": state.get("tool_calls", 0) + calls_made}):
                break

        return {
            "tool_results": results,
            "tool_calls": state.get("tool_calls", 0) + calls_made,
        }

    # ---------------------------------------------------------- generate
    async def node_generate(state: AgentState) -> dict:
        result = state.get("retrieval")
        tool_results = state.get("tool_results") or []

        # --- אין שליפה, רק כלים: מרכיבים תשובה מהנתונים ---
        if result is None or not result.candidates:
            if tool_results:
                return _answer_from_tools(state, tool_results)
            return {
                "answer": "לא מצאתי מידע מספק במסמכים המורשים לך כדי לענות על השאלה.",
                "refused": True,
                "refusal_reason": "אין קטעים מורשים ואין תוצאות כלים",
                "citations": [],
                "stop_reason": "no_context",
            }

        answer = await generate_answer(result, user_id=state["user_id"], gateway=gateway)

        text = answer.text
        if tool_results and not answer.refused:
            text = f"{text}\n\n{_render_tool_results(tool_results)}"

        return {
            "answer": text,
            "citations": [c.__dict__ for c in answer.citations],
            "refused": answer.refused,
            "refusal_reason": answer.refusal_reason,
            "groundedness": answer.groundedness,
            "hallucination_flag": answer.hallucination_flag,
            "injection_detected": answer.injection_detected,
            "stage_latencies": {**state.get("stage_latencies", {}), **answer.stage_latencies},
            "stop_reason": answer.stop_reason,
        }

    # ---------------------------------------------------------- finalize
    async def node_finalize(state: AgentState) -> dict:
        if state.get("stop_reason") in {None, "running"}:
            return {"stop_reason": "completed"}
        return {}

    # ---------------------------------------------------------- ניתוב
    def route(state: AgentState) -> str:
        intent = state.get("intent", "knowledge")
        if intent == "data":
            return "plan_tools"
        if intent == "hybrid":
            return "retrieve_for_hybrid"
        if intent == "chitchat":
            return "generate"
        return "retrieve"

    builder = StateGraph(AgentState)
    builder.add_node("understand", node_understand)
    builder.add_node("retrieve", node_retrieve)
    builder.add_node("retrieve_for_hybrid", node_retrieve)
    builder.add_node("extract_policy", node_extract_policy)
    builder.add_node("plan_tools", node_plan_tools)
    builder.add_node("generate", node_generate)
    builder.add_node("finalize", node_finalize)

    builder.set_entry_point("understand")
    builder.add_conditional_edges(
        "understand",
        route,
        {
            "retrieve": "retrieve",
            "retrieve_for_hybrid": "retrieve_for_hybrid",
            "plan_tools": "plan_tools",
            "generate": "generate",
        },
    )
    builder.add_edge("retrieve", "generate")
    builder.add_edge("retrieve_for_hybrid", "extract_policy")
    builder.add_edge("extract_policy", "plan_tools")
    builder.add_edge("plan_tools", "generate")
    builder.add_edge("generate", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile()


# ------------------------------------------------------------------ עזר
def _render_tool_results(results: list[dict]) -> str:
    lines: list[str] = []
    for r in results:
        if r["status"] == "blocked":
            lines.append(f"⛔ {r['tool']}: {r['message']}")
        elif r["status"] == "failed":
            lines.append(f"⚠️ {r['tool']}: {r['message']}")
        elif r["status"] == "empty":
            lines.append(f"○ {r['tool']}: לא נמצאו תוצאות")
        elif r["tool"] == "query_database" and isinstance(r["data"], list):
            lines.append(f"נתונים ({len(r['data'])} שורות):")
            for row in r["data"][:10]:
                lines.append("  · " + " | ".join(f"{k}: {v}" for k, v in row.items()))
            if len(r["data"]) > 10:
                lines.append(f"  … ועוד {len(r['data']) - 10} שורות")
        elif r["tool"] == "calculate" and isinstance(r["data"], dict):
            lines.append(f"חישוב: {r['data']['expression']} = {r['data']['result']}")
    return "\n".join(lines)


def _answer_from_tools(state: AgentState, tool_results: list[dict]) -> dict:
    blocked = [r for r in tool_results if r["status"] == "blocked"]
    if blocked and not any(r["status"] == "ok" for r in tool_results):
        return {
            "answer": "הפעולה נחסמה: " + "; ".join(r["message"] or "" for r in blocked),
            "refused": True,
            "refusal_reason": "כל הכלים נחסמו בהרשאות",
            "citations": [],
            "stop_reason": "blocked",
        }
    rendered = _render_tool_results(tool_results)
    return {
        "answer": rendered or "לא התקבלו תוצאות.",
        "refused": not rendered,
        "citations": [],
        "stop_reason": "completed",
    }
