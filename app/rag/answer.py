"""Answer generation, validation, and refusal.

Refusal is a feature, not a failure. A system that can say it lacks enough
information is a system that can be deployed in a bank. Therefore `refused`
is a first-class trace field and is measured like every other metric.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from app.config import settings
from app.llm.gateway import LLMGateway, get_gateway
from app.retrieval.pipeline import RetrievalResult, build_context
from app.retrieval.search import Candidate
from app.security.prompt_guard import (
    REFUSAL_TEXT,
    build_context_block,
    build_messages,
    verify_egress,
)

log = logging.getLogger(__name__)

GROUNDEDNESS_SCHEMA = {
    "type": "object",
    "properties": {
        "grounded": {"type": "boolean"},
        "score": {"type": "number"},
        "unsupported": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["grounded", "score"],
}

GROUNDEDNESS_PROMPT = """Check whether every claim in the answer is supported by the supplied chunks.

החזר JSON:
  grounded    — true אם כל טענה נתמכת
  score       — מספר בין 0 ל-1: איזה חלק מהטענות נתמך
  unsupported — רשימת הטענות שאינן נתמכות

הקטעים:
{context}

התשובה שיש לבדוק:
{answer}"""


@dataclass
class Citation:
    marker: str
    doc_id: str
    title: str
    section_path: str | None
    page_number: int | None
    chunk_id: int
    score: float


@dataclass
class Answer:
    text: str
    citations: list[Citation] = field(default_factory=list)
    refused: bool = False
    refusal_reason: str | None = None
    groundedness: float | None = None
    hallucination_flag: bool = False
    served_chunk_ids: list[int] = field(default_factory=list)
    stage_latencies: dict[str, int] = field(default_factory=dict)
    injection_detected: bool = False
    retries: int = 0
    stop_reason: str = "completed"


def _citations_from(context: list[Candidate], cited_markers: list[int]) -> list[Citation]:
    out: list[Citation] = []
    for n in cited_markers:
        if 1 <= n <= len(context):
            c = context[n - 1]
            out.append(
                Citation(
                    marker=f"S{n}",
                    doc_id=c.doc_id,
                    title=c.title,
                    section_path=c.section_path,
                    page_number=c.page_number,
                    chunk_id=c.chunk_id,
                    score=round(c.best_score, 4),
                )
            )
    return out


async def check_groundedness(
    answer_text: str, context_block: str, *, gateway: LLMGateway, user_id: int | None
) -> tuple[float, list[str]]:
    """Use a cheap second call to a model **different** from the answer model.

    מודל שמתבקש לשפוט את הפלט של עצמו נוטה לאשר אותו. הניתוב למשימת
    judge בשער המודלים הוא מה שמונע את זה.
    """
    try:
        resp = await gateway.complete(
            task="judge",
            messages=[
                {
                    "role": "user",
                    "content": GROUNDEDNESS_PROMPT.format(
                        context=context_block[:6000], answer=answer_text
                    ),
                }
            ],
            user_id=user_id,
            json_schema=GROUNDEDNESS_SCHEMA,
        )
        data = json.loads(resp.text)
        return float(data.get("score", 0.0)), list(data.get("unsupported") or [])
    except Exception as exc:  # noqa: BLE001
        log.warning("groundedness check failed: %s", exc)
        return -1.0, []  # -1 means unchecked; never pretend a check occurred.


async def generate_answer(
    retrieval: RetrievalResult,
    *,
    user_id: int | None = None,
    gateway: LLMGateway | None = None,
    verify_grounding: bool | None = None,
) -> Answer:
    gateway = gateway or get_gateway()
    verify = settings.groundedness_enabled if verify_grounding is None else verify_grounding
    timings = dict(retrieval.stage_latencies)

    # --- Refusal threshold: before spending a model call ---
    if retrieval.below_threshold:
        return Answer(
            text=REFUSAL_TEXT,
            refused=True,
            refusal_reason=(
                "No authorized chunks" if not retrieval.candidates
                else f"Top score {retrieval.top_score:.2f} is below the threshold"
            ),
            stage_latencies=timings,
            stop_reason="below_threshold",
        )

    context = build_context(retrieval.candidates)
    attempt = 0
    last_reason: str | None = None

    while attempt <= settings.max_answer_retries:
        block, guard = build_context_block(context)
        messages = build_messages(retrieval.question, block)

        t0 = time.perf_counter()
        resp = await gateway.complete(task="generation", messages=messages, user_id=user_id)
        timings["generation"] = int((time.perf_counter() - t0) * 1000)

        # --- Deterministic output validation ---
        t0 = time.perf_counter()
        egress = verify_egress(resp.text, len(context))
        timings["validation"] = int((time.perf_counter() - t0) * 1000)

        if not egress.ok:
            last_reason = egress.reason
            log.warning("egress check failed (attempt %d): %s", attempt + 1, egress.reason)
            attempt += 1
            if attempt <= settings.max_answer_retries:
                # Retry once with broader context, then refuse.
                context = build_context(retrieval.all_candidates[: settings.retrieval_top_k * 2])
                continue
            return Answer(
                text=REFUSAL_TEXT,
                refused=True,
                refusal_reason=last_reason,
                hallucination_flag=True,
                served_chunk_ids=[c.chunk_id for c in context],
                stage_latencies=timings,
                injection_detected=guard.triggered,
                retries=attempt,
                stop_reason="egress_violation",
            )

        citations = _citations_from(context, egress.cited)

        # --- Groundedness check ---
        groundedness: float | None = None
        hallucination = False
        if verify:
            t0 = time.perf_counter()
            score, unsupported = await check_groundedness(
                resp.text, block, gateway=gateway, user_id=user_id
            )
            timings["groundedness"] = int((time.perf_counter() - t0) * 1000)
            if score >= 0:
                groundedness = score
                hallucination = score < settings.min_groundedness
                if hallucination and attempt < settings.max_answer_retries:
                    attempt += 1
                    last_reason = f"Low groundedness ({score:.2f}): {unsupported[:2]}"
                    continue

        if not citations and not egress.cited:
            # An answer without citations cannot be verified.
            return Answer(
                text=REFUSAL_TEXT,
                refused=True,
                refusal_reason="The answer contained no citation to a source",
                served_chunk_ids=[c.chunk_id for c in context],
                stage_latencies=timings,
                injection_detected=guard.triggered,
                retries=attempt,
                stop_reason="no_citations",
            )

        return Answer(
            text=resp.text,
            citations=citations,
            groundedness=groundedness,
            hallucination_flag=hallucination,
            served_chunk_ids=[c.chunk_id for c in context],
            stage_latencies=timings,
            injection_detected=guard.triggered,
            retries=attempt,
        )

    return Answer(
        text=REFUSAL_TEXT,
        refused=True,
        refusal_reason=last_reason or "Validation failed",
        stage_latencies=timings,
        retries=attempt,
        stop_reason="validation_failed",
    )
