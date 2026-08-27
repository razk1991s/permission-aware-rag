"""Embeddings: a real local model and a deterministic test stub."""

from __future__ import annotations

import logging

from app.config import settings

log = logging.getLogger(__name__)

_model = None


class EmbeddingsDisabled(RuntimeError):
    pass


def _load_model():
    """Load once; the first bge-m3 download is about 2.3 GB."""
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
    """Return vectors normalized to L2.

    Normalization makes the dot product equivalent to cosine similarity, which
    pgvector computes with <=>. Without normalization, vector length pollutes ranking.
    """
    if not settings.embeddings_enabled:
        raise EmbeddingsDisabled(
            "Embeddings are disabled (EMBEDDINGS_ENABLED=false). "
            "Enable them or run ingestion with --skip-embeddings."
        )
    if not texts:
        return []

    if settings.embedding_provider == "stub":
        # The stub is not semantic: texts sharing words will be close.
        # It tests retrieval and indexing, but is meaningless for quality measurement.
        if not settings.is_dev:
            raise RuntimeError("The embedding stub is not allowed outside development")
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
            f"The model returns {dim} dimensions but the schema expects {settings.embedding_dim}. "
            "Update EMBEDDING_DIM and chunks.embedding, then rebuild the index."
        )
    return vectors.tolist()


def embed_query(query: str) -> list[float]:
    return embed_texts([query])[0]
