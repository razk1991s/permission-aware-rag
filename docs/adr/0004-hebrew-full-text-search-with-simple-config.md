# ADR 0004 — Use the `simple` text-search configuration plus trigrams for Hebrew lexical search

**Status:** Accepted · **Date:** 2026-08-21 · **Deciders:** Raz K.

## Context

The corpus is predominantly Hebrew, with English terms mixed in (`chargeback`, `KYC`, product names). The lexical half of hybrid retrieval (ADR 0003) uses PostgreSQL full-text search.

**PostgreSQL ships no text-search configuration for Hebrew.** There is no Hebrew stemmer, no Hebrew stop-word list, and no dictionary. This matters more than it first appears, because the failure is silent: `to_tsvector('english', 'נוהל זיכויים')` does not raise an error. It returns tokens, they are simply wrong — the English stemmer strips suffixes that mean nothing in Hebrew and applies English stop-words to Hebrew text. Retrieval quality drops and nothing in the logs says why.

Hebrew also inflects heavily by prefix: definite article ה, conjunction ו, prepositions ב/ל/מ/כ attach directly to the word. "זיכוי", "הזיכוי", "לזיכוי", and "וזיכויים" are four surface forms of one concept, and no stemmer available in Postgres handles them.

## Decision

1. Index Hebrew content with **`to_tsvector('simple', content)`** — tokenization without stemming or stop-word removal — in a generated column with a GIN index.
2. Add a **`pg_trgm` GIN index** on the raw content to recover morphological variants and typos that `simple` misses.
3. For bilingual documents, maintain a second `tsvector` using the `english` configuration and take the larger of the two ranks.
4. Document this explicitly in `README.md`, because it is a non-obvious property of the stack.

```sql
tsv_simple TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
tsv_en     TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
```

## Rationale

`simple` is honest: it does exactly one thing (split on token boundaries, lowercase) and gets that right for Hebrew. Applying an English stemmer to Hebrew is not a partial solution, it is a corruption — and one that is invisible without a Hebrew-language evaluation set.

Trigram matching covers what `simple` gives up. It matches "הזיכוי" against "זיכוי" on shared character sequences without needing any linguistic knowledge, and it tolerates the spelling variation common in Hebrew business writing (full vs. defective spelling — "זכוי" / "זיכוי").

The cost of losing stemming is partly absorbed by the architecture: this is the *lexical* half of a hybrid system. Semantic variation is the vector retriever's job, and `bge-m3` handles Hebrew morphology in embedding space. Lexical search here exists to catch exact identifiers, amounts, and rare terms — cases where `simple` is not merely adequate but actually preferable, since stemming would damage them.

## Consequences

**Positive**

- No silent quality loss from a mismatched language configuration.
- Exact-match queries (`FIN-006`, `2.8%`, `R-8842`) work correctly, since nothing is stemmed away.
- The approach extends to any language Postgres does not support.

**Negative**

- No stop-word removal, so the index is larger and very common Hebrew words carry weight they do not deserve. `ts_rank_cd`'s IDF component mitigates this partially.
- Trigram indexes are large — roughly the size of the text itself — and slower to build.
- Recall on morphological variants depends on trigram similarity thresholds that need tuning against the evaluation set.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **`to_tsvector('english', …)`** | Silently wrong. The failure mode that motivated this ADR. |
| **HebMorph / custom Postgres dictionary** | The linguistically correct answer, but it means compiling and maintaining a Postgres extension inside the Docker image — significant operational cost for a portfolio project, and a build that reviewers cannot reproduce easily. |
| **Elasticsearch with a Hebrew analyzer** | Better Hebrew support, but adds a second datastore and reintroduces the ACL-synchronization problem that ADR 0001 exists to avoid. |
| **Drop lexical search, vector only** | Loses exact identifier matching, which is a large share of real enterprise queries. |

## Revisit when

- A maintained Hebrew dictionary becomes installable as a plain Postgres extension without a custom image build.
- Evaluation shows the lexical arm contributing less than 3 points of Recall@5 over vector-only — at which point its complexity may not be worth keeping.
