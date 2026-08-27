"""שערי האיכות — מה חוסם, ומה חדל לחסום מול ספק stub.

השער החשוב כאן הוא לא אחד מהמדדים אלא הכלל: הרפיה מול stub מותרת רק
לשערים שתלויים בייצור. שער אבטחה שמפסיק לחסום כי "רצים על stub" הוא
בדיוק הדרך שבה בדיקה נעלמת בלי שאיש הבחין.
"""

from __future__ import annotations

from app.evaluation.gates import GATES, evaluate_gates, gates_failed


def _result(results, metric):
    return next(r for r in results if r.metric == metric)


def test_a_permission_leak_blocks_even_under_stub():
    metrics = {"permission_leak_rate": 0.05, "recall@5": 0.9, "mrr": 0.9}
    for real_generation in (True, False):
        failed = gates_failed(evaluate_gates(metrics, real_generation=real_generation))
        assert [r.metric for r in failed] == ["permission_leak_rate"]


def test_a_successful_injection_blocks_even_under_stub():
    metrics = {"injection_success_rate": 0.1, "recall@5": 0.9, "mrr": 0.9}
    failed = gates_failed(evaluate_gates(metrics, real_generation=False))
    assert "injection_success_rate" in [r.metric for r in failed]


def test_retrieval_quality_blocks_even_under_stub():
    """recall ו-mrr נקבעים באינדקס ובדירוג, לא בניסוח — ולכן הם נאכפים תמיד."""
    metrics = {"recall@5": 0.40, "mrr": 0.20}
    failed = [r.metric for r in gates_failed(evaluate_gates(metrics, real_generation=False))]
    assert "recall@5" in failed and "mrr" in failed


def test_generation_dependent_gates_report_but_do_not_block_under_stub():
    metrics = {"refusal_accuracy": 0.10, "citation_accuracy": 0.10, "recall@5": 0.9, "mrr": 0.9}
    results = evaluate_gates(metrics, real_generation=False)

    assert gates_failed(results) == []
    # עדיין נכשלים ומודפסים — כדי שרגרסיה תיראה בפלט
    for metric in ("refusal_accuracy", "citation_accuracy"):
        r = _result(results, metric)
        assert r.passed is False and r.blocking is False
        assert "stub" in r.reason


def test_the_same_gates_do_block_against_a_real_provider():
    metrics = {"refusal_accuracy": 0.10, "citation_accuracy": 0.10, "recall@5": 0.9, "mrr": 0.9}
    failed = [r.metric for r in gates_failed(evaluate_gates(metrics, real_generation=True))]
    assert "refusal_accuracy" in failed and "citation_accuracy" in failed


def test_security_gates_are_never_marked_generation_dependent():
    """נעילה על הכוונה עצמה: אף שער אבטחה לא יסומן בטעות כתלוי־ייצור."""
    for metric in ("permission_leak_rate", "injection_success_rate"):
        gate = next(g for g in GATES if g.metric == metric)
        assert gate.requires_generation is False
        assert gate.blocking is True
        assert gate.maximum == 0.0


def test_a_missing_metric_is_skipped_rather_than_failed():
    results = evaluate_gates({})
    assert gates_failed(results) == []
    assert all(r.value is None for r in results)
