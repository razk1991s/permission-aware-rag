"""קטלוג שאילתות פרמטריות — במקום Text-to-SQL.

ה-LLM בוחר **שם** שאילתה וערכי פרמטרים. הוא לעולם לא כותב SQL.
ראה ADR 0005 לנימוק המלא. בקצרה: SQL שנוצר על ידי מודל הוא טקסט
שגורם חיצוני יכול להשפיע עליו, ולכן מסמך מורעל יכול לשנות את מבנה
השאילתה. עם קטלוג, המבנה נקבע בזמן פיתוח ולהזרקה אין לאן לנחות.

הספים בשאילתות אינם מקודדים כאן אלא מגיעים כפרמטר — הסוכן שולף אותם
מהנוהל (ראה app/agent/approval.py). זה מה שהופך את שאלת הדמו
"לפי נוהל הזיכויים, אילו לקוחות בחריגה?" לשאלה משולבת אמיתית.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ParamType = Literal["int", "float", "str", "date"]


@dataclass(frozen=True)
class QuerySpec:
    name: str
    description: str
    sql: str
    params: dict[str, ParamType]
    required_roles: tuple[str, ...]
    defaults: dict[str, Any] = field(default_factory=dict)
    bounds: dict[str, tuple[float, float]] = field(default_factory=dict)
    max_rows: int = 100


CATALOG: dict[str, QuerySpec] = {
    "open_refunds_older_than": QuerySpec(
        name="open_refunds_older_than",
        description=(
            "בקשות זיכוי פתוחות שנפתחו לפני יותר מ-N ימים. "
            "משמש לאיתור בקשות בחריגה מהנוהל."
        ),
        sql="""
            SELECT r.id AS request_id,
                   c.full_name AS customer,
                   r.amount,
                   r.reason,
                   EXTRACT(DAY FROM now() - r.opened_at)::int AS days_open
            FROM refund_requests r
            JOIN customers c ON c.id = r.customer_id
            WHERE r.status = 'open'
              AND r.opened_at < now() - make_interval(days => :days)
            ORDER BY days_open DESC
            LIMIT :limit
        """,
        params={"days": "int", "limit": "int"},
        required_roles=("finance", "support", "admin"),
        defaults={"limit": 50},
        bounds={"days": (0, 3650), "limit": (1, 100)},
    ),
    "count_open_refunds_older_than": QuerySpec(
        name="count_open_refunds_older_than",
        description="כמה בקשות זיכוי פתוחות מעל N ימים. מחזיר מספר אחד.",
        sql="""
            SELECT count(*) AS total
            FROM refund_requests
            WHERE status = 'open'
              AND opened_at < now() - make_interval(days => :days)
        """,
        params={"days": "int"},
        required_roles=("finance", "support", "admin"),
        bounds={"days": (0, 3650)},
    ),
    "refund_status_by_id": QuerySpec(
        name="refund_status_by_id",
        description="סטטוס בקשת זיכוי לפי מזהה הבקשה.",
        sql="""
            SELECT r.id AS request_id, c.full_name AS customer, r.amount, r.status,
                   r.reason, r.opened_at, r.resolved_at,
                   EXTRACT(DAY FROM now() - r.opened_at)::int AS days_open
            FROM refund_requests r
            JOIN customers c ON c.id = r.customer_id
            WHERE r.id = :request_id
        """,
        params={"request_id": "int"},
        required_roles=("finance", "support", "admin"),
        bounds={"request_id": (1, 10_000_000)},
    ),
    "refunds_by_status": QuerySpec(
        name="refunds_by_status",
        description="פילוח בקשות הזיכוי לפי סטטוס.",
        sql="""
            SELECT status, count(*) AS total, round(sum(amount), 2) AS total_amount
            FROM refund_requests
            GROUP BY status
            ORDER BY total DESC
        """,
        params={},
        required_roles=("finance", "support", "admin"),
    ),
    "customer_refunds": QuerySpec(
        name="customer_refunds",
        description="בקשות הזיכוי של לקוח לפי שם.",
        sql="""
            SELECT r.id AS request_id, r.amount, r.status, r.reason,
                   EXTRACT(DAY FROM now() - r.opened_at)::int AS days_open
            FROM refund_requests r
            JOIN customers c ON c.id = r.customer_id
            WHERE c.full_name = :customer_name
            ORDER BY r.opened_at DESC
            LIMIT :limit
        """,
        params={"customer_name": "str", "limit": "int"},
        required_roles=("finance", "support", "admin"),
        defaults={"limit": 20},
        bounds={"limit": (1, 100)},
    ),
    "transactions_summary": QuerySpec(
        name="transactions_summary",
        description="סיכום עסקאות לפי סוג ומטבע ב-N הימים האחרונים.",
        sql="""
            SELECT tx_type, currency, count(*) AS total, round(sum(amount), 2) AS volume
            FROM transactions
            WHERE created_at > now() - make_interval(days => :days)
            GROUP BY tx_type, currency
            ORDER BY volume DESC
            LIMIT :limit
        """,
        params={"days": "int", "limit": "int"},
        required_roles=("finance", "admin"),
        defaults={"days": 30, "limit": 50},
        bounds={"days": (1, 3650), "limit": (1, 100)},
    ),
}


class QueryNotFound(KeyError):
    pass


class InvalidQueryParams(ValueError):
    pass


_CASTERS = {"int": int, "float": float, "str": str}


def validate_params(spec: QuerySpec, raw: dict[str, Any]) -> dict[str, Any]:
    """המרה וטיפוס לכל פרמטר, אכיפת תחומים, ודחיית פרמטרים לא מוכרים.

    זה השלב שהופך קלט שמודל ייצר לקלט שאפשר לקשור לשאילתה. פרמטר שאינו
    בסכמה נדחה — לא מתעלמים ממנו בשקט.
    """
    unknown = set(raw) - set(spec.params)
    if unknown:
        raise InvalidQueryParams(f"פרמטרים לא מוכרים: {sorted(unknown)}")

    out: dict[str, Any] = {}
    for key, kind in spec.params.items():
        value = raw.get(key, spec.defaults.get(key))
        if value is None:
            raise InvalidQueryParams(f"חסר פרמטר חובה: {key}")
        try:
            value = _CASTERS[kind](value)
        except (TypeError, ValueError) as exc:
            raise InvalidQueryParams(f"הפרמטר {key} אינו מטיפוס {kind}") from exc

        if key in spec.bounds:
            lo, hi = spec.bounds[key]
            if not (lo <= value <= hi):
                raise InvalidQueryParams(f"הפרמטר {key}={value} מחוץ לתחום [{lo}, {hi}]")
        if kind == "str" and len(value) > 200:
            raise InvalidQueryParams(f"הפרמטר {key} ארוך מדי")
        out[key] = value
    return out


def get_spec(name: str) -> QuerySpec:
    if name not in CATALOG:
        raise QueryNotFound(
            f"אין שאילתה בשם {name!r}. השאילתות הזמינות: {sorted(CATALOG)}"
        )
    return CATALOG[name]


def catalog_for_prompt(roles: set[str]) -> list[dict]:
    """תיאור הקטלוג עבור המודל — רק שאילתות שהמשתמש מורשה להריץ.

    שאילתה שאינה מותרת אינה מוצגת בכלל. אין טעם לפתות את המודל לבחור
    בה ואז לחסום — ואם היא לא מוצגת, גם מסמך מורעל לא יכול לבקש אותה
    בשם שלה בהצלחה.
    """
    return [
        {"name": s.name, "description": s.description, "params": s.params}
        for s in CATALOG.values()
        if roles.intersection(s.required_roles)
    ]
