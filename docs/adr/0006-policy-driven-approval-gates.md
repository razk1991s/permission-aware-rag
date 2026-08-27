# ADR 0006 — Derive approval requirements from policy documents, and pause execution with checkpointing

**Status:** Accepted · **Amended in part by [ADR 0008](0008-own-approval-state-not-graph-checkpointer.md)** · **Date:** 2026-08-21 · **Deciders:** Raz K.

> **Amendment note:** the *decision to pause and persist* stands unchanged. The *mechanism* changed: pending approvals are stored in the `agent_actions` table rather than a LangGraph Postgres checkpointer. See ADR 0008 for why.

## Context

The agent performs write actions — creating a refund request, adjusting a credit limit. These are not questions with answers; they are operations with consequences, and the organization already governs them.

Procedure FIN-001 §5 defines who may approve a refund:

| Amount | Approver |
|---|---|
| ≤ 2,500 ₪ | Service representative |
| 2,501 – 15,000 ₪ | Team lead |
| > 15,000 ₪ | Refunds committee |

The system needs to respect this. The question is where that knowledge lives.

## Decision

1. **Approval tiers are retrieved from the policy document at decision time**, not hard-coded. The agent resolves the tier by reading FIN-001 §5, and the resulting action record stores the citation (`FIN-001 §5.2`) that justified it.
2. When human approval is required, the graph calls LangGraph's **`interrupt()`**, and state is persisted through a **Postgres checkpointer** keyed by `thread_id`.
3. Approval arrives later — minutes or days — via `POST /actions/{id}/decision`, and execution **resumes from the interrupt point**, not from the beginning.
4. Every action carries an explicit status from a six-value enum: `completed`, `pending_approval`, `blocked`, `rejected`, `recommended`, `failed`.

## Rationale

**Why derive tiers from the document.** Hard-coding `if amount > 15000` duplicates a business rule that already exists in a governed, versioned document. When Finance revises the threshold, the code drifts from the policy and nobody notices until an audit. Reading the rule from the document means updating the procedure updates the system — which is the entire premise of a knowledge-grounded platform. It also produces something valuable for the approver: the request arrives with the clause that generated it, so the human can verify the reasoning rather than trusting it.

The honest caveat: this makes a control depend on retrieval quality. Mitigated by (a) a hard-coded conservative ceiling — any action above 15,000 ₪ requires committee approval regardless of what retrieval returns; (b) failing closed when the policy section cannot be retrieved; (c) an evaluation category that asserts tier resolution across amount boundaries. **Retrieval may loosen nothing; it may only tighten.**

**Why checkpointing rather than a job queue.** The alternative is to abandon the run, store a task row, and re-plan from scratch after approval. That loses the reasoning context, re-runs retrieval, and can produce a different plan than the one the human approved — approving intent A and executing intent B. Checkpointing resumes the *exact* state the approver saw.

**Why six statuses.** A model that reports "I created the request" when the request is merely pending is the confident-failure mode that makes agents untrustworthy. The distinction between *done*, *waiting*, *refused by policy*, *refused by a person*, and *suggested but not attempted* is the difference between a system an organization can install and a demo.

## Consequences

**Positive**

- Threshold changes are document edits, not deployments.
- Approvers see the policy citation behind every request.
- Execution survives restarts; a pending approval is durable state in Postgres.
- Audit trail answers "who approved what, when, under which clause".

**Negative**

- Checkpointed state accumulates and needs a retention policy (proposal: purge resolved threads after 90 days).
- A schema change to `AgentState` can invalidate in-flight checkpoints. Mitigation: version the state schema and refuse to resume a checkpoint written under an older version.
- Latency of the write path now includes a retrieval call to resolve the tier.

## Implementation notes

- `agent_actions` stores `thread_id`, `payload`, `status`, `required_role`, `policy_citation`, `approved_by`, `decided_at`.
- The approver's role is verified at decision time, not at request time — a person who lost the role cannot approve a request queued while they still had it.
- The requester may not approve their own action, regardless of role.
- Bounded autonomy: `MAX_TOOL_CALLS=6`, `MAX_WALL_CLOCK_SEC=45`, and every trace records a mandatory `stop_reason` so a silent loop is impossible to miss.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Hard-coded thresholds** | Drifts from the governing document; loses the citation that makes the request reviewable. |
| **Approval rules in a config table** | Better than code, still a second source of truth to keep synchronized with the procedure. A reasonable fallback if retrieval proves unreliable. |
| **Fire-and-forget with post-hoc review** | Unacceptable for financial operations. |
| **Task queue with re-planning after approval** | Approved intent and executed intent can diverge. |

## Revisit when

- Tier-resolution accuracy in evaluation falls below 98%, at which point the config-table fallback becomes the safer design.
- Approval workflows need delegation, escalation timers, or multi-party sign-off, which would justify a dedicated workflow engine rather than graph interrupts.
