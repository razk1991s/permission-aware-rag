"""קביעת דרג האישור מתוך מסמך הנוהל.

ADR 0006: ספי האישור אינם מקודדים. הסוכן שולף את סעיף הסמכויות מנוהל
הזיכויים ומחלץ ממנו את הספים, ושומר את הציטוט שהוביל להחלטה. כשמחלקת
הכספים תשנה את הסף בנוהל — המערכת תשתנה איתו.

הסתייגות שמטופלת כאן במפורש: זה הופך בקרה ארגונית לתלויה באיכות
השליפה. לכן קיימת תקרה קשיחה — מעל approval_hard_ceiling תמיד נדרש
אישור ועדה, בלי קשר למה שנשלף. **השליפה יכולה רק להחמיר, לא להקל.**
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import settings
from app.retrieval.pipeline import retrieve

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApprovalTier:
    name: str
    role: str
    max_amount: float | None      # None = ללא תקרה
    citation: str
    reason: str
    source: str                   # "document" | "fallback"


# ברירת מחדל שמרנית, לשימוש רק אם השליפה נכשלה. הסכומים כאן נמוכים
# מהמופיע בנוהל בכוונה: כשל בשליפה חייב להוביל להחמרה, לא להקלה.
FALLBACK_TIERS = (
    ApprovalTier("representative", "support", 1_000.0, "ברירת מחדל", "שליפת הנוהל נכשלה", "fallback"),
    ApprovalTier("team_lead", "finance", 10_000.0, "ברירת מחדל", "שליפת הנוהל נכשלה", "fallback"),
    ApprovalTier("committee", "admin", None, "ברירת מחדל", "שליפת הנוהל נכשלה", "fallback"),
)

_AMOUNT = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)\s*(?:ש\"ח|₪|שקל)")
_SECTION = re.compile(r"(\d+\.\d+)")
_SECTION_HEADING = re.compile(r"^\s*(\d+\.\d+)\s")


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ""))


def _sentences(text_: str) -> list[str]:
    """פיצול לשורות ולמשפטים.

    זה לא קישוט: צ'אנק אחד מכיל את כל סעיף 5 — גם 5.1 וגם 5.2 וגם 5.3.
    ניתוח ברמת הצ'אנק ייקח את הסכום הגדול ביותר וישייך אותו לדרג הראשון
    שזוהה, ויקבע שנציג מוקד מוסמך לאשר 15,000 ש"ח. זה בדיוק הבאג שהיה כאן.
    """
    parts: list[str] = []
    for line in text_.splitlines():
        parts.extend(p.strip() for p in re.split(r"(?<=[.!?])\s+", line) if p.strip())
    return parts


def parse_tiers_from_text(chunks: list[tuple[str, str, str]]) -> list[ApprovalTier]:
    """מחלץ ספי אישור מקטעי הנוהל.

    כל קטע הוא (טקסט, ציטוט, doc_id). הניתוח הוא ברמת משפט, וכל משפט
    חייב להכיל גם מילת מפתח של דרג וגם סכום — אחרת הוא נדחה. הסכום
    הגדול ביותר במשפט הוא התקרה, כי הניסוח הוא "בסכום של 2,501 עד 15,000".
    """
    found: list[ApprovalTier] = []

    for content, citation, doc_id in chunks:
        # רק המסמך שמגדיר את הסמכויות. בלי זה, נוהל האשראי (75,000 ש"ח)
        # נשלף יחד עם נוהל הזיכויים ומזהם את הספים.
        if doc_id != settings.approval_policy_doc:
            continue

        current_section: str | None = None
        for sentence in _sentences(content):
            # כותרת סעיף מעדכנת את ההקשר. הסכום עצמו מופיע במשפט הבא,
            # ובלי מעקב הציטוט היה מצביע על הסעיף הקודם — והמאשר קורא
            # את הציטוט הזה כדי לאמת את ההחלטה.
            heading = _SECTION_HEADING.match(sentence)
            if heading:
                current_section = heading.group(1)

            amounts = [_to_float(m.group(1)) for m in _AMOUNT.finditer(sentence)]
            is_committee = bool(re.search(r"ועד(?:ה|ת)", sentence))
            if not amounts and not is_committee:
                continue

            section = current_section or (
                _SECTION.search(sentence).group(1) if _SECTION.search(sentence) else None
            )
            cite = (
                f"{settings.approval_policy_doc} §{section}"
                if section
                else (citation or settings.approval_policy_doc)
            )
            ceiling = max(amounts) if amounts else None

            if re.search(r"נציג\s+(?:מוקד|שירות)", sentence) and ceiling:
                found.append(ApprovalTier("representative", "support", ceiling, cite,
                                          "נציג מוסמך עד סכום זה", "document"))
            elif re.search(r"מנהל\s+צוות", sentence) and ceiling:
                found.append(ApprovalTier("team_lead", "finance", ceiling, cite,
                                          "מנהל צוות מוסמך עד סכום זה", "document"))
            elif is_committee:
                found.append(ApprovalTier("committee", "admin", None, cite,
                                          "מעל הסכום המרבי נדרשת ועדה", "document"))

    # דה־דופליקציה לפי דרג — התקרה הנמוכה ביותר שנמצאה, כי שגיאת חילוץ
    # צריכה להוביל להחמרה ולא להקלה.
    by_name: dict[str, ApprovalTier] = {}
    for tier in found:
        current = by_name.get(tier.name)
        if current is None or (
            tier.max_amount is not None
            and current.max_amount is not None
            and tier.max_amount < current.max_amount
        ):
            by_name[tier.name] = tier
    return sorted(by_name.values(), key=lambda t: (t.max_amount is None, t.max_amount or 0))


async def resolve_approval_tier(
    conn: AsyncConnection, *, user_id: int, amount: float, action_type: str = "create_refund"
) -> ApprovalTier:
    """מחזיר את דרג האישור הנדרש לסכום נתון."""
    tiers: list[ApprovalTier] = []
    try:
        result = await retrieve(
            conn,
            user_id=user_id,
            question="סמכויות אישור זיכוי — נציג מוקד, מנהל צוות, ועדת זיכויים",
            top_k=8,
            domain="finance",
            use_understanding=False,
        )
        tiers = parse_tiers_from_text(
            [(c.content, c.section_path or c.citation, c.doc_id) for c in result.candidates]
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("approval tier retrieval failed: %s", exc)

    if not tiers:
        log.warning("no approval tiers parsed from policy — using conservative fallback")
        tiers = list(FALLBACK_TIERS)

    if not any(t.max_amount is None for t in tiers):
        tiers.append(
            ApprovalTier("committee", "admin", None,
                         tiers[-1].citation, "מעל התקרה הגבוהה ביותר שנמצאה", tiers[-1].source)
        )

    selected = next((t for t in tiers if t.max_amount is None or amount <= t.max_amount), tiers[-1])

    # --- תקרה קשיחה: השליפה יכולה רק להחמיר ---
    if amount > settings.approval_hard_ceiling and selected.name != "committee":
        log.warning(
            "hard ceiling overrode document tier: amount=%s tier=%s", amount, selected.name
        )
        return ApprovalTier(
            "committee",
            "admin",
            None,
            selected.citation,
            f"סכום מעל התקרה הקשיחה ({settings.approval_hard_ceiling:,.0f} ₪) — נדרשת ועדה",
            "hard_ceiling",
        )
    return selected


def can_approve(*, approver_roles: set[str], tier: ApprovalTier, is_requester: bool) -> tuple[bool, str]:
    """בדיקת סמכות בזמן ההחלטה, לא בזמן הבקשה."""
    if is_requester:
        return False, "מבקש הפעולה אינו יכול לאשר את עצמו"
    if "admin" in approver_roles:
        return True, "admin"
    if tier.role in approver_roles:
        return True, tier.role
    return False, f"נדרש תפקיד {tier.role}"
