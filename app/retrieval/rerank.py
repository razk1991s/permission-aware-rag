"""Reranking with a cross-encoder.

Vector search encodes the question and chunk **separately** and compares
vectors: fast, but unable to see their interaction. The cross-encoder encodes
the pair **together**, making it more accurate and slower, so it runs on only
30 candidates rather than the entire corpus.

This component is expected to produce the largest metric improvement, so it is
measured before and after rather than assumed to help.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

from app.config import settings
from app.retrieval.search import Candidate

log = logging.getLogger(__name__)

_model = None


@dataclass
class RerankOutcome:
    candidates: list[Candidate]
    model: str
    applied: bool


def _load_cross_encoder():
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder

        log.info("loading reranker %s", settings.reranker_model)
        _model = CrossEncoder(settings.reranker_model, max_length=512)
    return _model


def _lexical_overlap_score(query: str, content: str) -> float:
    """Deterministic fallback score used when the real reranker is unavailable.

    It is weighted token overlap, not a replacement for a cross-encoder. It
    exists so tests and CI can run without a GPU.
    """
    q = set(re.findall(r"\w+", query.lower()))
    c = re.findall(r"\w+", content.lower())
    if not q or not c:
        return 0.0
    cset = set(c)
    overlap = len(q & cset) / len(q)
    density = sum(1 for t in c if t in q) / math.sqrt(len(c))
    return max(0.0, min(1.0, 0.75 * overlap + 0.25 * min(density, 1.0)))


def rerank(
    query: str,
    candidates: list[Candidate],
    *,
    top_k: int | None = None,
    enabled: bool | None = None,
) -> RerankOutcome:
    """Rerank and return top_k while preserving position changes.

    `rerank_delta` is the difference between the original and new positions.
    The trace viewer uses it to show that a chunk moved from position 2 to 1.
    """
    top_k = top_k or settings.retrieval_top_k
    use = settings.rerank_enabled if enabled is None else enabled

    if not candidates:
        return RerankOutcome([], "none", False)
    if not use:
        for c in candidates[:top_k]:
            c.rerank_delta = 0
        return RerankOutcome(candidates[:top_k], "disabled", False)

    before = {c.chunk_id: pos for pos, c in enumerate(candidates, start=1)}

    try:
        model = _load_cross_encoder()
        pairs = [(query, c.content) for c in candidates]
        raw = model.predict(pairs, show_progress_bar=False)
        # Cross-encoder scores are logits; sigmoid maps them to [0,1] so the
        # refusal threshold remains interpretable.
        scores = [1 / (1 + math.exp(-float(s))) for s in raw]
        model_name = settings.reranker_model
    except Exception as exc:  # noqa: BLE001
        log.warning("reranker unavailable (%s) — using lexical fallback", exc)
        scores = [_lexical_overlap_score(query, c.content) for c in candidates]
        model_name = "lexical-fallback"

    for cand, score in zip(candidates, scores, strict=True):
        cand.rerank_score = float(score)

    ordered = sorted(candidates, key=lambda c: c.rerank_score or 0.0, reverse=True)
    for pos, cand in enumerate(ordered, start=1):
        cand.rerank_delta = before[cand.chunk_id] - pos

    return RerankOutcome(ordered[:top_k], model_name, True)
