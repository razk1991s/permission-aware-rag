"""Parameterized query catalog instead of Text-to-SQL.

The LLM chooses a query **name** and parameter values; it never writes SQL.
See ADR 0005. Model-generated SQL is text that external content can influence,
while a catalog fixes the query structure during development.

Query thresholds are parameters extracted from procedures by the agent
(see app/agent/approval.py), enabling genuine procedure-and-data questions.
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
            "Open refund requests older than N days. "
            "Used to find requests outside procedure limits."
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
        description="Count open refund requests older than N days. Returns one number.",
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
        description="Refund request status by request ID.",
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
        description="Break down refund requests by status.",
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
        description="A customer's refund requests by name.",
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
        description="Transaction summary by type and currency for the last N days.",
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
    """Cast and validate every parameter, enforcing bounds and rejecting unknowns.

    This converts model-generated input into values safe to bind to a query.
    Parameters outside the schema are rejected rather than silently ignored.
    """
    unknown = set(raw) - set(spec.params)
    if unknown:
        raise InvalidQueryParams(f"Unknown parameters: {sorted(unknown)}")

    out: dict[str, Any] = {}
    for key, kind in spec.params.items():
        value = raw.get(key, spec.defaults.get(key))
        if value is None:
            raise InvalidQueryParams(f"Missing required parameter: {key}")
        try:
            value = _CASTERS[kind](value)
        except (TypeError, ValueError) as exc:
            raise InvalidQueryParams(f"Parameter {key} is not of type {kind}") from exc

        if key in spec.bounds:
            lo, hi = spec.bounds[key]
            if not (lo <= value <= hi):
                raise InvalidQueryParams(f"Parameter {key}={value} is outside [{lo}, {hi}]")
        if kind == "str" and len(value) > 200:
            raise InvalidQueryParams(f"Parameter {key} is too long")
        out[key] = value
    return out


def get_spec(name: str) -> QuerySpec:
    if name not in CATALOG:
        raise QueryNotFound(
            f"No query named {name!r}. Available queries: {sorted(CATALOG)}"
        )
    return CATALOG[name]


def catalog_for_prompt(roles: set[str]) -> list[dict]:
    """Describe only catalog queries the user is authorized to run.

    Unauthorized queries are omitted entirely. If they are not shown, a poisoned
    document cannot successfully request them by name.
    """
    return [
        {"name": s.name, "description": s.description, "params": s.params}
        for s in CATALOG.values()
        if roles.intersection(s.required_roles)
    ]
