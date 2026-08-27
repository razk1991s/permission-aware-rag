#!/usr/bin/env python3
"""טעינת קורפוס שלם מתוך manifest.json.

    python scripts/ingest_corpus.py --corpus data/corpus
    python scripts/ingest_corpus.py --corpus data/corpus --skip-embeddings   # מהיר, לבדיקת מבנה
    python scripts/ingest_corpus.py --corpus data/corpus --include-redteam   # גם המסמכים המורעלים
    python scripts/ingest_corpus.py --corpus data/corpus --dry-run           # פרסור בלבד, בלי DB

ה-manifest הוא מקור האמת: הוא קובע גם את המטא־דאטה וגם את ה-ACL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import dispose_engine, get_engine, run_migrations  # noqa: E402
from app.ingestion.pipeline import DocumentMeta, build_chunks, ingest_file  # noqa: E402

ROLES = ["admin", "hr", "finance", "support", "employee"]


async def ensure_roles(conn) -> None:
    await conn.execute(
        text("INSERT INTO roles (name) SELECT unnest(CAST(:r AS text[])) ON CONFLICT DO NOTHING"),
        {"r": ROLES},
    )


def load_manifest(corpus: Path) -> list[dict]:
    manifest = corpus / "manifest.json"
    if not manifest.exists():
        sys.exit(f"לא נמצא manifest.json בתוך {corpus}")
    return json.loads(manifest.read_text(encoding="utf-8"))


def dry_run(corpus: Path, entries: list[dict]) -> int:
    total = 0
    for e in entries:
        path = corpus / e["source_path"]
        if not path.exists():
            print(f"  ✗ {e['doc_id']:<12} קובץ חסר: {e['source_path']}")
            continue
        chunks, file_type = build_chunks(path)
        total += len(chunks)
        strategy = chunks[0].strategy if chunks else "—"
        sample = (chunks[0].section_path or "—") if chunks else "—"
        print(f"  ✓ {e['doc_id']:<12} {file_type:<5} {len(chunks):>3} צ'אנקים  [{strategy}]  {sample}")
    print(f"\nסה\"כ {total} צ'אנקים מתוך {len(entries)} מסמכים (ללא כתיבה למסד).")
    return total


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path, default=settings.corpus_dir)
    ap.add_argument("--skip-embeddings", action="store_true")
    ap.add_argument("--include-redteam", action="store_true")
    ap.add_argument("--replace", action="store_true", help="טוען מחדש מסמכים שכבר קיימים")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    corpus: Path = args.corpus
    entries = load_manifest(corpus)
    if not args.include_redteam:
        entries = [e for e in entries if not e.get("redteam")]

    print(f"קורפוס: {corpus}  ·  {len(entries)} מסמכים\n")

    if args.dry_run:
        dry_run(corpus, entries)
        return

    await run_migrations()
    engine = get_engine()
    ok = skipped = failed = total_chunks = 0

    async with engine.begin() as conn:
        await ensure_roles(conn)

    for e in entries:
        path = corpus / e["source_path"]
        if not path.exists():
            print(f"  ✗ {e['doc_id']:<12} קובץ חסר")
            failed += 1
            continue
        meta = DocumentMeta(
            doc_id=e["doc_id"],
            title=e["title"],
            domain=e["domain"],
            allowed_roles=e["allowed_roles"],
            doc_type=e.get("doc_type"),
            version=e.get("version"),
            effective_from=e.get("effective_from"),
            effective_to=e.get("effective_to"),
            status=e.get("status", "active"),
            language=e.get("language", "he"),
        )
        try:
            # טרנזקציה לכל מסמך: כישלון באחד לא מפיל את כל הטעינה
            async with engine.begin() as conn:
                res = await ingest_file(
                    conn,
                    path,
                    meta,
                    with_embeddings=not args.skip_embeddings,
                    replace=args.replace,
                )
            if res.skipped:
                print(f"  ○ {e['doc_id']:<12} דילוג — {res.reason}")
                skipped += 1
            else:
                print(f"  ✓ {e['doc_id']:<12} {res.chunks:>3} צ'אנקים")
                ok += 1
                total_chunks += res.chunks
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {e['doc_id']:<12} שגיאה: {exc}")
            failed += 1

    print(f"\nנטענו {ok} · דילוגים {skipped} · כשלים {failed} · {total_chunks} צ'אנקים")
    if not args.skip_embeddings and ok:
        print("צעד הבא: psql -f scripts/build_vector_index.sql  (בניית אינדקס HNSW)")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
