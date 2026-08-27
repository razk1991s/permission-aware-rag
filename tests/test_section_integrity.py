"""שלמות נתיב הסעיף — הבדיקה שמגינה על הציטוטים.

הבאג שהבדיקות האלה נכתבו בעקבותיו: חפיפה שנגררה מעבר לגבול סעיף. הזנב
של סעיף 3.2 נכנס לתוך צ'אנק שנתיב הסעיף שלו כבר היה 4, ולכן ציטוט
לצ'אנק הזה היה מפנה את הקורא לסעיף הלא נכון במסמך.
"""

from __future__ import annotations

from app.ingestion.chunking import chunk_by_structure
from app.ingestion.parsers import Block


def test_overlap_never_crosses_a_section_boundary():
    blocks = [
        Block("heading", "3. תהליך", level=2),
        Block("para", "טקסט של סעיף שלוש. " * 30),
        Block("heading", "4. לוחות זמנים", level=2),
        Block("para", "טקסט של סעיף ארבע. " * 30),
    ]
    chunks = chunk_by_structure(blocks, target_tokens=400, overlap_ratio=0.3, min_tokens=10)

    for c in chunks:
        if c.section_path and c.section_path.startswith("4."):
            assert "סעיף שלוש" not in c.content, "זנב הסעיף הקודם נגרר מעבר לגבול"
        if c.section_path and c.section_path.startswith("3."):
            assert "סעיף ארבע" not in c.content


def test_overlap_still_applies_inside_a_long_section():
    blocks = [Block("heading", "1. מטרה", level=2)] + [
        Block("para", f"פסקה {i}. " * 40) for i in range(6)
    ]
    plain = chunk_by_structure(blocks, target_tokens=200, overlap_ratio=0.0, min_tokens=20)
    overlapped = chunk_by_structure(blocks, target_tokens=200, overlap_ratio=0.3, min_tokens=20)
    assert sum(c.token_count for c in overlapped) > sum(c.token_count for c in plain)


def test_every_chunk_after_first_heading_has_a_section_path():
    blocks = [
        Block("heading", "נוהל זיכויים", level=1),
        Block("heading", "4. לוחות זמנים", level=2),
        Block("para", "תוכן. " * 50),
        Block("heading", "5. סמכויות", level=2),
        Block("para", "תוכן נוסף. " * 50),
    ]
    chunks = chunk_by_structure(blocks, target_tokens=300, min_tokens=10)
    assert all(c.section_path for c in chunks)
    assert chunks[-1].section_path == "נוהל זיכויים › 5. סמכויות"
