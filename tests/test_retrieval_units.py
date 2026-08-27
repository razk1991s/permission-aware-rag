"""בדיקות יחידה לשכבת השליפה — RRF, סף סירוב, ובניית הקשר.

אין כאן מסד נתונים ואין מודל. RRF הוא חשבון טהור, וזו בדיוק הסיבה
שאפשר וצריך לנעול אותו בבדיקה: שינוי בו משפיע על כל תוצאה במערכת.
"""

from __future__ import annotations

from app.retrieval.pipeline import build_context
from app.retrieval.rerank import _lexical_overlap_score, rerank
from app.retrieval.search import Candidate, reciprocal_rank_fusion


def cand(cid: int, *, vrank: int | None = None, brank: int | None = None, content: str = "טקסט") -> Candidate:
    return Candidate(
        chunk_id=cid, document_id=1, chunk_index=cid, doc_id="FIN-001", title="נוהל",
        section_path=None, page_number=None, content=content,
        vector_rank=vrank, bm25_rank=brank,
        vector_score=(1.0 / vrank if vrank else None),
        bm25_score=(1.0 / brank if brank else None),
    )


# ------------------------------------------------------------------ RRF
def test_rrf_rewards_appearing_in_both_lists():
    """מסמך שדורג בינוני בשתי הרשימות מנצח מסמך שדורג ראשון רק באחת."""
    vec = [cand(1, vrank=1), cand(2, vrank=3)]
    lex = [cand(3, brank=1), cand(2, brank=2)]
    fused = reciprocal_rank_fusion([vec, lex], k=60)
    assert fused[0].chunk_id == 2


def test_rrf_score_matches_the_formula():
    fused = reciprocal_rank_fusion([[cand(1, vrank=1)], [cand(1, brank=5)]], k=60)
    assert abs(fused[0].rrf_score - (1 / 61 + 1 / 65)) < 1e-9


def test_rrf_merges_scores_from_both_arms():
    fused = reciprocal_rank_fusion([[cand(7, vrank=2)], [cand(7, brank=4)]], k=60)
    merged = fused[0]
    assert merged.vector_rank == 2 and merged.bm25_rank == 4
    assert merged.vector_score is not None and merged.bm25_score is not None


def test_rrf_handles_one_empty_arm():
    """כששאילתה לקסיקלית לא מחזירה כלום, הדירוג הווקטורי נשמר כמות שהוא."""
    fused = reciprocal_rank_fusion([[cand(1, vrank=1), cand(2, vrank=2)], []], k=60)
    assert [c.chunk_id for c in fused] == [1, 2]


def test_rrf_is_order_independent_between_lists():
    a = reciprocal_rank_fusion([[cand(1, vrank=1)], [cand(2, brank=1)]], k=60)
    b = reciprocal_rank_fusion([[cand(2, brank=1)], [cand(1, vrank=1)]], k=60)
    assert {c.chunk_id for c in a} == {c.chunk_id for c in b}
    assert abs(a[0].rrf_score - b[0].rrf_score) < 1e-9


def test_rrf_respects_limit():
    lists = [[cand(i, vrank=i) for i in range(1, 20)]]
    assert len(reciprocal_rank_fusion(lists, limit=5)) == 5


# ------------------------------------------------------------------ rerank
def test_rerank_reports_position_delta():
    """‏rerank_delta הוא מה שמאפשר להראות בטרייס שהרירנקר שינה משהו."""
    cands = [
        cand(1, vrank=1, content="טקסט על חופשה שנתית וצבירת ימים"),
        cand(2, vrank=2, content="זיכוי שאושר יבוצע בתוך 14 ימי עסקים ממועד אישור הבקשה"),
    ]
    out = rerank("תוך כמה ימי עסקים מבצעים זיכוי", cands, top_k=2, enabled=True)
    assert out.applied
    assert out.candidates[0].chunk_id == 2
    assert out.candidates[0].rerank_delta == 1     # עלה ממקום 2 למקום 1
    assert out.candidates[1].rerank_delta == -1


def test_rerank_disabled_keeps_original_order():
    cands = [cand(1, vrank=1), cand(2, vrank=2)]
    out = rerank("שאלה", cands, top_k=2, enabled=False)
    assert [c.chunk_id for c in out.candidates] == [1, 2]
    assert not out.applied


def test_lexical_fallback_prefers_overlapping_text():
    q = "מועד ביצוע זיכוי"
    high = _lexical_overlap_score(q, "מועד ביצוע זיכוי הוא 14 ימי עסקים")
    low = _lexical_overlap_score(q, "מדיניות עבודה מהבית לדרגות 8 ומעלה")
    assert high > low


def test_rerank_on_empty_input():
    out = rerank("שאלה", [], top_k=5)
    assert out.candidates == [] and not out.applied


# ------------------------------------------------------------------ הקשר
def test_context_respects_token_budget():
    cands = [cand(i, vrank=i, content="מילה " * 400) for i in range(1, 10)]
    chosen = build_context(cands, max_tokens=500)
    assert 0 < len(chosen) < len(cands)


def test_context_deduplicates_overlapping_chunks():
    same = "אותו תוכן בדיוק שחוזר על עצמו בשני צ'אנקים חופפים"
    cands = [cand(1, vrank=1, content=same), cand(2, vrank=2, content=same), cand(3, vrank=3, content="אחר")]
    chosen = build_context(cands, max_tokens=10_000)
    assert len(chosen) == 2


def test_context_always_returns_at_least_one_chunk():
    """גם צ'אנק שחורג מהתקציב לבדו נכנס — עדיף הקשר אחד מאשר כלום."""
    chosen = build_context([cand(1, vrank=1, content="מילה " * 5000)], max_tokens=10)
    assert len(chosen) == 1
