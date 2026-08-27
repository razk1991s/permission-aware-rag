"""מצב הסוכן — מה עובר בין הצמתים בגרף.

הפרדה חשובה: השדות שמתחת לקו הם **מצב אפליקציה** ולא הקשר מודל. הם
לעולם לא נכנסים לפרומפט. זו גם החלטה הנדסית נכונה וגם שכבת אבטחה:
מה שלא נמצא בהקשר, מסמך מורעל לא יכול לנסות לשנות.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict


def _append(existing: list | None, new: list | None) -> list:
    return (existing or []) + (new or [])


class AgentState(TypedDict, total=False):
    # --- קלט ---
    question: str
    session_id: str | None
    thread_id: str

    # --- זהות והרשאות (לעולם לא בפרומפט) ---
    user_id: int
    roles: list[str]
    allowed_doc_ids: list[int]

    # --- תוצרי ביניים ---
    intent: str
    domain_hint: str | None
    queries: list[str]
    retrieval: Any                      # RetrievalResult
    tool_results: Annotated[list[dict], _append]
    policy_facts: dict[str, Any]        # ספים שחולצו ממסמכים

    # --- פעולה ממתינה ---
    pending_action: dict | None
    approval: dict | None

    # --- פלט ---
    answer: str
    citations: list[dict]
    refused: bool
    refusal_reason: str | None
    groundedness: float | None
    hallucination_flag: bool
    injection_detected: bool

    # --- בקרה ---
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
