# ADR 0009 — Ship a deterministic model provider, and refuse to measure quality with it

**Status:** Accepted · **Date:** 2026-08-22 · **Deciders:** Raz K. · **Relates to:** ADR 0007

## Context

The system depends on a 7B generation model, a multilingual embedding model, and a cross-encoder reranker. Together they need roughly 10 GB of downloads and 16 GB of RAM. CI runners have neither, and a contributor cloning the repository should not need a GPU to run the tests.

But most of what this project claims is **not** model quality. Access control enforced in SQL, injection defenses that live outside the prompt, approval tiers derived from a document, RRF fusion, egress verification — all of these are architectural, and all of them are testable without a model that can write Hebrew.

## Decision

Ship a `stub` provider (`LLM_PROVIDER=stub`, `EMBEDDING_PROVIDER=stub`) that is fully deterministic:

- **Generation** echoes a bounded slice of the context and cites the first source id, so citation verification has something real to verify.
- **Structured output** returns a minimal object shaped to the requested JSON schema, so schema validation and routing execute.
- **Embeddings** are L2-normalized hashed bag-of-words vectors — texts sharing words land near each other, and nothing more.

And guard it in three places:

1. `app/main.py` refuses to start with a stub provider when `ENVIRONMENT` is not `dev`/`test`.
2. `app/ingestion/embedder.py` raises rather than writing stub vectors outside development.
3. `scripts/run_eval.py` exits unless `--allow-stub` is passed explicitly.

## Rationale

**The stub tests what the stub can test.** Whether `allowed_doc_ids` reaches the SQL filter, whether a poisoned document's marker escapes into an answer, whether a 4,200 ₪ refund resolves to team-lead approval citing §5.2 — none of these depend on how well a model writes. Running them on every commit is worth far more than running them nightly.

**The refusal to measure quality is the important half.** A hashed bag-of-words embedding has no semantic understanding. `Recall@5` measured against it describes lexical overlap, not retrieval quality, and publishing that number as if it meant something would be worse than not measuring at all. The explicit `--allow-stub` flag exists so that number can never be produced by accident — and the banner printed alongside says which metrics remain valid and which do not.

**It makes the provider boundary real.** A seam that only ever has one implementation is a seam in name only. Having a second implementation is what proves the gateway abstraction (ADR 0007) actually holds.

## Consequences

**Positive**

- CI runs the full integration suite — 100+ tests including permission sweeps and the injection suite — in under a minute, with no model downloads.
- Contributors can run everything on a laptop.
- The provider interface is genuinely exercised by two implementations.

**Negative**

- Answer quality, faithfulness, and hallucination rate are **not** covered by CI and must be measured nightly against Ollama. A regression in generation quality can merge.
- The stub can mask a prompt bug that only a real model would surface — for example, a system prompt the stub ignores because it never reads it.
- Two code paths to keep working, and a temptation to reach for the stub when the real provider is inconvenient.

**Mitigation:** the injection suite prints which metrics are invalid under the stub, and `false_refusal_rate` is not gated there — because a stub cannot produce a real answer, so a "false refusal" against it means nothing.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Tiny real model in CI** (e.g. a 0.5B) | Slow to download, still non-deterministic, and its "quality" numbers are as meaningless as the stub's — without the honesty of being obviously fake. |
| **Recorded fixtures (VCR-style)** | Brittle: any prompt change invalidates every cassette, and prompts change constantly in this project. |
| **Mocking at the call site in each test** | Duplicates mocking logic across the suite and never exercises the gateway itself. |
| **No CI for model-dependent paths** | Would leave the permission sweep and injection suite unrun on every commit — exactly the checks that matter most. |

## Revisit when

- A CI runner with a GPU becomes available, making a real small model viable for a subset of quality checks.
- Prompt-sensitive behavior starts regressing in ways the stub structurally cannot catch — that is the signal the seam has drifted too far from reality.
