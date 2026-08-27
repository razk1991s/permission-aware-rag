"""Ingestion pipeline: file -> blocks -> cleaning -> chunks -> database.

Operation order matters: ACL rows are written in the same transaction as the
document. A document without ACL is visible to nobody; this intentionally fails closed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ingestion.chunking import Chunk, chunk_document
from app.ingestion.cleaning import clean_blocks
from app.ingestion.embedder import embed_texts
from app.ingestion.parsers import parse

log = logging.getLogger(__name__)

FILE_TYPES = {".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx", ".html": "html", ".htm": "html"}


@dataclass
class DocumentMeta:
    doc_id: str
    title: str
    domain: str
    allowed_roles: list[str]
    doc_type: str | None = None
    version: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    status: str = "active"
    language: str = "he"


@dataclass
class IngestResult:
    doc_id: str
    document_row_id: int | None
    chunks: int
    skipped: bool = False
    reason: str | None = None


def as_date(value: str | dt.date | None) -> dt.date | None:
    """Convert ISO strings to datetime.date values expected by asyncpg.

    The manifest comes from JSON, so dates are ISO strings. Conversion belongs
    here rather than inside the query.
    """
    if value is None or isinstance(value, dt.date):
        return value
    value = value.strip()
    return dt.date.fromisoformat(value) if value else None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def build_chunks(path: Path) -> tuple[list[Chunk], str]:
    """Parse, clean, and chunk without a database, making this easy to test."""
    file_type = FILE_TYPES[path.suffix.lower()]
    blocks = clean_blocks(parse(path))
    return chunk_document(blocks, file_type), file_type


def _vector_literal(vec: list[float]) -> str:
    """Format a vector as the '[0.1,0.2,...]' string accepted by pgvector."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


async def ingest_file(
    conn: AsyncConnection,
    path: Path,
    meta: DocumentMeta,
    *,
    with_embeddings: bool = True,
    uploaded_by: int | None = None,
    replace: bool = False,
) -> IngestResult:
    checksum = sha256(path)

    existing = (
        await conn.execute(
            text("SELECT id, doc_id FROM documents WHERE checksum = :c OR doc_id = :d"),
            {"c": checksum, "d": meta.doc_id},
        )
    ).first()

    if existing and not replace:
        return IngestResult(meta.doc_id, existing.id, 0, skipped=True,
                            reason="Identical document already exists (checksum or doc_id)")
    if existing and replace:
        # Cascading deletion removes the chunks and ACL rows as well.
        await conn.execute(text("DELETE FROM documents WHERE id = :i"), {"i": existing.id})

    chunks, file_type = build_chunks(path)
    if not chunks:
        return IngestResult(meta.doc_id, None, 0, skipped=True, reason="No text was extracted from the file")

    row = (
        await conn.execute(
            text(
                """
                INSERT INTO documents
                    (doc_id, title, source_path, file_type, domain, doc_type, language,
                     version, effective_from, effective_to, status, checksum, chunk_count, meta,
                     uploaded_by)
                VALUES
                    (:doc_id, :title, :source_path, :file_type, :domain, :doc_type, :language,
                     :version, :effective_from, :effective_to,
                     :status, :checksum, :chunk_count, CAST(:meta AS jsonb), :uploaded_by)
                RETURNING id
                """
            ),
            {
                "doc_id": meta.doc_id,
                "title": meta.title,
                "source_path": str(path),
                "file_type": file_type,
                "domain": meta.domain,
                "doc_type": meta.doc_type,
                "language": meta.language,
                "version": meta.version,
                "effective_from": as_date(meta.effective_from),
                "effective_to": as_date(meta.effective_to),
                "status": meta.status,
                "checksum": checksum,
                "chunk_count": len(chunks),
                "meta": json.dumps({"strategy": chunks[0].strategy}, ensure_ascii=False),
                "uploaded_by": uploaded_by,
            },
        )
    ).first()
    document_id = row.id

    # --- ACL is required; there is no "open to everyone" default. ---
    if not meta.allowed_roles:
        raise ValueError(f"{meta.doc_id}: allowed_roles is required; documents without ACL are not loaded")
    await conn.execute(
        text(
            """
            INSERT INTO document_acl (document_id, role_id)
            SELECT :doc, r.id FROM roles r WHERE r.name = ANY(:roles)
            ON CONFLICT DO NOTHING
            """
        ),
        {"doc": document_id, "roles": meta.allowed_roles},
    )
    granted = (
        await conn.execute(
            text("SELECT count(*) FROM document_acl WHERE document_id = :d"), {"d": document_id}
        )
    ).scalar_one()
    if granted != len(meta.allowed_roles):
        raise ValueError(
            f"{meta.doc_id}: created {granted} ACL rows out of {len(meta.allowed_roles)}; "
            "a role may be missing from the roles table"
        )

    # --- Embeddings ---
    vectors: list[list[float]] | None = None
    if with_embeddings:
        vectors = embed_texts([c.content for c in chunks])

    for i, chunk in enumerate(chunks):
        await conn.execute(
            text(
                """
                INSERT INTO chunks
                    (document_id, chunk_index, content, section_path, page_number,
                     sheet_name, row_number, token_count, strategy, embedding, meta)
                VALUES
                    (:document_id, :chunk_index, :content, :section_path, :page_number,
                     :sheet_name, :row_number, :token_count, :strategy,
                     CAST(:embedding AS vector), CAST(:meta AS jsonb))
                """
            ),
            {
                "document_id": document_id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "section_path": chunk.section_path,
                "page_number": chunk.page_number,
                "sheet_name": chunk.sheet_name,
                "row_number": chunk.row_number,
                "token_count": chunk.token_count,
                "strategy": chunk.strategy,
                "embedding": _vector_literal(vectors[i]) if vectors else None,
                "meta": json.dumps(chunk.meta, ensure_ascii=False),
            },
        )

    log.info("ingested %s — %d chunks (%s)", meta.doc_id, len(chunks), chunks[0].strategy)
    return IngestResult(meta.doc_id, document_id, len(chunks))
