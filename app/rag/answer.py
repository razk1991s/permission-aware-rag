"""ייצור התשובה, ולידציה, וסירוב.

הסירוב הוא פיצ'ר ולא כישלון. מערכת שיודעת לומר "אין לי מידע מספק" היא
מערכת שאפשר להתקין בבנק. לכן `refused` הוא שדה מובנה בטרייס, והוא נמדד
בחבילת ההערכה כמו כל מדד אחר.
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

GROUNDEDNESS_PROMPT = """בדוק אם כל טענה בתשובה נתמכת בקטעים שסופקו.

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
    """קריאה שנייה, זולה, למודל **אחר** מזה שייצר את התשובה.

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
        return -1.0, []  # ‎-1 = לא נבדק. לא מתחזים לבדיקה שלא קרתה.


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

    # --- סף סירוב: לפני שמבזבזים קריאה למודל ---
    if retrieval.below_threshold:
        return Answer(
            text=REFUSAL_TEXT,
            refused=True,
            refusal_reason=(
                "אין קטעים מורשים" if not retrieval.candidates
                else f"ציון מוביל {retrieval.top_score:.2f} מתחת לסף"
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

        # --- ולידציה דטרמיניסטית של הפלט ---
        t0 = time.perf_counter()
        egress = verify_egress(resp.text, len(context))
        timings["validation"] = int((time.perf_counter() - t0) * 1000)

        if not egress.ok:
            last_reason = egress.reason
            log.warning("egress check failed (attempt %d): %s", attempt + 1, egress.reason)
            attempt += 1
            if attempt <= settings.max_answer_retries:
                # ניסיון אחד נוסף עם הקשר רחב יותר, ואז מסרבים.
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

        # --- בדיקת ביסוס ---
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
                    last_reason = f"ביסוס נמוך ({score:.2f}): {unsupported[:2]}"
                    continue

        if not citations and not egress.cited:
            # תשובה בלי ציטוט אחד היא תשובה שאי אפשר לאמת.
            return Answer(
                text=REFUSAL_TEXT,
                refused=True,
                refusal_reason="התשובה לא כללה ציטוט לאף מקור",
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
        refusal_reason=last_reason or "נכשלה הולידציה",
        stage_latencies=timings,
        retries=attempt,
        stop_reason="validation_failed",
    )
