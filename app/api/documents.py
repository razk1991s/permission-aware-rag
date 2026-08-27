"""ניהול מסמכים. כל שליפה כאן מסוננת ב-SQL לפי ה-ACL של המשתמש."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import text

from app.config import settings
from app.core.deps import ConnDep, UserDep, audit, require_roles
from app.ingestion.pipeline import DocumentMeta, ingest_file

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_SUFFIXES = {".pdf", ".docx", ".xlsx", ".html", ".htm"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class DocumentOut(BaseModel):
    doc_id: str
    title: str
    domain: str
    doc_type: str | None
    file_type: str
    version: str | None
    status: str
    chunk_count: int


class ChunkOut(BaseModel):
    id: int
    chunk_index: int
    section_path: str | None
    page_number: int | None
    strategy: str | None
    token_count: int | None
    content: str


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    user: UserDep,
    conn: ConnDep,
    domain: str | None = None,
    include_superseded: bool = False,
) -> list[DocumentOut]:
    """רק מסמכים שהמשתמש מורשה לראות. אין כאן סינון בדיעבד."""
    if not user.allowed_doc_ids:
        return []
    rows = await conn.execute(
        text(
            """
            SELECT doc_id, title, domain, doc_type, file_type, version, status, chunk_count
            FROM documents
            WHERE id = ANY(:ids)
              AND (CAST(:domain AS text) IS NULL OR domain = :domain)
              AND (:include_superseded OR status = 'active')
            ORDER BY domain, doc_id
            """
        ),
        {
            "ids": list(user.allowed_doc_ids),
            "domain": domain,
            "include_superseded": include_superseded,
        },
    )
    return [DocumentOut(**r._mapping) for r in rows.all()]


@router.get("/{doc_id}/chunks", response_model=list[ChunkOut])
async def document_chunks(
    doc_id: str, user: UserDep, conn: ConnDep, limit: int = 200
) -> list[ChunkOut]:
    """תצוגת הצ'אנקים — כלי הדיבאג המרכזי של שבוע 1."""
    rows = await conn.execute(
        text(
            """
            SELECT c.id, c.chunk_index, c.section_path, c.page_number,
                   c.strategy, c.token_count, c.content
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE d.doc_id = :doc_id AND d.id = ANY(:ids)
            ORDER BY c.chunk_index
            LIMIT :limit
            """
        ),
        {"doc_id": doc_id, "ids": list(user.allowed_doc_ids) or [0], "limit": limit},
    )
    items = rows.all()
    if not items:
        # אותה תשובה למסמך שאינו קיים ולמסמך שאינו מורשה — אחרת אפשר
        # למפות אילו מסמכים קיימים במערכת.
        await audit(
            conn, actor_id=user.id, action="read_chunks", outcome="blocked", resource=doc_id
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "המסמך לא נמצא")
    return [ChunkOut(**r._mapping) for r in items]


@router.post("/upload", response_model=dict, status_code=status.HTTP_201_CREATED)
async def upload_document(
    conn: ConnDep,
    user: Annotated[object, Depends(require_roles("admin"))],
    file: UploadFile = File(...),
    doc_id: str = Form(...),
    title: str = Form(...),
    domain: str = Form(...),
    allowed_roles: str = Form(..., description="רשימה מופרדת בפסיקים, למשל: finance,admin"),
    doc_type: str | None = Form(None),
    version: str | None = Form(None),
    effective_from: str | None = Form(None),
    with_embeddings: bool = Form(True),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"סוג קובץ לא נתמך: {suffix}")

    roles = [r.strip() for r in allowed_roles.split(",") if r.strip()]
    if not roles:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "חובה לציין לפחות תפקיד אחד")

    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.upload_dir / f"{uuid.uuid4().hex}{suffix}"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out, length=1024 * 1024)
    if dest.stat().st_size > MAX_UPLOAD_BYTES:
        dest.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "הקובץ גדול מדי")

    meta = DocumentMeta(
        doc_id=doc_id,
        title=title,
        domain=domain,
        allowed_roles=roles,
        doc_type=doc_type,
        version=version,
        effective_from=effective_from,
    )
    try:
        result = await ingest_file(
            conn, dest, meta, with_embeddings=with_embeddings, uploaded_by=user.id  # type: ignore[attr-defined]
        )
    except ValueError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    await audit(
        conn,
        actor_id=user.id,  # type: ignore[attr-defined]
        action="upload_document",
        outcome="allowed",
        resource=doc_id,
        detail={"chunks": result.chunks, "roles": roles},
    )
    return {
        "doc_id": result.doc_id,
        "chunks": result.chunks,
        "skipped": result.skipped,
        "reason": result.reason,
    }


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: str,
    conn: ConnDep,
    user: Annotated[object, Depends(require_roles("admin"))],
) -> None:
    res = await conn.execute(text("DELETE FROM documents WHERE doc_id = :d"), {"d": doc_id})
    if res.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "המסמך לא נמצא")
    await audit(
        conn,
        actor_id=user.id,  # type: ignore[attr-defined]
        action="delete_document",
        outcome="allowed",
        resource=doc_id,
    )
