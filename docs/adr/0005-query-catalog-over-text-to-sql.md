# ADR 0005 — Give the agent a parameterized query catalog instead of generating SQL

**Status:** Accepted · **Date:** 2026-08-21 · **Deciders:** Raz K.

## Context

The agent must answer questions that require operational data, not documents: *"how many refund requests are open past the policy deadline?"*, *"which customers are affected?"*. This requires a tool that reaches the business database.

The common pattern is Text-to-SQL: give the LLM the schema, let it write a query, execute it. It demos beautifully and it is the wrong choice here.

## Decision

The `query_database` tool accepts a **query name** and **typed parameters**, selected from a server-side catalog. It never accepts SQL.

```python
QUERY_CATALOG = {
    "open_refunds_older_than": {
        "description": "Open refund requests older than N days",
        "sql": """SELECT r.id, c.full_name, r.amount,
                         EXTRACT(DAY FROM now() - r.opened_at)::int AS days_open
                  FROM refund_requests r
                  JOIN customers c ON c.id = r.customer_id
                  WHERE r.status = 'open'
                    AND r.opened_at < now() - (:days || ' days')::interval
                  ORDER BY days_open DESC LIMIT :limit""",
        "params": {"days": "int", "limit": "int"},
        "required_roles": ["finance", "support", "admin"],
        "max_rows": 100,
    },
}
```

The LLM's entire influence over the database is: *which* of ~10 named queries, and *what values* for its declared parameters. Parameters are validated against their declared types and bounds before binding.

## Rationale

**Security.** Generated SQL is attacker-influenced text. A poisoned document (see the red-team suite, `INJ-004`) can instruct the model to emit a query with a different `WHERE` clause or a join to a table it should not read. Parameter binding alone does not help when the *structure* is generated. With a catalog, the structure is fixed at development time; the injection has nowhere to land.

**Access control.** Each catalog entry carries `required_roles`, checked in the tool wrapper before execution — consistent with ADR 0002. There is no equivalent check possible for arbitrary generated SQL short of parsing it, and a SQL parser that must decide "is this query safe" is a losing position.

**Predictable performance.** Every catalog query is written against known indexes and carries a `LIMIT`. A generated query can produce a cross join over the transactions table and hold a connection for a minute.

**Correctness.** Business logic — what "open past deadline" means, which statuses count, how the interval is computed — lives in reviewed SQL rather than being re-derived by the model on each call, differently each time.

**It is honest about the constraint.** This is a deliberate reduction of the agent's action space to a set of operations known to be safe and indexed. That is a design decision, not a limitation to apologize for.

## Consequences

**Positive**

- SQL injection through prompt injection is structurally impossible.
- Role checks and row limits are uniform across every data access.
- Query plans are reviewable and testable; each catalog entry has a unit test.
- The catalog doubles as tool documentation for the LLM — `description` and `params` are what the model sees.

**Negative**

- Any question not covered by a catalog entry cannot be answered. Adding a question means writing SQL, which is a development task, not a runtime capability.
- The catalog is a maintenance surface that grows with the product.
- A demo of "ask anything about your database" is not possible — deliberately.

**Mitigation for coverage:** the agent must distinguish "I have no query for this" from "the answer is zero". When no catalog entry matches, it returns `status="recommended"` with a description of what data would be needed, rather than improvising.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Free Text-to-SQL** | Injection surface, unbounded queries, no role enforcement, non-deterministic business logic. |
| **Text-to-SQL against a read-only view layer with a per-role user** | Meaningfully safer, and a legitimate production pattern. Rejected here because it still allows unbounded query shapes and adds per-role database users to manage — complexity that buys flexibility this product does not need. |
| **Generate SQL, then validate with a parser/allowlist** | Requires the validator to be more sophisticated than the generator. A losing arms race. |
| **GraphQL layer** | Solves the shape problem but introduces its own query-complexity attack surface. |

## Revisit when

- The catalog exceeds roughly 30 entries and most additions are trivial variations, suggesting a constrained query builder (fixed tables and joins, generated filters) would serve better.
- A genuine ad-hoc analytics requirement appears, which should then be scoped to a separate read-only replica with its own role — never to this tool.
