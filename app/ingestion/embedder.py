"""הטמעות. שני מימושים: מודל מקומי אמיתי, ו-stub דטרמיניסטי לבדיקות."""

from __future__ import annotations

import logging

from app.config import settings

log = logging.getLogger(__name__)

_model = None


class EmbeddingsDisabled(RuntimeError):
    pass


def _load_model():
    """טעינה חד־פעמית. ההורדה הראשונה של bge-m3 שוקלת בערך 2.3GB."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        log.info("loading embedding model %s", settings.embedding_model)
        _model = SentenceTransformer(settings.embedding_model)
    return _model


def _stub_vectors(texts: list[str]) -> list[list[float]]:
    from app.llm.providers import StubProvider

    return [StubProvider._hash_vector(t, settings.embedding_dim) for t in texts]


def embed_texts(texts: list[str], *, batch_size: int | None = None) -> list[list[float]]:
    """מחזיר וקטורים מנורמלים ל-L2.

    נרמול הופך מכפלה סקלרית לקוסינוס, וזה מה ש-pgvector מחשב עם האופרטור
    <=>. בלי נרמול, אורך הווקטור מזהם את הדירוג.
    """
    if not settings.embeddings_enabled:
        raise EmbeddingsDisabled(
            "ההטמעות מכובות (EMBEDDINGS_ENABLED=false). "
            "הפעל אותן, או הרץ אינג'סט עם --skip-embeddings."
        )
    if not texts:
        return []

    if settings.embedding_provider == "stub":
        # ‏stub אינו סמנטי: טקסטים שחולקים מילים יהיו קרובים, וזהו.
        # מספיק כדי לבדוק שהשליפה והאינדקס עובדים, חסר משמעות למדידת איכות.
        if not settings.is_dev:
            raise RuntimeError("אסור להשתמש ב-embedding stub מחוץ לסביבת פיתוח")
        return _stub_vectors(texts)

    model = _load_model()
    vectors = model.encode(
        texts,
        batch_size=batch_size or settings.embedding_batch_size,
        normalize_embeddings=True,
        show_progress_bar=len(texts) > 64,
        convert_to_numpy=True,
    )
    dim = vectors.shape[1]
    if dim != settings.embedding_dim:
        raise ValueError(
            f"המודל מחזיר {dim} מימדים אבל הסכמה מצפה ל-{settings.embedding_dim}. "
            f"עדכן EMBEDDING_DIM ואת טיפוס העמודה chunks.embedding, ובנה מחדש את האינדקס."
        )
    return vectors.tolist()


def embed_query(query: str) -> list[float]:
    return embed_texts([query])[0]
