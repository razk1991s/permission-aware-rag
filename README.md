# Meridian - Enterprise Agentic RAG Platform

Meridian is a fully local enterprise knowledge platform with hybrid retrieval, reranking, SQL-enforced document access control, a restricted-tool agent, policy-driven approval gates, and automated regression evaluation.

It is built with Python/FastAPI and Angular, using self-hosted open models. The platform ingests heterogeneous documents, performs hybrid retrieval with cross-encoder reranking, enforces document-level authorization in SQL, executes governed write actions behind human approval gates, exposes tools over MCP, and includes prompt-injection defenses, tracing, quotas, model routing, and audit trails.

## Running the project

```bash
cp .env.example .env
docker compose up -d
make install
make ui-install
# Place the corpus package in data/corpus; expected path: data/corpus/manifest.json
make demo
make ui-build
make dev
```

The first `make demo` run may take several minutes because bge-m3 is about 2.3 GB. For a model-free check:

```bash
LLM_PROVIDER=stub EMBEDDING_PROVIDER=stub ENVIRONMENT=test make demo test
```

The full setup requires 16 GB RAM. `make dry-run` needs much less. On Windows, use `make.ps1` with the same targets; see [docs/RUN-WINDOWS.md](docs/RUN-WINDOWS.md).

### Development and production

| | Development | Production |
|---|---|---|
| Run | `make dev` + `make ui-dev` in two terminals | `make ui-build`, then `make dev` |
| URL | `http://localhost:4200` | `http://localhost:8000` |
| UI delivery | Angular `ng serve` with HMR and a proxy | FastAPI serves `ui/dist/browser` |
| CORS | Enabled for port 4200 | Disabled; one origin |

Angular calls `/api/...` in both modes. `/api/*` belongs to the server, `/health` and `/stats` are health endpoints, and all other paths belong to the Angular Router. This preserves deep links such as `/traces/<uuid>`.

## Demo scenarios

Open `http://localhost:8000` (or port 4200 in development). The login screen provides demo users.

1. Ask a knowledge question as Yuval (Finance) and inspect the cited answer and trace.
2. Compare HR and Finance access to salary data; authorization is enforced in SQL.
3. Request a 4,200 ILS refund as Maya (Customer Service), then approve it as an authorized Finance user.
4. Ask a combined procedure-and-data question and inspect the separate retrieval and tool steps.
5. Ask Maya for salary data and verify that the restricted tool is rejected visibly.

## Architecture

```text
Angular 19 SPA -> FastAPI -> ingestion, agent, retrieval, RAG, security, LLM gateway
                                      |
                       PostgreSQL 16 + pgvector + full-text search
```

Question flow: `authz -> understand -> route -> vector and lexical search -> RRF -> rerank -> context -> generate -> validate`.

## Security invariants

- Authorization is resolved from the authenticated identity and enforced inside every retrieval SQL query. Unauthorized documents are never retrieved and filtered afterward.
- ACL scope is outside the agent's tool schemas; the model cannot provide or alter it.
- Database access uses named, typed queries from the query catalog. The agent never writes free-form SQL.
- Tool authorization is enforced at the tool boundary.
- Write actions are persisted as `pending_approval` when policy approval is required. Approval thresholds come from procedures and cannot weaken the hard ceiling.
- Every citation must refer to a retrieved chunk supplied to generation.
- Prompt-injection defenses combine data/instruction separation, restricted tools, external authorization, and deterministic output validation.

See the relevant architectural decision records in [docs/adr](docs/adr/).

## Repository structure

- `app/`: FastAPI backend, agent, ingestion, retrieval, RAG, security, LLM, and evaluation.
- `ui/`: Angular 19 standalone application.
- `mcp_server/`: MCP server using the same authorization path.
- `migrations/`: PostgreSQL schema and migrations.
- `scripts/`: migration, ingestion, indexing, evaluation, and injection-test commands.
- `tests/`: Python, security, integration, and SPA routing tests.
- `data/corpus/`: fictional source corpus, manifests, seed, sample, and red-team documents.
- `data/eval/`: evaluation dataset.

## Evaluation and tests

```bash
make test
DATABASE_URL=... LLM_PROVIDER=stub make test
make ui-test
make eval
make eval-gate
make injection
```

Security and retrieval gates block regardless of the provider. Generation-dependent metrics are reported separately when using the deterministic stub. Do not treat stub output as a quality measurement unless `--allow-stub` is supplied. See [docs/EVALUATION.md](docs/EVALUATION.md).

## MCP

```bash
MCP_TOKEN=$(curl -s localhost:8000/api/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"yuval@meridian.local","password":"Demo1234!"}' | jq -r .access_token) make mcp
```

MCP derives identity from the token and calculates permissions from the database, using the same path as the API.

## Known limitations

- Local 7B model quality is below larger hosted models, especially for complex Hebrew.
- CI uses a deterministic provider; generation quality is measured separately in nightly evaluation.
- In-memory quotas reset on restart; production should use a database table or Redis.
- HNSW parameters are not yet calibrated against the evaluation suite.
- Full UI E2E coverage is not available yet.
- Run `make ui-build` after UI changes to avoid serving a stale build.

All corpus content is fictional and exists for technical demonstration only.
