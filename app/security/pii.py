"""הסרת מזהים לפני שליחה למודל.

גם כשהמודל מקומי — התהליך הוא מה שנדרש בארגון פיננסי, והוא מה שנשאלים
עליו בראיון. חשוב מכך: אם ביום מן הימים השער ינותב לספק ענן, ההסרה כבר
במקום ואינה תלויה בזכירה של מישהו.
"""

from __future__ import annotations

import re

# --- מספר זהות ישראלי: 9 ספרות עם ספרת ביקורת ---
_ID = re.compile(r"(?<!\d)(\d{9})(?!\d)")
# --- כרטיס אשראי: 13–19 ספרות עם מפרידים אפשריים ---
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_IL = re.compile(r"(?<!\d)(?:\+972[- ]?|0)(?:[23489]|5\d|7\d)[- ]?\d{3}[- ]?\d{4}(?!\d)")


def luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def israeli_id_ok(digits: str) -> bool:
    """ספרת ביקורת של תעודת זהות — מונע סימון של כל מספר בן 9 ספרות."""
    if len(digits) != 9 or not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(digits):
        d = int(ch) * (1 if i % 2 == 0 else 2)
        total += d if d < 10 else d - 9
    return total % 10 == 0


def redact(text: str) -> str:
    """מחליף מזהים בתגיות. שומר על אורך הטקסט קריא ועל ההקשר סביבו."""
    if not text:
        return text

    def _card(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return "[REDACTED:CARD]" if len(digits) >= 13 and luhn_ok(digits) else m.group(0)

    def _id(m: re.Match) -> str:
        return "[REDACTED:ID]" if israeli_id_ok(m.group(1)) else m.group(0)

    text = _CARD.sub(_card, text)
    text = _ID.sub(_id, text)
    text = _IBAN.sub("[REDACTED:IBAN]", text)
    text = _EMAIL.sub("[REDACTED:EMAIL]", text)
    text = _PHONE_IL.sub("[REDACTED:PHONE]", text)
    return text


def find_pii(text: str) -> list[str]:
    """מחזיר את סוגי המזהים שנמצאו — לשימוש בבקרה ובדוחות, לא בהסרה."""
    found: list[str] = []
    if any(israeli_id_ok(m.group(1)) for m in _ID.finditer(text)):
        found.append("ID")
    if any(luhn_ok(re.sub(r"\D", "", m.group(0))) for m in _CARD.finditer(text)):
        found.append("CARD")
    if _IBAN.search(text):
        found.append("IBAN")
    if _EMAIL.search(text):
        found.append("EMAIL")
    if _PHONE_IL.search(text):
        found.append("PHONE")
    return found
