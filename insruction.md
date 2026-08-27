# AI Project Instructions: Meridian Enterprise RAG

## Purpose

Meridian is a self-hosted enterprise Agentic RAG platform. It ingests heterogeneous business documents, retrieves authorized knowledge, answers with citations, executes governed read/write tools, and exposes selected capabilities through MCP.

When working on this repository, use this file as a compact project map and preserve the security and governance guarantees described below.

## Technology Stack

- Backend: Python 3.11+, FastAPI, PostgreSQL 16, pgvector, PostgreSQL full-text search.
- Agent: LangGraph-style workflow with explicit state, restricted tools, query catalog, approval gates, and validation.
- Frontend: Angular 19 standalone application using signals and lazy routes.
- Models: self-hosted providers behind an LLM gateway, including Ollama and a deterministic stub provider for CI.
- Operations: Docker Compose, PowerShell support on Windows, Make targets on Unix-like systems.
- Testing: pytest for Python and Karma/Angular tests for the UI.

## Repository Map

- `app/config.py`, `app/db.py`, `app/main.py`: application configuration, database access, and startup/routing.
- `app/api/`: HTTP endpoints for authentication, documents, chat, actions, traces, and UI delivery.
- `app/core/`: identity, dependency injection, authorization dependencies, and security helpers.
- `app/agent/`: graph orchestration, state, tools, query catalog, approval logic, and actions.
- `app/ingestion/`: parsing, cleaning, chunking, embedding, and ingestion pipeline.
- `app/retrieval/`: vector search, lexical search, reciprocal rank fusion, reranking, and retrieval orchestration.
- `app/rag/`: answer generation, citations, grounding, and refusal behavior.
- `app/security/`: PII handling and prompt-injection defenses.
- `app/llm/`: provider interfaces, providers, gateway routing, quotas, fallbacks, and audit concerns.
- `app/evaluation/`: metrics, regression gates, and evaluation runner.
- `mcp_server/`: MCP server using the same authorization path.
- `migrations/`: SQL schema and database migrations.
- `scripts/`: migration, corpus ingestion, vector indexing, evaluation, and injection-test entry points.
- `ui/src/app/`: Angular core services/models and feature areas for login, chat, approvals, traces, and documents.
- `data/corpus/`: source corpus, manifests, facts, seed documents, sample documents, and red-team documents.
- `data/eval/`: evaluation dataset.
- `docs/adr/`: architectural decision records; read the relevant ADR before changing a protected design choice.
- `tests/`: Python unit/integration/security tests and SPA routing tests.

## Request Flow

The main question path is:

`authentication and authorization -> understand -> route -> vector and lexical retrieval -> RRF -> rerank -> context -> generate -> validate`

The production UI is served by FastAPI from the Angular build. The frontend calls `/api/...`. `/api/*` belongs to the backend, `/health` and `/stats` are operational endpoints, and other paths are handled by the Angular router so deep links continue to work.

## Non-Negotiable Security Invariants

1. **Authorization is enforced in SQL.** Resolve the user's allowed document IDs from the authenticated identity and apply the ACL inside every retrieval query. Never retrieve broadly and filter unauthorized documents afterward.
2. **The model must not control authorization.** ACL scope is outside the agent's tool schemas and must not be accepted as a model-provided argument.
3. **No free-form SQL from the agent.** Database access uses named, typed queries from the query catalog. Reject unknown query names and unexpected parameters; do not silently ignore invalid input.
4. **Tool authorization is enforced at the tool boundary.** A prompt or model decision is never an authorization mechanism.
5. **Write actions require policy-driven approval.** Approval thresholds are derived from the relevant procedure documents, retained with evidence, and bounded by the hard ceiling. Retrieval may make a decision stricter, never weaker.
6. **Citations must be grounded.** Every citation in an answer must refer to a retrieved chunk that was actually supplied to generation.
7. **Prompt injection defenses are architectural.** Keep data separate from instructions, constrain tools, enforce authorization outside the model, and apply deterministic output validation.
8. **Do not leak protected data through answers, traces, errors, documents, MCP, or UI state.** Check authorization at every relevant boundary.
9. **Security and retrieval evaluation gates must remain meaningful with the stub provider.** Do not weaken blocking gates merely because generation is deterministic in CI.

## Engineering Rules

- Start from the narrowest owning module, symbol, failing test, or call site.
- Prefer existing repository patterns and public APIs over new abstractions.
- Keep changes small and focused; do not reformat unrelated code.
- Preserve behavior across both development and production UI serving modes.
- Treat database schema, authorization, tool schemas, approval records, and API contracts as compatibility-sensitive.
- Use structured parsing and typed validation instead of ad hoc string manipulation where practical.
- Add focused tests for behavioral changes, especially authorization, injection resistance, citations, approval transitions, and SPA routing.
- Do not expose secrets, tokens, credentials, or full sensitive document contents in logs or responses.
- Follow the existing Ruff configuration: Python target 3.11 and line length 100.
- Keep new source files ASCII unless the domain requires otherwise. Avoid unnecessary inline comments.
- Do not commit changes unless explicitly requested.

## Useful Commands

On Windows, use `make.ps1` equivalents:

```powershell
.\make.ps1 install
.\make.ps1 demo
.\make.ps1 test
.\make.ps1 ui-test
.\make.ps1 eval
.\make.ps1 eval-gate
.\make.ps1 injection
```

Typical local setup:

```powershell
Copy-Item .env.example .env
docker compose up -d
.\make.ps1 demo
.\make.ps1 ui-build
.\make.ps1 dev
```

For a fast model-free check:

```powershell
$env:LLM_PROVIDER = "stub"
$env:EMBEDDING_PROVIDER = "stub"
$env:ENVIRONMENT = "test"
.\make.ps1 demo
.\make.ps1 test
```

The full local UI is usually available at `http://localhost:8000` after building it. During UI development, use the Angular dev server at `http://localhost:4200` with the configured proxy.

## Validation Expectations

Before finishing a change:

1. Run the narrowest relevant test or check first.
2. Run the broader Python tests when backend behavior or shared contracts changed.
3. Run UI tests when Angular behavior or routing changed.
4. Run security/injection tests for security-sensitive changes.
5. Run evaluation gates when retrieval, authorization, grounding, or agent behavior changes.
6. Inspect the diff and report any unavailable infrastructure, skipped integration tests, or unrelated pre-existing failures.

## Architectural References

Use these ADRs when the change touches the corresponding concern:

- ADR 0001: PostgreSQL/pgvector over a dedicated vector database.
- ADR 0002: Authorization in SQL, not post-filtering.
- ADR 0003: Reciprocal rank fusion over weighted score fusion.
- ADR 0004: Hebrew full-text search with the simple configuration.
- ADR 0005: Query catalog over text-to-SQL.
- ADR 0006: Policy-driven approval gates.
- ADR 0007: Local models behind an LLM gateway.
- ADR 0008: Application-owned approval state rather than graph checkpointer state.
- ADR 0009: Deterministic provider for CI.
- ADR 0010: Angular SPA served by FastAPI.

## How an AI Assistant Should Work Here

When a request arrives:

1. Identify the concrete file, symbol, failing behavior, test, or command involved.
2. Read the nearest implementation and the most relevant neighboring test or ADR.
3. State a concise hypothesis about the control path and choose a cheap check that could disprove it.
4. Make the smallest change that tests the hypothesis.
5. Immediately run focused validation, then expand validation according to risk.
6. In the final response, summarize changed files, validation performed, and any remaining limitations.

When uncertain about a security-sensitive behavior, prefer denying access or requiring approval until the intended policy is confirmed by code, tests, schema, or an ADR.
