# ADR 0001 — Use PostgreSQL + pgvector instead of a dedicated vector database

**Status:** Accepted · **Date:** 2026-08-21 · **Deciders:** Raz K.

## Context

The platform needs vector similarity search over ~30 enterprise documents (a few thousand chunks), and it must enforce **document-level access control** on every retrieval. It also needs lexical search (BM25), request tracing, evaluation results, and operational business data.

The obvious default in most RAG tutorials is a dedicated vector database — Qdrant, Weaviate, Pinecone, or Azure AI Search — alongside a relational database for everything else.

## Decision

Use **a single PostgreSQL 16 instance** with the `pgvector` extension for embeddings, `tsvector` for lexical search, and ordinary tables for ACL, traces, evaluations, and business data.

## Rationale

The deciding factor is not performance — it is **where the permission filter runs**.

With a separate vector store, the ACL check happens either:

- **before** the search — fetch the user's allowed document IDs from Postgres, pass them as a filter to the vector DB (a large `IN` list that grows with the corpus, and two systems that must agree on identity), or
- **after** the search — retrieve top-K, then discard unauthorized chunks. This is worse than it looks: the discarded chunks were already loaded into application memory, and the discard silently degrades ranking, because top-5 after filtering is not the top-5 of the permitted set.

With pgvector, the ACL is a CTE in the same query. The filter runs inside the engine, before ranking:

```sql
WITH allowed AS (
    SELECT DISTINCT a.document_id FROM document_acl a
    JOIN user_roles ur ON ur.role_id = a.role_id
    WHERE ur.user_id = :user_id
)
SELECT c.id, 1 - (c.embedding <=> :qvec) AS score
FROM chunks c
WHERE c.document_id IN (SELECT document_id FROM allowed)
ORDER BY c.embedding <=> :qvec LIMIT 30;
```

Secondary benefits: no synchronization layer between two datastores, no dual-write consistency problem on ingest and delete, one backup, one connection pool, one `docker compose` service instead of two.

## Consequences

**Positive**

- Access control is enforced by the database, not by application code that could be bypassed.
- Hybrid search (vector + BM25) fuses two result sets in one round trip instead of two network calls.
- Deleting a document removes its chunks, ACL rows, and vectors atomically in one transaction.

**Negative**

- pgvector's HNSW index is less tunable than a purpose-built engine, and lacks features such as native multi-tenancy partitioning and quantization.
- Vector search competes for the same connection pool and CPU as OLTP traffic. At this scale that is irrelevant; at scale it would need a read replica.
- Some hosted-vector-DB conveniences (managed reranking, built-in hybrid) must be implemented by hand — though implementing them is a stated goal of this project.

**Neutral**

- HNSW parameters (`m=16`, `ef_construction=64`) are defaults, not tuned. Tuning is deferred until the evaluation harness can measure the effect.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Qdrant** | Excellent filtering, but ACL still lives in a second system that must stay in sync with Postgres. |
| **Azure AI Search** | Managed hybrid search and semantic reranking out of the box, but adds cost and a cloud dependency to a project that must run fully local. Also removes the retrieval engineering this project exists to demonstrate. |
| **FAISS (in-process)** | No persistence, no filtering, no concurrency. Fine for a notebook, not for a service. |

## Revisit when

- The corpus exceeds roughly **1 million chunks**, where HNSW build time and memory in Postgres become the bottleneck.
- p95 retrieval latency exceeds 200 ms attributable to vector search specifically.
- Multi-tenancy requires physical isolation of one tenant's vectors from another's.
