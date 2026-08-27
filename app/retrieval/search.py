"""שליפה היברידית: וקטורי ולקסיקלי, מסוננים בהרשאות, ממוזגים ב-RRF.

זה הלב של המערכת. שלוש נקודות שחשוב לשים לב אליהן בקוד שלמטה:

1. ה-CTE בשם allowed מסנן הרשאות **בתוך** השאילתה שמדרגת. אין סינון
   בדיעבד ואין רשימה שמורכבת באפליקציה (ADR 0002).
2. המיזוג הוא RRF — לפי מיקום בדירוג ולא לפי ציון — כי ציון קוסינוס
   וציון ts_rank_cd אינם ברי־השוואה (ADR 0003).
3. הסינון הלקסיקלי משתמש בקונפיגורציית simple, כי לפוסטגרס אין תמיכה
   בעברית (ADR 0004).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import settings

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    chunk_id: int
    document_id: int
    chunk_index: int
    doc_id: str
    title: str
    section_path: str | None
    page_number: int | None
    content: str
    vector_score: float | None = None
    vector_rank: int | None = None
    bm25_score: float | None = None
    bm25_rank: int | None = None
    rrf_score: float = 0.0
    rerank_score: float | None = None
    rerank_delta: int | None = None      # כמה מקומות הרירנקר הזיז אותו
    matched_queries: list[str] = field(default_factory=list)

    @property
    def citation(self) -> str:
        parts = [self.title]
        if self.section_path:
            parts.append(self.section_path.split(" › ")[-1])
        if self.page_number:
            parts.append(f"עמ' {self.page_number}")
        return ", ".join(parts)

    @property
    def best_score(self) -> float:
        if self.rerank_score is not None:
            return self.rerank_score
        return self.vector_score or 0.0


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


# ------------------------------------------------------------------ SQL
_VECTOR_SQL = text(
    """
    WITH allowed AS (
        SELECT DISTINCT a.document_id
        FROM document_acl a
        JOIN user_roles ur ON ur.role_id = a.role_id
        WHERE ur.user_id = :user_id AND a.permission = 'read'
    )
    SELECT c.id            AS chunk_id,
           c.document_id,
           c.chunk_index,
           d.doc_id,
           d.title,
           c.section_path,
           c.page_number,
           c.content,
           1 - (c.embedding <=> CAST(:qvec AS vector)) AS score,
           ROW_NUMBER() OVER (ORDER BY c.embedding <=> CAST(:qvec AS vector)) AS rank
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.document_id IN (SELECT document_id FROM allowed)
      AND c.embedding IS NOT NULL
      AND (:include_superseded OR d.status = 'active')
      AND (CAST(:domain AS text) IS NULL OR d.domain = :domain)
    ORDER BY c.embedding <=> CAST(:qvec AS vector)
    LIMIT :limit
    """
)

_LEXICAL_SQL = text(
    """
    WITH allowed AS (
        SELECT DISTINCT a.document_id
        FROM document_acl a
        JOIN user_roles ur ON ur.role_id = a.role_id
        WHERE ur.user_id = :user_id AND a.permission = 'read'
    ),
    q AS (SELECT plainto_tsquery('simple', :query) AS tsq)
    SELECT c.id            AS chunk_id,
           c.document_id,
           c.chunk_index,
           d.doc_id,
           d.title,
           c.section_path,
           c.page_number,
           c.content,
           ts_rank_cd(c.tsv_simple, q.tsq) AS score,
           ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.tsv_simple, q.tsq) DESC, c.id) AS rank
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    CROSS JOIN q
    WHERE c.document_id IN (SELECT document_id FROM allowed)
      AND (:include_superseded OR d.status = 'active')
      AND (CAST(:domain AS text) IS NULL OR d.domain = :domain)
      AND c.tsv_simple @@ q.tsq
    ORDER BY score DESC, c.id
    LIMIT :limit
    """
)

# חיפוש טריגרם — רשת ביטחון להטיות ולשגיאות כתיב בעברית, שם אין stemmer.
_TRIGRAM_SQL = text(
    """
    WITH allowed AS (
        SELECT DISTINCT a.document_id
        FROM document_acl a
        JOIN user_roles ur ON ur.role_id = a.role_id
        WHERE ur.user_id = :user_id AND a.permission = 'read'
    )
    SELECT c.id AS chunk_id, c.document_id, c.chunk_index, d.doc_id, d.title,
           c.section_path, c.page_number, c.content,
           similarity(c.content, :query) AS score,
           ROW_NUMBER() OVER (ORDER BY similarity(c.content, :query) DESC, c.id) AS rank
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.document_id IN (SELECT document_id FROM allowed)
      AND (:include_superseded OR d.status = 'active')
      AND similarity(c.content, :query) > 0.05
    ORDER BY score DESC, c.id
    LIMIT :limit
    """
)


def _row_to_candidate(row) -> Candidate:
    m = row._mapping
    return Candidate(
        chunk_id=m["chunk_id"],
        document_id=m["document_id"],
        chunk_index=m["chunk_index"],
        doc_id=m["doc_id"],
        title=m["title"],
        section_path=m["section_path"],
        page_number=m["page_number"],
        content=m["content"],
    )


async def vector_search(
    conn: AsyncConnection,
    *,
    user_id: int,
    query_vector: list[float],
    limit: int | None = None,
    domain: str | None = None,
    include_superseded: bool = False,
) -> list[Candidate]:
    rows = await conn.execute(
        _VECTOR_SQL,
        {
            "user_id": user_id,
            "qvec": vector_literal(query_vector),
            "limit": limit or settings.retrieval_candidates,
            "domain": domain,
            "include_superseded": include_superseded,
        },
    )
    out = []
    for row in rows.all():
        c = _row_to_candidate(row)
        c.vector_score = float(row._mapping["score"])
        c.vector_rank = int(row._mapping["rank"])
        out.append(c)
    return out


async def lexical_search(
    conn: AsyncConnection,
    *,
    user_id: int,
    query: str,
    limit: int | None = None,
    domain: str | None = None,
    include_superseded: bool = False,
) -> list[Candidate]:
    params = {
        "user_id": user_id,
        "query": query,
        "limit": limit or settings.retrieval_candidates,
        "domain": domain,
        "include_superseded": include_superseded,
    }
    rows = (await conn.execute(_LEXICAL_SQL, params)).all()
    if not rows:
        # אין התאמת טוקנים — ננסה טריגרם לפני שמוותרים
        rows = (await conn.execute(_TRIGRAM_SQL, params)).all()

    out = []
    for row in rows:
        c = _row_to_candidate(row)
        c.bm25_score = float(row._mapping["score"])
        c.bm25_rank = int(row._mapping["rank"])
        out.append(c)
    return out


# ------------------------------------------------------------------ RRF
def reciprocal_rank_fusion(
    lists: list[list[Candidate]], *, k: int | None = None, limit: int | None = None
) -> list[Candidate]:
    """ממזג רשימות מדורגות לפי מיקום, לא לפי ציון.

    למה לא ממוצע משוקלל: ציון קוסינוס נע ב-[0,1] ו-ts_rank_cd בסקאלה
    פתוחה שתלויה בקורפוס ובשאילתה. נרמול ביניהם הוא ניחוש; RRF פשוט
    לא זקוק לו. ראה ADR 0003.
    """
    k = k or settings.rrf_k
    merged: dict[int, Candidate] = {}

    for ranked in lists:
        for pos, cand in enumerate(ranked, start=1):
            rank = cand.vector_rank or cand.bm25_rank or pos
            existing = merged.get(cand.chunk_id)
            if existing is None:
                merged[cand.chunk_id] = cand
                existing = cand
            else:
                # מאחדים ציונים משתי הרשימות לאותו מועמד
                existing.vector_score = existing.vector_score or cand.vector_score
                existing.vector_rank = existing.vector_rank or cand.vector_rank
                existing.bm25_score = existing.bm25_score or cand.bm25_score
                existing.bm25_rank = existing.bm25_rank or cand.bm25_rank
            existing.rrf_score += 1.0 / (k + rank)

    ordered = sorted(merged.values(), key=lambda c: c.rrf_score, reverse=True)
    return ordered[: (limit or settings.retrieval_candidates)]


async def expand_with_neighbours(
    conn: AsyncConnection, candidates: list[Candidate], *, user_id: int, window: int = 1
) -> list[Candidate]:
    """מצרף לצ'אנק המוביל את שכניו במסמך.

    מועיל כשהתשובה יושבת על גבול צ'אנקים — הכותרת בצ'אנק אחד והפירוט
    בבא אחריו. השכנים עוברים את אותו סינון הרשאות.
    """
    if not candidates:
        return candidates
    top = candidates[0]
    rows = await conn.execute(
        text(
            """
            WITH allowed AS (
                SELECT DISTINCT a.document_id FROM document_acl a
                JOIN user_roles ur ON ur.role_id = a.role_id
                WHERE ur.user_id = :user_id AND a.permission = 'read'
            )
            SELECT c.id AS chunk_id, c.document_id, c.chunk_index, d.doc_id, d.title,
                   c.section_path, c.page_number, c.content
            FROM chunks c JOIN documents d ON d.id = c.document_id
            WHERE c.document_id = :doc
              AND c.document_id IN (SELECT document_id FROM allowed)
              AND c.chunk_index BETWEEN :lo AND :hi
              AND c.id <> ALL(:known)
            ORDER BY c.chunk_index
            """
        ),
        {
            "user_id": user_id,
            "doc": top.document_id,
            "lo": max(0, top.chunk_index - window),
            "hi": top.chunk_index + window,
            "known": [c.chunk_id for c in candidates],
        },
    )
    extra = [_row_to_candidate(r) for r in rows.all()]
    for c in extra:
        c.rrf_score = top.rrf_score * 0.5
    return candidates + extra
