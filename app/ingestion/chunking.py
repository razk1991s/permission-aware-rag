"""חיתוך לצ'אנקים — שלוש אסטרטגיות, לפי סוג התוכן.

אין אסטרטגיה אחת נכונה. מסמך מדיניות נחתך לפי גבולות סעיפים; גיליון
אקסל נחתך שורה־שורה; שאלה ותשובה לעולם לא נפרדות. ההחלטה הזו משפיעה
על איכות השליפה יותר מכל היפר־פרמטר אחר בצינור.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings
from app.ingestion.parsers import Block

# עברית מקודדת בערך ב-3 תווים לטוקן במודלים מולטי־לינגואליים.
# זו הערכה גסה ומכוונת: המטרה היא תקציב עקבי, לא דיוק לטוקן.
CHARS_PER_TOKEN = 3.0


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / CHARS_PER_TOKEN))


@dataclass
class Chunk:
    content: str
    chunk_index: int = 0
    section_path: str | None = None
    page_number: int | None = None
    sheet_name: str | None = None
    row_number: int | None = None
    strategy: str = "structure"
    token_count: int = 0
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.token_count:
            self.token_count = estimate_tokens(self.content)


# ------------------------------------------------------------ מבנה סעיפים
class SectionStack:
    """שומר את היררכיית הכותרות הפעילה ומרכיב ממנה נתיב סעיף."""

    def __init__(self) -> None:
        self._stack: list[tuple[int, str]] = []

    def push(self, level: int, title: str) -> None:
        while self._stack and self._stack[-1][0] >= level:
            self._stack.pop()
        self._stack.append((level, title))

    @property
    def path(self) -> str | None:
        if not self._stack:
            return None
        return " › ".join(t for _lvl, t in self._stack)


# ------------------------------------------------------------ אסטרטגיה 1
def chunk_by_structure(
    blocks: list[Block],
    *,
    target_tokens: int | None = None,
    overlap_ratio: float | None = None,
    min_tokens: int | None = None,
) -> list[Chunk]:
    """חיתוך מודע־מבנה למסמכי מדיניות.

    הצ'אנק נסגר כשמגיעה כותרת חדשה — גם אם לא הגיע לגודל היעד. סעיף
    רגולטורי לא נחתך באמצע, ותשובה לא מנותקת מהכותרת שנותנת לה הקשר.
    """
    target = target_tokens or settings.chunk_target_tokens
    overlap = overlap_ratio if overlap_ratio is not None else settings.chunk_overlap_ratio
    minimum = min_tokens or settings.chunk_min_tokens

    chunks: list[Chunk] = []
    sections = SectionStack()
    buf: list[str] = []
    buf_tokens = 0
    buf_page: int | None = None
    buf_path: str | None = None

    def flush(carry_tail: bool = True) -> None:
        nonlocal buf, buf_tokens, buf_page
        if not buf:
            return
        content = "\n".join(buf).strip()
        if estimate_tokens(content) < minimum and chunks:
            # קטע זעיר — מצרפים לצ'אנק הקודם במקום לייצר רעש
            prev = chunks[-1]
            prev.content = f"{prev.content}\n{content}"
            prev.token_count = estimate_tokens(prev.content)
        elif content:
            chunks.append(
                Chunk(
                    content=content,
                    chunk_index=len(chunks),
                    section_path=buf_path,
                    page_number=buf_page,
                    strategy="structure",
                )
            )
        tail: list[str] = []
        if carry_tail and overlap > 0 and buf:
            budget = int(target * overlap)
            for line in reversed(buf):
                if estimate_tokens("\n".join(tail)) >= budget:
                    break
                tail.insert(0, line)
        buf = list(tail)
        buf_tokens = estimate_tokens("\n".join(buf)) if buf else 0
        buf_page = None

    for block in blocks:
        if block.kind == "heading":
            # גבול סעיף סוגר את הצ'אנק נקי, בלי חפיפה: אחרת סוף הסעיף
            # הקודם היה נגרר לתוך צ'אנק שנתיב הסעיף שלו כבר שונה, והציטוט
            # היה מצביע על הסעיף הלא נכון.
            flush(carry_tail=False)
            sections.push(block.level or 1, block.text)
            buf_path = sections.path
            buf.append(block.text)
            buf_tokens += estimate_tokens(block.text)
            if buf_page is None:
                buf_page = block.page
            continue

        if buf_path is None:
            buf_path = sections.path
        if buf_page is None:
            buf_page = block.page

        tokens = estimate_tokens(block.text)
        if buf_tokens + tokens > target and buf_tokens >= minimum:
            flush()
            buf_path = sections.path
            buf_page = block.page
        buf.append(block.text)
        buf_tokens += tokens

    flush(carry_tail=False)
    for i, c in enumerate(chunks):
        c.chunk_index = i
    return chunks


# ------------------------------------------------------------ אסטרטגיה 2
def chunk_by_row(blocks: list[Block], *, overlap: int | None = None) -> list[Chunk]:
    """חיתוך ברמת שורה לגיליונות.

    כל שורה היא צ'אנק, עם שורה אחת לפניה ואחריה לשמירת הקשר בטבלאות
    רציפות. שים לב: כותרות העמודות כבר מוזרקות בתוך טקסט השורה על ידי
    הפרסר — ולכן כותרת עמודה זדונית מוזרקת מחדש בכל שורה (ראה
    data/redteam ב-INJ-006).
    """
    n = settings.row_chunk_overlap if overlap is None else overlap
    rows = [b for b in blocks if b.kind == "row"]
    headings = [b for b in blocks if b.kind == "heading"]
    sheet_title = headings[0].text if headings else None

    chunks: list[Chunk] = []
    for i, block in enumerate(rows):
        lo, hi = max(0, i - n), min(len(rows), i + n + 1)
        context = [rows[j].text for j in range(lo, hi)]
        content = "\n".join(context)
        chunks.append(
            Chunk(
                content=content,
                chunk_index=len(chunks),
                section_path=block.sheet or sheet_title,
                sheet_name=block.sheet,
                row_number=block.row,
                strategy="row",
                meta={"focus_row": block.text},
            )
        )
    return chunks


# ------------------------------------------------------------ אסטרטגיה 3
def chunk_by_qa(blocks: list[Block]) -> list[Chunk]:
    """שאלה ותשובה הן יחידה אחת. חיתוך ביניהן הורס את שתיהן."""
    chunks: list[Chunk] = []
    sections = SectionStack()
    pending: Block | None = None

    for block in blocks:
        if block.kind == "heading":
            sections.push(block.level or 1, block.text)
        elif block.kind == "question":
            pending = block
        elif block.kind == "answer" and pending is not None:
            chunks.append(
                Chunk(
                    content=f"שאלה: {pending.text}\nתשובה: {block.text}",
                    chunk_index=len(chunks),
                    section_path=sections.path,
                    strategy="qa",
                )
            )
            pending = None
    return chunks


# ------------------------------------------------------------ בחירה
def choose_strategy(blocks: list[Block], file_type: str) -> str:
    kinds = {b.kind for b in blocks}
    if {"question", "answer"} & kinds:
        return "qa"
    if file_type == "xlsx":
        return "row"
    rows = sum(1 for b in blocks if b.kind == "row")
    prose = sum(1 for b in blocks if b.kind in {"para", "heading"})
    if rows and rows > prose * 2:
        return "row"
    return "structure"


def chunk_document(blocks: list[Block], file_type: str) -> list[Chunk]:
    """נקודת הכניסה: בוחר אסטרטגיה ומחזיר צ'אנקים ממוספרים ברצף.

    ‏FAQ מכיל גם זוגות שאלה־תשובה וגם פסקאות מבוא; במקרה כזה מפעילים
    את שתי האסטרטגיות ומאחדים, כדי שהמבוא לא ייעלם.
    """
    strategy = choose_strategy(blocks, file_type)
    if strategy == "qa":
        chunks = chunk_by_qa(blocks)
        prose = [b for b in blocks if b.kind in {"para", "heading"}]
        if prose:
            chunks += chunk_by_structure(prose)
    elif strategy == "row":
        chunks = chunk_by_row(blocks)
    else:
        chunks = chunk_by_structure(blocks)

    for i, c in enumerate(chunks):
        c.chunk_index = i
    return chunks
