"""Resolve approval tiers from a procedure document.

ADR 0006: approval thresholds are not hard-coded. The agent retrieves the
authorization section from the refund procedure, extracts its thresholds, and
stores the citation that led to the decision. When Finance changes the procedure,
the system changes with it.

This makes organizational control dependent on retrieval quality, so the hard
ceiling is enforced explicitly: amounts above approval_hard_ceiling always
require committee approval. **Retrieval can only make the decision stricter.**
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
    max_amount: float | None      # None = no ceiling
    citation: str
    reason: str
    source: str                   # "document" | "fallback"


# Conservative fallback, used only when retrieval fails. The lower amounts are
# intentional: retrieval failure must make the decision stricter.
FALLBACK_TIERS = (
    ApprovalTier("representative", "support", 1_000.0, "Fallback", "Procedure retrieval failed", "fallback"),
    ApprovalTier("team_lead", "finance", 10_000.0, "Fallback", "Procedure retrieval failed", "fallback"),
    ApprovalTier("committee", "admin", None, "Fallback", "Procedure retrieval failed", "fallback"),
)

_AMOUNT = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)\s*(?:ש\"ח|₪|שקל)")
_SECTION = re.compile(r"(\d+\.\d+)")
_SECTION_HEADING = re.compile(r"^\s*(\d+\.\d+)\s")


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ""))


def _sentences(text_: str) -> list[str]:
    """Split text into lines and sentences.

    This is essential because one chunk can contain sections 5.1, 5.2, and 5.3.
    Chunk-level analysis would assign the largest amount to the first tier found.
    """
    parts: list[str] = []
    for line in text_.splitlines():
        parts.extend(p.strip() for p in re.split(r"(?<=[.!?])\s+", line) if p.strip())
    return parts


def parse_tiers_from_text(chunks: list[tuple[str, str, str]]) -> list[ApprovalTier]:
    """Extract approval thresholds from procedure chunks.

    Each chunk is (text, citation, doc_id). Analysis is sentence-level; every
    sentence must contain both a tier keyword and an amount. The largest amount
    in a sentence is its ceiling because procedures use ranges.
    """
    found: list[ApprovalTier] = []

    for content, citation, doc_id in chunks:
        # Only the document defining authorization rules is relevant. Otherwise,
        # the credit procedure can contaminate refund thresholds.
        if doc_id != settings.approval_policy_doc:
            continue

        current_section: str | None = None
        for sentence in _sentences(content):
            # A section heading updates context. The amount may appear in the
            # next sentence, so citation tracking must not point to the prior section.
            # Store this citation so the decision can be verified.
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

    # Deduplicate by tier and keep the lowest ceiling: extraction errors must
    # make the decision stricter rather than weaker.
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
    """Return the approval tier required for an amount."""
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
