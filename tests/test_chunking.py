"""בדיקות חיתוך לצ'אנקים — הלוגיקה שמשפיעה הכי הרבה על איכות השליפה."""

from __future__ import annotations

from app.ingestion.chunking import (
    SectionStack,
    choose_strategy,
    chunk_by_qa,
    chunk_by_row,
    chunk_by_structure,
    estimate_tokens,
)
from app.ingestion.parsers import Block


def para(text: str, page: int | None = 1) -> Block:
    return Block("para", text, page=page)


def heading(text: str, level: int = 2, page: int | None = 1) -> Block:
    return Block("heading", text, level=level, page=page)


# ------------------------------------------------------------ נתיב סעיפים
def test_section_stack_builds_hierarchy():
    s = SectionStack()
    s.push(1, "נוהל זיכויים")
    s.push(2, "4. לוחות זמנים")
    s.push(3, "4.1 מועד ביצוע")
    assert s.path == "נוהל זיכויים › 4. לוחות זמנים › 4.1 מועד ביצוע"


def test_section_stack_pops_siblings():
    s = SectionStack()
    s.push(2, "4. לוחות זמנים")
    s.push(3, "4.1 מועד ביצוע")
    s.push(3, "4.2 חריגה")          # אח, לא בן
    assert s.path == "4. לוחות זמנים › 4.2 חריגה"


# ------------------------------------------------------------ חיתוך מבני
def test_heading_forces_new_chunk_even_when_small():
    """גבול סעיף גובר על גודל היעד — סעיף רגולטורי לא נחתך באמצע."""
    blocks = [
        heading("4.1 מועד ביצוע הזיכוי", 3),
        para("זיכוי שאושר יבוצע בתוך 14 ימי עסקים ממועד אישור הבקשה. " * 12),
        heading("4.2 חריגה ממועד הביצוע", 3),
        para("בקשה שלא בוצעה תסומן במערכת כבקשה בחריגה. " * 12),
    ]
    chunks = chunk_by_structure(blocks, target_tokens=600, overlap_ratio=0.0, min_tokens=10)
    assert len(chunks) == 2
    assert "14 ימי עסקים" in chunks[0].content
    assert "בחריגה" in chunks[1].content
    assert chunks[0].section_path.endswith("4.1 מועד ביצוע הזיכוי")
    assert chunks[1].section_path.endswith("4.2 חריגה ממועד הביצוע")


def test_long_section_splits_by_token_budget():
    blocks = [heading("1. מטרה", 2)] + [para("משפט ארוך על נהלים. " * 20) for _ in range(8)]
    chunks = chunk_by_structure(blocks, target_tokens=300, overlap_ratio=0.0, min_tokens=20)
    assert len(chunks) > 1
    assert all(c.token_count <= 900 for c in chunks)


def test_overlap_carries_tail_into_next_chunk():
    blocks = [heading("1. מטרה", 2)] + [para(f"פסקה מספר {i}. " * 25) for i in range(6)]
    no_overlap = chunk_by_structure(blocks, target_tokens=250, overlap_ratio=0.0, min_tokens=20)
    with_overlap = chunk_by_structure(blocks, target_tokens=250, overlap_ratio=0.3, min_tokens=20)
    assert sum(c.token_count for c in with_overlap) > sum(c.token_count for c in no_overlap)


def test_tiny_trailing_block_merges_into_previous():
    blocks = [
        heading("1. מטרה", 2),
        para("תוכן מהותי. " * 40),
        para("קצר."),
    ]
    chunks = chunk_by_structure(blocks, target_tokens=100, overlap_ratio=0.0, min_tokens=30)
    assert "קצר." in chunks[-1].content


def test_chunk_indexes_are_sequential():
    blocks = [heading(f"{i}. סעיף", 2) for i in range(5)]
    chunks = chunk_by_structure(blocks, min_tokens=1)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


# ------------------------------------------------------------ חיתוך שורות
def test_row_chunk_keeps_focus_row_and_neighbours():
    rows = [
        Block("row", f"דרגה: {i} | שכר מינימלי: {i * 1000}", sheet="דרגות שכר", row=i + 1)
        for i in range(1, 6)
    ]
    chunks = chunk_by_row([Block("heading", "גיליון: דרגות שכר", level=1)] + rows, overlap=1)
    assert len(chunks) == 5
    middle = chunks[2]
    assert middle.meta["focus_row"].startswith("דרגה: 3")
    assert "דרגה: 2" in middle.content and "דרגה: 4" in middle.content
    assert middle.row_number == 4
    assert middle.strategy == "row"


def test_row_chunk_edges_do_not_wrap():
    rows = [Block("row", f"שורה {i}", row=i) for i in range(1, 4)]
    chunks = chunk_by_row(rows, overlap=1)
    assert "שורה 3" not in chunks[0].content     # הראשון לא גולש לאחרון
    assert "שורה 1" not in chunks[-1].content


# ------------------------------------------------------------ שאלה ותשובה
def test_qa_pair_stays_together():
    blocks = [
        Block("heading", "זיכויים", level=2),
        Block("question", "תוך כמה זמן אקבל את הזיכוי?"),
        Block("answer", "בתוך 14 ימי עסקים ממועד אישור הבקשה."),
        Block("question", "האם אפשר לקבל במזומן?"),
        Block("answer", "לא."),
    ]
    chunks = chunk_by_qa(blocks)
    assert len(chunks) == 2
    assert "14 ימי עסקים" in chunks[0].content
    assert chunks[0].content.startswith("שאלה:")
    assert chunks[0].section_path == "זיכויים"


def test_answer_without_question_is_dropped():
    chunks = chunk_by_qa([Block("answer", "תשובה יתומה")])
    assert chunks == []


# ------------------------------------------------------------ בחירת אסטרטגיה
def test_strategy_selection():
    qa = [Block("question", "ש"), Block("answer", "ת")]
    rows = [Block("row", f"שורה {i}") for i in range(10)]
    prose = [heading("1. מטרה"), para("טקסט")]
    assert choose_strategy(qa, "docx") == "qa"
    assert choose_strategy(rows, "xlsx") == "row"
    assert choose_strategy(rows, "docx") == "row"       # טבלה גדולה בתוך Word
    assert choose_strategy(prose, "pdf") == "structure"


def test_token_estimate_is_monotonic():
    assert estimate_tokens("קצר") < estimate_tokens("טקסט ארוך בהרבה " * 10)
