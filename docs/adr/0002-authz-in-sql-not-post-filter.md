# ADR 0002 — Enforce document-level authorization in SQL, never as a post-filter

**Status:** Accepted · **Date:** 2026-08-21 · **Deciders:** Raz K.

## Context

The corpus spans two domains that must not leak into each other. HR documents (salary bands, termination procedure) are visible to `hr` and `admin`; finance documents to `finance` and `admin`; `support` sees a *subset* of finance — the refund and fraud procedures, but not the interest-rate policy or the credit-limit procedure.

The system is an LLM application, so there are three plausible places to enforce this: in the prompt, in application code after retrieval, or in the retrieval query itself.

## Decision

1. Authorization is resolved **once, first**, from the JWT — before any graph node runs — into `state.allowed_doc_ids`.
2. Every retrieval query filters on that set **inside SQL**, in the same statement that ranks.
3. `user_id`, `user_roles`, and `allowed_doc_ids` are **never exposed as tool parameters** to the LLM and never appear in a prompt.
4. A deterministic **egress check** verifies, after generation, that every cited chunk was in the set actually served.

## Rationale

**Prompt-level enforcement is not enforcement.** Asking the model "only answer from documents the user may see" fails against a single poisoned document instructing otherwise (see ADR 0005 and the red-team suite). The model is an untrusted reasoning component, not a security boundary.

**Post-filtering is a leak and a ranking bug.** A chunk retrieved and then discarded has already crossed into application memory, and dropping it after ranking means the user gets the top-5 of an unfiltered set minus some rows — not the top-5 of their permitted set. Recall degrades silently and unevenly.

**Tool parameters are attacker-controlled.** If `search_documents(query, user_id)` exposes `user_id`, the LLM generates its value — and anything the LLM generates can be influenced by retrieved text. The tool signature therefore takes only `query`, `domain`, and `top_k`; identity is injected by the runtime from state.

## Consequences

**Positive**

- A prompt injection attempting privilege escalation cannot succeed, because the escalation path does not exist in the code.
- The filter benefits from the same index scan as the search; no wasted retrieval work.
- `permission_leak_rate` is measurable, deterministic, and gate-able in CI.

**Negative**

- Every retrieval path must be written against the `allowed` CTE. A new query that forgets it is a silent vulnerability — mitigated by routing all retrieval through a single `retrieval/` module and by 20 permission tests in CI.
- Role changes take effect only on the next token issue (15-minute TTL), not instantly.

## Implementation notes

- `document_acl(document_id, role_id, permission)` is the single source of truth; it is populated at ingest time from `allowed_roles` in `manifest.json`.
- Failures fail **closed**: a database error, timeout, or missing ACL row yields a refusal, never a partial answer.
- Every blocked access is written to `audit_log` with `outcome='blocked'`. The table is append-only; the application role holds no `UPDATE` or `DELETE` grant on it.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **PostgreSQL Row-Level Security (RLS)** | Genuinely attractive, and arguably stricter. Rejected for now because it requires per-request `SET LOCAL` role switching that interacts badly with connection pooling, and it makes the authorization logic invisible in the application code — which is exactly what this project wants to *show*. Reconsider for a real production deployment. |
| **Separate index per role** | Duplicates chunks, multiplies storage and embedding cost, and breaks for users holding two roles. |
| **Post-retrieval filtering** | Leaks into memory and silently degrades ranking, as described above. |

## Revisit when

- The permission model grows beyond roles to per-user or per-group grants, at which point RLS becomes the better fit.
- A requirement appears for field-level redaction inside an otherwise-permitted document.
