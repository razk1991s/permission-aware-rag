"""בדיקות יחידה לסוכן: קטלוג השאילתות, חישוב בטוח, וקביעת דרג אישור."""

from __future__ import annotations

import ast

import pytest

from app.agent.approval import ApprovalTier, can_approve, parse_tiers_from_text
from app.agent.query_catalog import (
    CATALOG,
    InvalidQueryParams,
    QueryNotFound,
    catalog_for_prompt,
    get_spec,
    validate_params,
)
from app.agent.tools import _safe_eval


# ------------------------------------------------------- קטלוג שאילתות
def test_unknown_query_name_is_rejected():
    with pytest.raises(QueryNotFound):
        get_spec("drop_everything")


def test_unknown_parameter_is_rejected_not_ignored():
    """פרמטר שלא בסכמה נדחה. התעלמות שקטה היא איך הזרקות מצליחות."""
    spec = get_spec("open_refunds_older_than")
    with pytest.raises(InvalidQueryParams):
        validate_params(spec, {"days": 14, "bypass_acl": True})


def test_params_are_cast_to_declared_types():
    bound = validate_params(get_spec("open_refunds_older_than"), {"days": "14", "limit": "5"})
    assert bound == {"days": 14, "limit": 5}
    assert isinstance(bound["days"], int)


def test_out_of_range_parameter_is_rejected():
    spec = get_spec("open_refunds_older_than")
    with pytest.raises(InvalidQueryParams):
        validate_params(spec, {"days": -5, "limit": 10})
    with pytest.raises(InvalidQueryParams):
        validate_params(spec, {"days": 14, "limit": 10_000})


def test_defaults_fill_optional_params():
    assert validate_params(get_spec("open_refunds_older_than"), {"days": 14})["limit"] == 50


def test_missing_required_param_is_rejected():
    with pytest.raises(InvalidQueryParams):
        validate_params(get_spec("refund_status_by_id"), {})


def test_no_catalog_sql_is_interpolated():
    """אסור ש-SQL בקטלוג ייבנה במחרוזת. הכול חייב להיות פרמטרים קשורים."""
    for spec in CATALOG.values():
        assert "%s" not in spec.sql
        assert "format(" not in spec.sql
        assert "+ '" not in spec.sql


def test_catalog_for_prompt_hides_unauthorized_queries():
    support = {q["name"] for q in catalog_for_prompt({"support"})}
    finance = {q["name"] for q in catalog_for_prompt({"finance"})}
    employee = {q["name"] for q in catalog_for_prompt({"employee"})}
    assert "transactions_summary" in finance
    assert "transactions_summary" not in support   # finance/admin בלבד
    assert employee == set()


# ------------------------------------------------------------ חישוב בטוח
def test_calculator_evaluates_arithmetic():
    assert _safe_eval(ast.parse("4200 * 0.028", mode="eval")) == pytest.approx(117.6)


def test_calculator_rejects_function_calls():
    with pytest.raises(ValueError):
        _safe_eval(ast.parse("__import__('os').system('ls')", mode="eval"))


def test_calculator_rejects_attribute_access():
    with pytest.raises(ValueError):
        _safe_eval(ast.parse("(1).__class__", mode="eval"))


def test_calculator_rejects_huge_exponent():
    with pytest.raises(ValueError):
        _safe_eval(ast.parse("9**99999", mode="eval"))


# ------------------------------------------------------------ דרגי אישור
POLICY = (
    "5. סמכויות אישור\n"
    "5.1 נציג מוקד\n"
    "נציג מוקד מוסמך לאשר זיכוי בסכום של עד 2,500 ש\"ח, ובלבד שסיבת הזיכוי מאושרת.\n"
    "5.2 מנהל צוות\n"
    "מנהל צוות מוסמך לאשר זיכוי בסכום של 2,501 עד 15,000 ש\"ח.\n"
    "5.3 ועדת זיכויים\n"
    "זיכוי בסכום העולה על 15,000 ש\"ח יובא לאישור ועדת הזיכויים.\n"
)


def test_tiers_are_parsed_per_sentence_not_per_chunk():
    """הבאג שהיה: הצ'אנק מכיל את 5.1 ו-5.2 יחד, והסכום הגדול שויך לנציג."""
    tiers = parse_tiers_from_text([(POLICY, "5. סמכויות אישור", "FIN-001")])
    by_name = {t.name: t for t in tiers}
    assert by_name["representative"].max_amount == 2500
    assert by_name["team_lead"].max_amount == 15000
    assert by_name["committee"].max_amount is None


def test_tier_citation_points_at_the_right_subsection():
    tiers = {t.name: t for t in parse_tiers_from_text([(POLICY, "5", "FIN-001")])}
    assert tiers["representative"].citation.endswith("5.1")
    assert tiers["team_lead"].citation.endswith("5.2")


def test_other_documents_do_not_contaminate_tiers():
    """נוהל האשראי מזכיר 75,000 ש\"ח למנהל צוות — ואסור שייכנס לספי הזיכוי."""
    credit = "3.2 מנהל צוות אשראי מוסמך לאשר מסגרת בסכום של 20,001 עד 75,000 ש\"ח."
    tiers = parse_tiers_from_text([(POLICY, "5", "FIN-001"), (credit, "3.2", "FIN-005")])
    assert {t.name: t.max_amount for t in tiers}["team_lead"] == 15000


def test_conflicting_ceilings_resolve_to_the_stricter_one():
    """שגיאת חילוץ חייבת להוביל להחמרה, לא להקלה."""
    a = "5.1 נציג מוקד מוסמך לאשר עד 2,500 ש\"ח."
    b = "5.1 נציג מוקד מוסמך לאשר עד 9,000 ש\"ח."
    tiers = {t.name: t for t in parse_tiers_from_text([(a, "5.1", "FIN-001"), (b, "5.1", "FIN-001")])}
    assert tiers["representative"].max_amount == 2500


def test_requester_cannot_approve_own_action():
    tier = ApprovalTier("team_lead", "finance", 15000, "FIN-001 §5.2", "", "document")
    ok, why = can_approve(approver_roles={"finance"}, tier=tier, is_requester=True)
    assert not ok and "עצמו" in why


def test_admin_can_approve_any_tier():
    tier = ApprovalTier("committee", "admin", None, "FIN-001 §5.3", "", "document")
    assert can_approve(approver_roles={"admin"}, tier=tier, is_requester=False)[0]


def test_wrong_role_cannot_approve():
    tier = ApprovalTier("team_lead", "finance", 15000, "FIN-001 §5.2", "", "document")
    ok, why = can_approve(approver_roles={"support"}, tier=tier, is_requester=False)
    assert not ok and "finance" in why
