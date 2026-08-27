"""Agent state passed between graph nodes.

Important distinction: the fields below are **application state**, not model
context. They never enter the prompt. This is both sound engineering and a
security layer: a poisoned document cannot change what is outside its context.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict


def _append(existing: list | None, new: list | None) -> list:
    return (existing or []) + (new or [])


class AgentState(TypedDict, total=False):
    # --- Input ---
    question: str
    session_id: str | None
    thread_id: str

    # --- Identity and authorization (never in the prompt) ---
    user_id: int
    roles: list[str]
    allowed_doc_ids: list[int]

    # --- Intermediate results ---
    intent: str
    domain_hint: str | None
    queries: list[str]
    retrieval: Any                      # RetrievalResult
    tool_results: Annotated[list[dict], _append]
    policy_facts: dict[str, Any]        # Thresholds extracted from documents

    # --- Pending action ---
    pending_action: dict | None
    approval: dict | None

    # --- Output ---
    answer: str
    citations: list[dict]
    refused: bool
    refusal_reason: str | None
    groundedness: float | None
    hallucination_flag: bool
    injection_detected: bool

    # --- Control ---
    tool_calls: int
    stop_reason: str
    stage_latencies: dict[str, int]
    started_at: float


def new_state(
    *,
    question: str,
    user_id: int,
    roles: set[str] | frozenset[str],
    allowed_doc_ids: set[int] | frozenset[int],
    thread_id: str,
    session_id: str | None = None,
) -> AgentState:
    import time

    return AgentState(
        question=question,
        session_id=session_id,
        thread_id=thread_id,
        user_id=user_id,
        roles=sorted(roles),
        allowed_doc_ids=sorted(allowed_doc_ids),
        queries=[],
        tool_results=[],
        policy_facts={},
        pending_action=None,
        approval=None,
        refused=False,
        hallucination_flag=False,
        injection_detected=False,
        tool_calls=0,
        stop_reason="running",
        stage_latencies={},
        started_at=time.perf_counter(),
    )
