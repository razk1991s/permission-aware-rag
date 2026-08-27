# Security Model

This document describes where each control lives and why. The guiding principle is: **the model is an untrusted inference component, not a security boundary.**

## Layers from weakest to strongest

| # | Layer | Code location | Depends on model compliance? |
|---|---|---|---|
| 1 | Data/instruction separation in prompts | `app/security/prompt_guard.py` | **Yes**, so it is the weakest |
| 2 | Authorization outside model reach | `app/core/deps.py`, `app/retrieval/search.py` | No |
| 3 | Tool-boundary enforcement | `app/agent/tools.py` - `requires_roles` | No |
| 4 | Deterministic output filtering | `verify_egress` | No |
| 5 | Fail-closed behavior | Throughout the pipeline | No |

Layer 1 handles low-risk filtering. **No security decision relies on it alone.**

## Why authorization is not a tool parameter

The `search_documents` schema visible to the model contains `query`, `domain`, and `top_k`, and nothing else. `user_id` and `allowed_doc_ids` are injected at runtime through `ToolContext`.

This is the difference between asking the model not to do something and making it impossible for the model to do it. A poisoned document may persuade the model to request a salary table, but there is no code path through which that request can succeed.

## Write actions

| Status | Meaning |
|---|---|
| `completed` | Executed |
| `pending_approval` | Waiting for a human |
| `blocked` | Blocked by authorization or policy |
| `rejected` | Rejected by a human |
| `recommended` | Recommended but not executed |
| `failed` | Technical error |

**The agent never reports that it executed an action that has not completed.** Confidently false reporting is more serious than acknowledging uncertainty.

Additional rules:

- Authority is checked when the decision is made, not when the request is created.
- The requester cannot approve their own action.
- Amounts above `APPROVAL_HARD_CEILING` always require committee approval.

## Audit logging

`audit_log` is **append-only**. The application role should not have `UPDATE` or `DELETE` permissions:

```sql
REVOKE UPDATE, DELETE ON audit_log FROM rag_app;
```

The log records successful and failed logins, blocked access, tool execution, action requests, and decisions.

## PII

`app/security/pii.py` removes national IDs (with check-digit validation), credit cards (Luhn), IBANs, email addresses, and phone numbers before content is sent to the model.

Check-digit validation is important: without it, every nine-digit number, including request IDs, would be removed and answers would be damaged.

## Out of scope

- **Encryption at rest:** owned by the infrastructure layer.
- **JWT key rotation:** one key without `kid`; production requires JWKS.
- **Rate limiting:** model concurrency is limited, but per-user rate limiting is not implemented.
- **Multi-stage injection:** a document that activates an instruction only when combined with another document; this is a natural extension for the red-team suite.
