"""‎/chat — הרצת הסוכן ושמירת הטרייס.

הטרייס אינו לוג שנוסף בדיעבד: כל צומת בגרף מוסיף שדות ל-state, והשמירה
כאן היא פשוט כתיבה של אותו state. זו הסיבה שהתצפיתיות בפרויקט הזה
מלאה — היא תוצר לוואי של הארכיטקטורה.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.agent.graph import build_graph
from app.agent.state import new_state
from app.core.deps import ConnDep, UserDep
from app.llm.gateway import get_gateway
from app.retrieval.pipeline import retrieve

log = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


class CitationOut(BaseModel):
    marker: str
    doc_id: str
    title: str
    section_path: str | None = None
    page_number: int | None = None
    chunk_id: int
    score: float


class ChatResponse(BaseModel):
    answer: str
    citations: list[CitationOut] = []
    refused: bool = False
    refusal_reason: str | None = None
    intent: str | None = None
    tools_called: list[dict] = []
    groundedness: float | None = None
    trace_uuid: str
    latency_ms: int
    stop_reason: str


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, user: UserDep, conn: ConnDep) -> ChatResponse:
    started = time.perf_counter()
    trace_uuid = str(uuid.uuid4())
    thread_id = body.session_id or str(uuid.uuid4())

    state = new_state(
        question=body.question,
        user_id=user.id,
        roles=user.roles,
        allowed_doc_ids=user.allowed_doc_ids,
        thread_id=thread_id,
        session_id=body.session_id,
    )

    graph = build_graph(conn, get_gateway())
    final = await graph.ainvoke(state)
    latency = int((time.perf_counter() - started) * 1000)

    retrieval = final.get("retrieval")
    retrieved_debug = []
    if retrieval is not None:
        for c in retrieval.all_candidates[:30]:
            retrieved_debug.append(
                {
                    "chunk_id": c.chunk_id,
                    "doc_id": c.doc_id,
                    "citation": c.citation,
                    "vector_score": round(c.vector_score, 4) if c.vector_score else None,
                    "bm25_score": round(c.bm25_score, 4) if c.bm25_score else None,
                    "rrf": round(c.rrf_score, 5),
                    "rerank_score": round(c.rerank_score, 4) if c.rerank_score is not None else None,
                    "rerank_delta": c.rerank_delta,
                }
            )

    await _save_trace(
        conn,
        trace_uuid=trace_uuid,
        user_id=user.id,
        session_id=body.session_id,
        state=final,
        retrieved=retrieved_debug,
        latency_ms=latency,
    )

    return ChatResponse(
        answer=final.get("answer", ""),
        citations=[CitationOut(**c) for c in (final.get("citations") or [])],
        refused=bool(final.get("refused")),
        refusal_reason=final.get("refusal_reason"),
        intent=final.get("intent"),
        tools_called=final.get("tool_results") or [],
        groundedness=final.get("groundedness"),
        trace_uuid=trace_uuid,
        latency_ms=latency,
        stop_reason=final.get("stop_reason", "completed"),
    )


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=30)
    hybrid: bool = True
    rerank: bool = True


@router.post("/search")
async def raw_search(body: SearchRequest, user: UserDep, conn: ConnDep) -> dict:
    """שליפה גולמית בלי מודל — לדיבאג ולהערכה של שכבת השליפה בלבד."""
    result = await retrieve(
        conn,
        user_id=user.id,
        question=body.query,
        top_k=body.top_k,
        hybrid=body.hybrid,
        use_rerank=body.rerank,
        use_understanding=False,
    )
    return {
        "query": body.query,
        "rerank_model": result.rerank_model,
        "candidates": [
            {
                "chunk_id": c.chunk_id,
                "doc_id": c.doc_id,
                "citation": c.citation,
                "section_path": c.section_path,
                "vector_score": c.vector_score,
                "bm25_score": c.bm25_score,
                "rrf": round(c.rrf_score, 5),
                "rerank_score": c.rerank_score,
                "rerank_delta": c.rerank_delta,
                "content": c.content[:400],
            }
            for c in result.candidates
        ],
        "stage_latencies": result.stage_latencies,
    }


async def _save_trace(
    conn, *, trace_uuid, user_id, session_id, state, retrieved, latency_ms
) -> None:
    usage = get_gateway().usage()
    await conn.execute(
        text(
            """
            INSERT INTO traces
                (trace_uuid, user_id, session_id, question, rewritten_queries, route,
                 tools_called, retrieved_chunks, final_context_ids, answer, citations,
                 groundedness, hallucination_flag, refused, stop_reason,
                 prompt_tokens, completion_tokens, estimated_cost, latency_ms, stage_latencies)
            VALUES
                (CAST(:trace_uuid AS uuid), :user_id, CAST(:session_id AS uuid), :question,
                 CAST(:queries AS jsonb), :route, CAST(:tools AS jsonb), CAST(:retrieved AS jsonb),
                 :context_ids, :answer, CAST(:citations AS jsonb), :groundedness,
                 :hallucination, :refused, :stop_reason,
                 :prompt_tokens, :completion_tokens, :cost, :latency_ms,
                 CAST(:stage_latencies AS jsonb))
            """
        ),
        {
            "trace_uuid": trace_uuid,
            "user_id": user_id,
            "session_id": session_id,
            "question": state.get("question", ""),
            "queries": json.dumps(state.get("queries") or [], ensure_ascii=False),
            "route": state.get("intent"),
            "tools": json.dumps(state.get("tool_results") or [], ensure_ascii=False, default=str),
            "retrieved": json.dumps(retrieved, ensure_ascii=False),
            "context_ids": [c["chunk_id"] for c in (state.get("citations") or [])],
            "answer": state.get("answer"),
            "citations": json.dumps(state.get("citations") or [], ensure_ascii=False),
            "groundedness": state.get("groundedness"),
            "hallucination": bool(state.get("hallucination_flag")),
            "refused": bool(state.get("refused")),
            "stop_reason": state.get("stop_reason", "completed"),
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "cost": usage["estimated_cost"],
            "latency_ms": latency_ms,
            "stage_latencies": json.dumps(state.get("stage_latencies") or {}, ensure_ascii=False),
        },
    )
