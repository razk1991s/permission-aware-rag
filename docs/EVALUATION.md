# Evaluation

## What is measured and why

| Metric | Type | Frequency | Gate | Blocks with stub? |
|---|---|---|---|---|
| `permission_leak_rate` | Deterministic | Every PR | **0** - blocking | **Yes** |
| `injection_success_rate` | Deterministic | Every PR | **0** - blocking | **Yes** |
| `recall@5` | Deterministic | Every PR | >=0.70, drop <=0.03 from baseline | **Yes** |
| `mrr` | Deterministic | Every PR | >=0.50 | **Yes** |
| `citation_accuracy` | Generation-dependent | Nightly | >=0.95 | No |
| `refusal_accuracy` | Generation-dependent | Nightly | >=0.80 | No |
| `false_refusal_rate` | Generation-dependent | Nightly | <=0.05 | No |
| `missed_refusal_rate` | Generation-dependent | Nightly | <=0.20 - reported | No |
| `hallucination_rate` | LLM-as-judge | Nightly | <=0.15 - reported | No |
| `p95_latency_ms` | Operational | Nightly | <=15,000 - reported | No |

Retrieval metrics are inexpensive and deterministic, so they are merge gates. Generation metrics are noisy, so they are reported rather than blocking.

### The last column is the important one

CI runs with the stub provider (ADR 0009). A gate that depends on what the model chooses to say, such as whether it refused or cited correctly, measures stub behavior rather than the system. `Gate.requires_generation` therefore disables blocking for that gate in stub runs while still printing its value so regressions remain visible.

The stub must never weaken `permission_leak_rate` or `injection_success_rate`. These are determined by SQL and enforcement code rather than wording, so they always block. `tests/test_gates.py` locks this behavior in place.

## Metrics that must be measured together

`injection_success_rate` alone is not meaningful: a system that refuses every question can score zero while being useless. Therefore `false_refusal_rate` is always measured alongside it.

The same distinction separates `permission_leak_rate` (sensitive data was exposed - a security failure) from `missed_refusal_rate` (the system answered without exposing data - a behavioral bug). Combining them makes both numbers meaningless.

## Dataset

The dataset contains 44 items across six categories. Ground truth is derived from `FACTS.md` in the corpus package rather than by rereading documents, keeping the test independent from system output.

| Category | Items | What is tested |
|---|---:|---|
| `knowledge` | 26 | Retrieval and correct facts |
| `data` | 3 | Tool execution against data |
| `hybrid` | 3 | Procedure and data together |
| `permission` | 7 | Must refuse without leaking |
| `unanswerable` | 4 | Must refuse |
| `versioning` | 1 | Expired documents are not retrieved |

`relevant_docs` and `relevant_sections` are identified at document and section level rather than by `chunk_id`, so the dataset survives re-ingestion.

## Configurations

| Config | Hybrid | Rerank | Multi-query | Generation |
|---|---|---|---|---|
| `v1-vector-only` | No | No | No | No |
| `v2-hybrid` | Yes | No | No | No |
| `v3-hybrid-rerank` | Yes | Yes | No | No |
| `v4-multiquery` | Yes | Yes | Yes | No |
| `v5-full` | Yes | Yes | Yes | Yes |

The first four configurations measure retrieval only, without a generation model, so they are inexpensive and deterministic. Items marked `requires_generation` are skipped because no model is available to answer them.

## Results

The table is populated by `make eval`. Do not fill it with estimates, here, in the README, or on a resume.

| Config | Recall@5 | MRR | CtxPrec | Correct | Refusal | Leak | p95 ms |
|---|---|---|---|---|---|---|---|
| v1-vector-only | | | | | | | |
| v2-hybrid | | | | | | | |
| v3-hybrid-rerank | | | | | | | |
| v4-multiquery | | | | | | | |
| v5-full | | | | | | | |

### Analysis after the run

- What contributed the most, and by how much?
- Did anything hurt performance? Why?
- Where does the system still fail, and in which category?

A negative result is still a result. If hybrid search did not improve performance, the likely explanation is that the corpus is semantic rather than term-driven, which is a stronger interview answer than an unexplained upward graph.
