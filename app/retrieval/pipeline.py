"""Full retrieval pipeline: authorization -> understanding -> dual retrieval -> RRF -> reranking -> context.

`retrieve` is the single retrieval entry point for /chat, the agent tool, and
the MCP server, so no path bypasses access control.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import settings
from app.ingestion.embedder import embed_query
from app.llm.gateway import LLMGateway, get_gateway
from app.retrieval.rerank import rerank
from app.retrieval.search import (
    Candidate,
    expand_with_neighbours,
    lexical_search,
    reciprocal_rank_fusion,
    vector_search,
)

log = logging.getLogger(__name__)

UNDERSTANDING_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["knowledge", "data", "hybrid", "chitchat"]},
        "domain_hint": {"type": "string"},
        "expanded_queries": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["intent", "expanded_queries"],
}

UNDERSTANDING_PROMPT = """נתח את שאלת המשתמש והחזר JSON בלבד.

intent:
  knowledge — התשובה נמצאת במסמכי נהלים
  data      — התשובה דורשת שאילתה על נתונים תפעוליים (לקוחות, עסקאות, בקשות זיכוי)
  hybrid    — נדרשים גם נוהל וגם נתונים
  chitchat  — לא שאלת תוכן

domain_hint: finance או hr או public, או מחרוזת ריקה אם לא ברור.

expanded_queries: עד {n} ניסוחים חלופיים לשאלה, בעברית, שמנסחים את אותה
כוונה במילים שסביר שיופיעו במסמך רשמי. השאלה המקורית תמיד ראשונה.

השאלה: {question}"""


@dataclass
class Understanding:
    intent: str = "knowledge"
    domain_hint: str | None = None
    queries: list[str] = field(default_factory=list)


@dataclass
class RetrievalResult:
    question: str
    understanding: Understanding
    candidates: list[Candidate]          # אחרי דירוג מחדש, top_k
    all_candidates: list[Candidate]      # לפני חיתוך — לטרייס
    rerank_model: str
    stage_latencies: dict[str, int] = field(default_factory=dict)
    allowed_documents: int = 0

    @property
    def top_score(self) -> float:
        return self.candidates[0].best_score if self.candidates else 0.0

    @property
    def below_threshold(self) -> bool:
        """Whether the top score is too low to answer."""
        if not self.candidates:
            return True
        threshold = (
            settings.min_rerank_score
            if self.rerank_model not in {"disabled", "none"}
            else settings.min_vector_score
        )
        return self.top_score < threshold


async def understand(
    question: str, *, gateway: LLMGateway, user_id: int | None = None
) -> Understanding:
    """Classify intent and expand queries. Fail soft if the model does not respond."""
    if not settings.multi_query_enabled:
        return Understanding(queries=[question])

    try:
        resp = await gateway.complete(
            task="understanding",
            messages=[
                {
                    "role": "user",
                    "content": UNDERSTANDING_PROMPT.format(
                        n=settings.max_expanded_queries, question=question
                    ),
                }
            ],
            user_id=user_id,
            json_schema=UNDERSTANDING_SCHEMA,
        )
        data = json.loads(resp.text)
    except Exception as exc:  # noqa: BLE001
        log.warning("understanding failed (%s) — falling back to the raw question", exc)
        return Understanding(queries=[question])

    queries = [question] + [
        q for q in (data.get("expanded_queries") or []) if isinstance(q, str) and q.strip()
    ]
    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique = [q for q in queries if not (q.lower() in seen or seen.add(q.lower()))]

    return Understanding(
        intent=data.get("intent", "knowledge"),
        domain_hint=(data.get("domain_hint") or None) or None,
        queries=unique[: settings.max_expanded_queries + 1],
    )


async def retrieve(
    conn: AsyncConnection,
    *,
    user_id: int,
    question: str,
    top_k: int | None = None,
    domain: str | None = None,
    gateway: LLMGateway | None = None,
    use_understanding: bool = True,
    hybrid: bool | None = None,
    use_rerank: bool | None = None,
    include_superseded: bool = False,
) -> RetrievalResult:
    gateway = gateway or get_gateway()
    hybrid = settings.hybrid_enabled if hybrid is None else hybrid
    timings: dict[str, int] = {}

    # --- 1. Question understanding ---
    t0 = time.perf_counter()
    if use_understanding:
        u = await understand(question, gateway=gateway, user_id=user_id)
    else:
        u = Understanding(queries=[question])
    timings["understanding"] = int((time.perf_counter() - t0) * 1000)
    effective_domain = domain or u.domain_hint

    # --- 2. Dual retrieval for every query ---
    t0 = time.perf_counter()
    ranked_lists: list[list[Candidate]] = []
    for query in u.queries:
        vec = embed_query(query)
        hits = await vector_search(
            conn,
            user_id=user_id,
            query_vector=vec,
            domain=effective_domain,
            include_superseded=include_superseded,
        )
        for h in hits:
            h.matched_queries.append(query)
        ranked_lists.append(hits)

        if hybrid:
            lex = await lexical_search(
                conn,
                user_id=user_id,
                query=query,
                domain=effective_domain,
                include_superseded=include_superseded,
            )
            for h in lex:
                h.matched_queries.append(query)
            ranked_lists.append(lex)
    timings["retrieval"] = int((time.perf_counter() - t0) * 1000)

    # --- 3. Fusion ---
    fused = reciprocal_rank_fusion(ranked_lists)

    if settings.neighbor_expansion and fused:
        fused = await expand_with_neighbours(conn, fused, user_id=user_id)

    # --- 4. Reranking ---
    t0 = time.perf_counter()
    outcome = rerank(question, fused, top_k=top_k, enabled=use_rerank)
    timings["rerank"] = int((time.perf_counter() - t0) * 1000)

    return RetrievalResult(
        question=question,
        understanding=u,
        candidates=outcome.candidates,
        all_candidates=fused,
        rerank_model=outcome.model,
        stage_latencies=timings,
    )


def build_context(candidates: list[Candidate], *, max_tokens: int | None = None) -> list[Candidate]:
    """Choose chunks for context within the token budget.

    Drop weaker chunks rather than cutting one in the middle. A truncated chunk
    is worse than a missing chunk because it appears complete when it is not.
    """
    budget = max_tokens or settings.max_context_tokens
    chosen: list[Candidate] = []
    used = 0
    seen_content: set[str] = set()

    for cand in candidates:
        fingerprint = cand.content[:120]
        if fingerprint in seen_content:      # Deduplicate overlapping chunks.
            continue
        tokens = max(1, len(cand.content) // 3)
        if used + tokens > budget and chosen:
            break
        chosen.append(cand)
        seen_content.add(fingerprint)
        used += tokens
    return chosen
