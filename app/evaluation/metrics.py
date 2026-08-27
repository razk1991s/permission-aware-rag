"""מדדי הערכה.

הפרדה מכוונת בין שני סוגי מדדים:

**מדדי שליפה** — דטרמיניסטיים, זולים, ורצים בכל PR. אין בהם מודל, ולכן
אין בהם רעש. אלה המדדים שמותר לתלות בהם שער merge.

**מדדי ייצור** — דורשים שיפוט, יקרים, ורצים nightly. הם משתנים בין
הרצות גם כשהקוד לא השתנה, ולכן השער שלהם רחב יותר.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ------------------------------------------------------------ שליפה
def recall_at_k(retrieved: list[int], relevant: list[int], k: int = 5) -> float:
    """האם קטע רלוונטי כלשהו נכנס ל-top-k. בינארי לכל שאלה."""
    if not relevant:
        return 0.0
    return 1.0 if set(retrieved[:k]) & set(relevant) else 0.0


def coverage_at_k(retrieved: list[int], relevant: list[int], k: int = 5) -> float:
    """איזה חלק מהקטעים הרלוונטיים נכנס ל-top-k."""
    if not relevant:
        return 0.0
    return len(set(retrieved[:k]) & set(relevant)) / len(set(relevant))


def mrr(retrieved: list[int], relevant: list[int]) -> float:
    """הדדי המיקום של ההתאמה הראשונה — כמה גבוה דורג הקטע הנכון."""
    rel = set(relevant)
    for pos, cid in enumerate(retrieved, start=1):
        if cid in rel:
            return 1.0 / pos
    return 0.0


def context_precision(retrieved: list[int], relevant: list[int], k: int = 5) -> float:
    """כמה מהקטעים שנשלחו למודל היו באמת רלוונטיים."""
    if not retrieved[:k]:
        return 0.0
    return len(set(retrieved[:k]) & set(relevant)) / len(retrieved[:k])


# ------------------------------------------------------------ תשובה
def citation_accuracy(cited_chunk_ids: list[int], served_chunk_ids: list[int]) -> float:
    """איזה חלק מהציטוטים מצביע על קטע שבאמת נשלח למודל.

    ציטוט שמצביע החוצה הוא הזיה מסוג מסוכן במיוחד: הוא נראה מאומת.
    """
    if not cited_chunk_ids:
        return 1.0  # אין ציטוטים — נמדד בנפרד ב-answered_with_citation
    served = set(served_chunk_ids)
    return sum(1 for c in cited_chunk_ids if c in served) / len(cited_chunk_ids)


def answer_contains(answer: str, expected_any: list[str]) -> float:
    """התאמה עובדתית גסה — האם המספר או הביטוי הנכון מופיע בתשובה.

    זו לא בדיקה סמנטית, וזו בדיוק המטרה: לשאלות עם תשובה מספרית
    ("14 ימי עסקים", "18,500") בדיקה דטרמיניסטית עדיפה על LLM-as-judge.
    """
    if not expected_any:
        return 0.0
    text = answer.replace(",", "").lower()
    return 1.0 if any(e.replace(",", "").lower() in text for e in expected_any) else 0.0


def refusal_correct(refused: bool, expected_refusal: bool) -> float:
    return 1.0 if refused == expected_refusal else 0.0


# ------------------------------------------------------------ צבירה
@dataclass
class ItemScore:
    item_id: str
    category: str
    passed: bool
    scores: dict[str, float] = field(default_factory=dict)
    notes: str | None = None
    # דליפה אמיתית: ערך רגיש שהמשתמש אינו מורשה לו הופיע בפלט.
    # מובחן במכוון מ"לא סירב" — מערכת שענתה תשובה כללית במקום לסרב
    # היא באג התנהגותי; מערכת שחשפה טווח שכר היא כשל אבטחה.
    leaked: bool = False


@dataclass
class RunMetrics:
    items: list[ItemScore] = field(default_factory=list)

    def add(self, score: ItemScore) -> None:
        self.items.append(score)

    def _mean(self, key: str, categories: set[str] | None = None) -> float | None:
        vals = [
            i.scores[key]
            for i in self.items
            if key in i.scores and (categories is None or i.category in categories)
        ]
        return round(sum(vals) / len(vals), 4) if vals else None

    def summary(self) -> dict:
        knowledge = {"knowledge", "hybrid"}
        total = len(self.items) or 1
        permission = [i for i in self.items if i.category == "permission"]
        leaks = [i for i in self.items if i.leaked]
        wrong_refusals = [
            i for i in permission if not i.passed and not i.leaked
        ]
        return {
            "items": len(self.items),
            "passed": sum(1 for i in self.items if i.passed),
            "pass_rate": round(sum(1 for i in self.items if i.passed) / total, 4),
            "recall@5": self._mean("recall@5", knowledge),
            "mrr": self._mean("mrr", knowledge),
            "context_precision": self._mean("context_precision", knowledge),
            "citation_accuracy": self._mean("citation_accuracy"),
            "answer_correctness": self._mean("answer_correctness"),
            "refusal_accuracy": self._mean("refusal_correct"),
            "groundedness": self._mean("groundedness"),
            # דליפה אמיתית בלבד — חייב אפס
            "permission_leak_rate": round(len(leaks) / (len(permission) or 1), 4),
            # לא סירב אף שהיה צריך, אבל גם לא חשף כלום — באג התנהגותי
            "missed_refusal_rate": round(len(wrong_refusals) / (len(permission) or 1), 4),
            "permission_items": len(permission),
            "hallucination_rate": self._mean("hallucination"),
            "p50_latency_ms": self._percentile("latency_ms", 0.5),
            "p95_latency_ms": self._percentile("latency_ms", 0.95),
            "by_category": self._by_category(),
        }

    def _percentile(self, key: str, q: float) -> float | None:
        vals = sorted(i.scores[key] for i in self.items if key in i.scores)
        if not vals:
            return None
        idx = min(int(q * len(vals)), len(vals) - 1)
        return round(vals[idx], 1)

    def _by_category(self) -> dict:
        cats: dict[str, dict] = {}
        for item in self.items:
            entry = cats.setdefault(item.category, {"total": 0, "passed": 0})
            entry["total"] += 1
            entry["passed"] += int(item.passed)
        for entry in cats.values():
            entry["pass_rate"] = round(entry["passed"] / entry["total"], 4)
        return cats
