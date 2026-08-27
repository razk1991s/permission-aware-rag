"""ניקוי טקסט — עם דגש על מה שקורה לעברית ב-PDF.

חילוץ טקסט עברי מ-PDF מחזיר טקסט קריא, אבל עם שני ארטיפקטים קבועים
שנובעים מאלגוריתם ה-bidi ולא מבאג:

1. סימן פיסוק סופי נודד לתחילת השורה:  ".השירות או ללקוחות"
2. כל שורה ויזואלית היא בלוק נפרד, ולכן משפט אחד מפוצל לכמה בלוקים.

בלי תיקון, הצ'אנקים נחתכים באמצע משפט — וזה פוגע גם בהטמעות וגם ב-BM25.
"""

from __future__ import annotations

import re

# סימני פיסוק שנודדים לתחילת שורה בטקסט RTL
_LEADING_PUNCT = re.compile(r"^\s*([.,;:!?…])\s*")
# סוף משפט אמיתי: פיסוק סופי, נקודתיים, או סוגר
_SENTENCE_END = re.compile(r"[.!?:;׃]\s*$|[)\]]\s*$")
_CONTROL = re.compile(r"[‎‏‪-‮⁦-⁩]")  # סימני bidi בלתי נראים
_WS = re.compile(r"[ \t ]+")


def fix_hanging_punctuation(line: str) -> tuple[str, str]:
    """מחזיר (הפיסוק שנדד לתחילת השורה, השורה בלעדיו)."""
    m = _LEADING_PUNCT.match(line)
    if not m:
        return "", line
    return m.group(1), line[m.end():]


def normalize(text: str) -> str:
    text = _CONTROL.sub("", text)
    text = _WS.sub(" ", text)
    return text.strip()


def join_lines(lines: list[str]) -> list[str]:
    """מאחד שורות לפסקאות שלמות ומחזיר את הפיסוק הנודד למקומו.

    שורה מצטרפת לקודמתה אלא אם הקודמת נגמרה בסימן סוף משפט, או שהשורה
    הנוכחית פותחת פריט רשימה או סעיף ממוספר.
    """
    out: list[str] = []
    for raw in lines:
        line = normalize(raw)
        if not line:
            continue

        # הפיסוק שמופיע בתחילת השורה הוא סופו של המשפט שהשורה הזו מסיימת:
        # ב-RTL הנקודה הסופית מרונדרת בקצה השמאלי, ולכן היא מחולצת ראשונה.
        # מחזירים אותה לסוף השורה הנוכחית — לא לסוף הקודמת.
        punct, body = fix_hanging_punctuation(line)
        if punct:
            if not body:
                if out:
                    out[-1] = out[-1].rstrip() + punct
                continue
            line = body.rstrip() + punct

        starts_new = bool(re.match(r"^\s*(?:[•\-–—*]|\d+[.)]|\d+\.\d+)", line))
        if out and not starts_new and not _SENTENCE_END.search(out[-1]):
            out[-1] = f"{out[-1]} {line}".strip()
        else:
            out.append(line)
    return out


def clean_blocks(blocks: list) -> list:
    """מאחד בלוקי para עוקבים שהם למעשה שורות של אותה פסקה.

    פועל רק על בלוקי para מאותו עמוד — כותרות, שורות טבלה ושאלות
    לעולם לא מתאחדות, כי הן יחידות מידע עצמאיות.
    """
    from app.ingestion.parsers import Block

    result: list[Block] = []
    buffer: list[Block] = []

    def flush() -> None:
        if not buffer:
            return
        merged = join_lines([b.text for b in buffer])
        for text in merged:
            result.append(Block("para", text, page=buffer[0].page, sheet=buffer[0].sheet))
        buffer.clear()

    for b in blocks:
        if b.kind == "para" and (not buffer or buffer[-1].page == b.page):
            buffer.append(b)
            continue
        flush()
        if b.kind == "para":
            buffer.append(b)
        else:
            result.append(Block(b.kind, normalize(b.text), level=b.level, page=b.page,
                                sheet=b.sheet, row=b.row, meta=b.meta))
    flush()
    return [b for b in result if b.text]
