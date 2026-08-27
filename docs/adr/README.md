# Architecture Decision Records

Each ADR records one decision: the context that forced it, what was decided, what it costs, what was rejected, and the condition that would make us change our minds.

ADRs are immutable once accepted. A decision that changes is not edited — a new ADR supersedes it, and the old one is marked `Superseded by ADR-NNNN`. The history is the point.

| # | Decision | Status |
|---|---|---|
| [0001](0001-pgvector-over-dedicated-vector-db.md) | PostgreSQL + pgvector instead of a dedicated vector database | Accepted |
| [0002](0002-authz-in-sql-not-post-filter.md) | Document-level authorization enforced in SQL, never post-filtered | Accepted |
| [0003](0003-rrf-over-weighted-score-fusion.md) | Reciprocal Rank Fusion for hybrid search, not weighted score averaging | Accepted |
| [0004](0004-hebrew-full-text-search-with-simple-config.md) | `simple` text-search config plus trigrams for Hebrew lexical search | Accepted |
| [0005](0005-query-catalog-over-text-to-sql.md) | Parameterized query catalog instead of generated SQL | Accepted |
| [0006](0006-policy-driven-approval-gates.md) | Approval tiers derived from policy documents; execution paused via checkpointing | Accepted |
| [0007](0007-local-models-behind-a-gateway.md) | Local open models, behind a gateway that keeps the provider replaceable | Accepted |
| [0008](0008-own-approval-state-not-graph-checkpointer.md) | Pending approvals persisted in an application table, not a graph checkpointer | Accepted |
| [0009](0009-deterministic-provider-for-ci.md) | A deterministic provider for CI — and a refusal to measure quality with it | Accepted |
| [0010](0010-angular-spa-served-by-fastapi.md) | Angular SPA served by FastAPI from a single origin | Accepted |

## The through-line

Six of these ten decisions constrain the language model rather than empower it: authorization lives outside its reach (0002), its database access is a fixed menu (0005), its approval authority is bounded by a document and a human (0006), and its provider is swappable (0007). The model is treated as an untrusted reasoning component, not a security boundary.

The other four (0003, 0004, 0008, 0010) are engineering trade-offs whose effects are measured rather than asserted — retrieval quality in `docs/EVALUATION.md`, the approval path in the end-to-end tests, and in 0010's case the deliberate choice to pay two toolchains for one deployable.

## Template

```markdown
# ADR NNNN — <decision in one line, imperative>

**Status:** Proposed | Accepted | Superseded by ADR-NNNN · **Date:** YYYY-MM-DD · **Deciders:**

## Context
What forces the decision? Constraints, requirements, what breaks if we do nothing.

## Decision
What we are doing. Present tense, specific.

## Rationale
Why this and not the obvious alternative. This is the section interviewers read.

## Consequences
Positive, negative, and neutral. **An ADR with no negative consequences is not finished.**

## Alternatives considered
| Option | Why rejected |

## Revisit when
The concrete condition — a metric, a scale, a requirement — that would reopen this.
```

The last two sections are what separate an ADR from documentation. Anyone can list what they built; stating what it costs and what would change your mind is the part that shows judgment.
