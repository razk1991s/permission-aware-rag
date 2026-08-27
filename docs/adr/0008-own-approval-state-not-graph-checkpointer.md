# ADR 0008 — Persist pending approvals in an application table, not a graph checkpointer

**Status:** Accepted · **Date:** 2026-08-22 · **Deciders:** Raz K. · **Relates to:** ADR 0006

## Context

ADR 0006 requires that an action needing human approval **pauses and is saved**, then resumes after a decision that may come hours later. The natural mechanism in LangGraph is `interrupt()` plus a checkpointer — `langgraph-checkpoint-postgres` persists the full graph state and resumes at the interrupt point.

## Decision

Use LangGraph for the graph structure (nodes, conditional edges, typed state), but persist pending approvals in our own `agent_actions` table rather than a graph checkpointer. Resuming means executing the stored action, not replaying the graph.

## Rationale

**Driver mismatch.** `langgraph-checkpoint-postgres` is built on psycopg; the entire application runs on asyncpg (ADR 0001). Adding a second Postgres driver means two connection pools, two transaction models, and two failure modes against one database — a permanent operational cost for one feature.

**The paused state is small and well-defined.** A pending action is an action type and a payload — `{customer_name, amount, reason}` — plus the tier and citation that produced it. It is not an arbitrary continuation. Serializing a whole graph state to resume a two-field INSERT is machinery without a matching problem.

**Approvals must be queryable.** "Which actions are pending, requested by whom, under which policy clause, for how long" is an operational question an auditor asks, and it should be one `SELECT`. Graph checkpoints are opaque blobs keyed by thread; answering that question over them means either a parallel index or deserializing every row.

**Schema evolution is safer.** A checkpoint written under one `AgentState` shape can fail to resume after the state changes — an in-flight approval silently becomes unresumable. An explicit table with typed columns fails loudly at migration time instead.

## Consequences

**Positive**

- One database driver, one pool, one transaction model.
- `agent_actions` is directly queryable, indexable, and auditable in SQL.
- Approvals survive deployments that change the graph's internal state shape.
- The approval flow is readable by someone who has never used LangGraph.

**Negative**

- We do not get free resumption of arbitrary mid-graph pauses. A future action needing to pause *in the middle of a multi-step tool chain* would need real checkpointing.
- Two mechanisms would coexist if that day comes — graph checkpoints for complex pauses, `agent_actions` for simple ones. That is a real cost to weigh then, not now.
- `interrupt()` is not exercised, so the LangGraph-specific interrupt/resume idiom is not demonstrated in this codebase.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **`langgraph-checkpoint-postgres`** | Second driver (psycopg) alongside asyncpg; opaque state for a case that is not opaque. Reconsider if pauses become mid-chain. |
| **In-memory checkpointer** | Loses every pending approval on restart. Unacceptable for financial actions. |
| **Redis checkpoint** | Third datastore, and reintroduces the sync problem ADR 0001 avoids. |
| **Execute optimistically, roll back on rejection** | A refund that was created and then reversed is not the same as one never created. Auditors see both. |

## Revisit when

- An action requires pausing **mid-tool-chain** rather than at a decision point, where the remaining plan genuinely must be preserved.
- The application moves to psycopg for other reasons, removing the driver-mismatch objection.
- Pending approvals need to survive a change in action semantics, not just action data.
