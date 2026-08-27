"""בדיקות ניקוי טקסט עברי — הארטיפקטים שחילוץ מ-PDF מייצר."""

from __future__ import annotations

from app.ingestion.cleaning import (
    clean_blocks,
    fix_hanging_punctuation,
    join_lines,
    normalize,
)
from app.ingestion.parsers import Block


def test_hanging_period_moves_back_to_previous_line():
    """הארטיפקט המרכזי: נקודה סופית שנודדת לתחילת השורה הבאה."""
    lines = [
        "מסמך זה מרכז את מדיניות התמחור של החברה",
        ".ואינו מיועד להפצה ללקוחות",
    ]
    out = join_lines(lines)
    assert len(out) == 1
    # הנקודה חוזרת לסוף המשפט שהשורה מסיימת, והשורות מתאחדות לפסקה אחת
    assert out[0] == "מסמך זה מרכז את מדיניות התמחור של החברה ואינו מיועד להפצה ללקוחות."
    assert not out[0].startswith(".")


def test_split_sentence_is_rejoined():
    lines = ["זיכוי שאושר יבוצע ויוזרם לחשבון הלקוח", "בתוך 14 ימי עסקים ממועד אישור הבקשה."]
    out = join_lines(lines)
    assert out == ["זיכוי שאושר יבוצע ויוזרם לחשבון הלקוח בתוך 14 ימי עסקים ממועד אישור הבקשה."]


def test_sentence_end_starts_new_paragraph():
    lines = ["המשפט הראשון נגמר כאן.", "המשפט השני מתחיל כאן."]
    assert len(join_lines(lines)) == 2


def test_list_item_starts_new_line_even_mid_sentence():
    lines = ["הנוהל חל על", "• נציגי מוקד", "• מחלקת התפעול"]
    out = join_lines(lines)
    assert len(out) == 3


def test_numbered_section_is_not_merged_into_previous():
    lines = ["טקסט שאינו נגמר בנקודה", "4.2 חריגה ממועד הביצוע"]
    out = join_lines(lines)
    assert len(out) == 2
    assert out[1].startswith("4.2")


def test_fix_hanging_punctuation_returns_parts():
    punct, rest = fix_hanging_punctuation(".המשך המשפט")
    assert punct == "."
    assert rest == "המשך המשפט"
    assert fix_hanging_punctuation("שורה רגילה") == ("", "שורה רגילה")


def test_normalize_strips_bidi_marks_and_extra_spaces():
    dirty = "‏טקסט‎   עם    רווחים‫"
    clean = normalize(dirty)
    assert clean == "טקסט עם רווחים"


def test_clean_blocks_merges_paragraphs_but_not_headings():
    blocks = [
        Block("heading", "4. לוחות זמנים", level=2, page=1),
        Block("para", "זיכוי שאושר יבוצע", page=1),
        Block("para", ".בתוך 14 ימי עסקים", page=1),
        Block("heading", "5. סמכויות", level=2, page=1),
    ]
    out = clean_blocks(blocks)
    kinds = [b.kind for b in out]
    assert kinds == ["heading", "para", "heading"]
    assert out[1].text == "זיכוי שאושר יבוצע בתוך 14 ימי עסקים."


def test_clean_blocks_does_not_merge_rows():
    blocks = [Block("row", "דרגה: 6 | 15,600"), Block("row", "דרגה: 7 | 18,500")]
    out = clean_blocks(blocks)
    assert len(out) == 2


def test_clean_blocks_keeps_pages_separate():
    blocks = [
        Block("para", "סוף עמוד אחד בלי נקודה", page=1),
        Block("para", "תחילת עמוד שתיים", page=2),
    ]
    out = clean_blocks(blocks)
    assert len(out) == 2
