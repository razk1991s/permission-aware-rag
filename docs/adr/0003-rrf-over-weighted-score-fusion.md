# ADR 0003 — Fuse hybrid search results with Reciprocal Rank Fusion, not weighted score averaging

**Status:** Accepted · **Date:** 2026-08-21 · **Deciders:** Raz K.

## Context

Retrieval runs two independent searches over the same permitted chunk set:

- **Vector search** — cosine similarity via pgvector, producing scores in roughly `[0, 1]`.
- **Lexical search** — `ts_rank_cd` over `tsvector`, producing scores on an unbounded, corpus-dependent scale (typically `0.0`–`1.5`, but not normalized and not comparable between queries).

The two ranked lists must be merged into a single candidate list of 30 before reranking.

## Decision

Merge with **Reciprocal Rank Fusion**:

```
rrf_score(d) = Σ over each list L containing d of  1 / (k + rank_L(d))
```

with **k = 60**, the constant from the original Cormack et al. paper, applied via a `FULL OUTER JOIN` between the two ranked CTEs.

## Rationale

**The scores are not comparable, and normalizing them is guesswork.** A cosine similarity of 0.84 and a `ts_rank_cd` of 0.61 carry no common meaning. Min-max normalization per query makes the top result always 1.0 regardless of whether it was excellent or merely least-bad, and it makes fusion unstable when one list returns few results.

**RRF ignores scores entirely and uses only rank position.** That removes the normalization problem by construction, and it removes a hyperparameter that would otherwise need tuning per corpus and per language.

**It degrades gracefully.** When a query is purely semantic ("what happens if a customer doesn't get their refund"), BM25 returns few or no matches and RRF simply reflects the vector ranking. When a query is a specific term ("chargeback", "FIN-006", "2.8%"), lexical search dominates. Neither case needs a special branch.

The `k = 60` constant dampens the influence of the top rank so that a document ranked #1 in one list and absent from the other does not automatically beat a document ranked #3 in both. It is not tuned; it is the published default, and tuning it is deferred until the evaluation harness can measure whether it matters.

## Consequences

**Positive**

- No score normalization, no weight to tune, no per-language calibration.
- Deterministic and cheap: one SQL statement, no extra round trip.
- Robust when one retriever returns an empty set.

**Negative**

- Discards magnitude information. A vector hit at 0.95 and one at 0.71 fuse identically if they rank #1 and #2. In practice the cross-encoder reranker (ADR 0004) recovers this, which is part of why reranking is not optional here.
- `k` is a magic number carried from a paper rather than derived from this corpus.

## Measurement

The evaluation harness records four configurations so the contribution of this decision is visible rather than asserted:

| Config | Recall@5 | MRR |
|---|---|---|
| v1 — vector only | _measure_ | |
| v3a — hybrid, weighted sum (min-max normalized) | _measure_ | |
| v3b — hybrid, RRF | _measure_ | |
| v4 — RRF + cross-encoder rerank | _measure_ | |

If v3a beats v3b on this corpus, this ADR is wrong and gets superseded. That is the point of measuring.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Weighted sum of normalized scores** | Requires choosing α, re-tuning per corpus, and unstable when one list is short. Kept as a measured comparison, not as the default. |
| **CombSUM / CombMNZ** | Same normalization problem as weighted sum. |
| **Learning-to-rank model** | Requires labeled training data that does not exist at this scale, and adds a model to maintain. |
| **Vector search only** | Fails on exact identifiers, amounts, and rare terms — precisely the queries an enterprise knowledge base receives. |

## Revisit when

- The evaluation shows weighted fusion outperforming RRF by more than 2 points of Recall@5.
- A third retriever is added (for example a metadata or graph retriever), where RRF extends naturally but the weights would multiply.
