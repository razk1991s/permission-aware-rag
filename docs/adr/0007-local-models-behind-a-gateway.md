# ADR 0007 — Run local open models, behind a gateway that makes the provider replaceable

**Status:** Accepted · **Date:** 2026-08-21 · **Deciders:** Raz K.

## Context

The system needs an LLM for generation, an embedding model, a cross-encoder reranker, and a cheap model for query understanding and groundedness judging. The default choice would be a hosted API (OpenAI, Anthropic, Azure OpenAI).

Two constraints shape the decision. First, the corpus is Hebrew, and the target domain is financial services — a sector where sending internal procedures to a third-party API is a compliance conversation, not a technical one. Second, the project must be reproducible by anyone who clones it, with no API key and no cost.

## Decision

Run everything locally through Ollama and `sentence-transformers`:

| Role | Model | Notes |
|---|---|---|
| Generation | `qwen2.5:7b-instruct` | Strong tool-calling for its size; acceptable Hebrew |
| Query understanding / judging | `qwen2.5:3b-instruct` | Cheap, structured output only |
| Embeddings | `bge-m3` (1024-dim) | Multilingual — Hebrew and English share one vector space |
| Reranking | `bge-reranker-v2-m3` | Multilingual cross-encoder, CPU-viable on 30 candidates |

**Every model call goes through a single `LLMGateway`** that resolves model by task, redacts PII, enforces per-user quota, records tokens/cost/latency, and falls back to a secondary model on timeout.

## Rationale

**Multilingual embeddings are not optional here.** A monolingual English embedding model on Hebrew text produces a vector space where unrelated Hebrew documents cluster together simply for being Hebrew. `bge-m3` places Hebrew and English content in a shared space, which matters because the corpus mixes them inside single documents.

**The gateway is the actual decision.** Local models are the *current* configuration; the gateway is what makes that reversible. Switching to Azure OpenAI is a registry entry, not a refactor — which is the honest answer to "but production would use a hosted model": yes, and this architecture assumes so.

**Task-based routing has a specific purpose beyond cost.** Groundedness judging must not use the same model that produced the answer. A model asked to evaluate its own output systematically over-approves. Routing the judge to a different model (or at minimum a different prompt with no access to the original reasoning) is a correctness requirement, not an optimization.

**Cost accounting is kept even though cost is zero.** `estimated_cost` is recorded per request against a price table. Locally it is 0.00; the value is that the moment a hosted provider is configured, cost-per-request is already instrumented rather than being discovered on the first invoice.

## Consequences

**Positive**

- No API key, no cost, no data leaving the machine. `git clone && docker compose up` produces a working system.
- Latency is predictable and unaffected by third-party rate limits.
- Demonstrates the full serving stack rather than hiding it behind an API call.

**Negative**

- **Answer quality is below GPT-4-class models**, particularly for nuanced Hebrew and multi-step reasoning. This must be stated plainly in the README rather than glossed over — the project demonstrates architecture and measurement, not frontier model quality.
- Requires ~16 GB RAM. On CPU-only hardware, generation runs at 5–15 tokens/second, so p95 latency lands in the 5–10 second range.
- The local model is a single shared resource: concurrent requests must be serialized behind a semaphore, so throughput is limited by design.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Azure OpenAI** | The likely production choice for an Israeli financial institution, and the gateway is designed for it. Rejected as the default because it makes the project unrunnable without a subscription. |
| **OpenAI API directly** | Same reproducibility problem, plus a data-residency conversation the target domain does not permit. |
| **Hosted model + local embeddings** | A reasonable hybrid, but splits the "no external calls" story in half and complicates the demo narrative. |
| **Smaller model (3B) for everything** | Tool-calling reliability degrades noticeably below 7B, and the agent depends on it. |

## Revisit when

- Evaluation shows answer correctness below 70% with retrieval measured as correct — indicating the generator, not the retriever, is the bottleneck.
- The project is deployed for real use rather than demonstration, at which point the gateway routes to Azure OpenAI and this ADR is superseded rather than reversed.
