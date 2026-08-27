# ADR 0010 — Angular SPA served by FastAPI from a single origin

**Status:** Accepted · **Date:** 2026-08-23 · **Deciders:** Raz K. · **Relates to:** ADR 0002

## Context

The system needs a UI for four things that cannot be demonstrated with `curl`:

1. **The same question asked by two different users returns different answers.** This is the central claim of ADR 0002, and it is only convincing when you watch the role switch and the answer change.
2. **The trace viewer.** Stage latencies, the candidate table, and the Δ column showing how far the reranker moved each chunk — this is what makes the retrieval pipeline inspectable rather than asserted.
3. **The approval preview.** Enter an amount, see which tier applies and which clause of FIN-001 it came from.
4. **Blocked tool calls.** When the agent tries a tool the user's roles do not permit, the refusal has to be visible, not buried in a log.

The first version of the UI was a server-rendered page. It worked, but it was not the language of the team this project is meant to represent, and a trace viewer with live sorting and expandable rows is genuinely awkward without a component framework.

## Decision

Build the UI as an **Angular 19 SPA** in `ui/`, and have **FastAPI serve the build output** as static files from the same origin.

- Standalone components throughout — no `NgModule`. Signals for component state, `inject()` over constructor injection, the new `@if` / `@for` control flow.
- Every feature route is lazy (`loadComponent`), guarded by a functional `CanActivateFn`.
- Auth is a functional `HttpInterceptorFn`: attach the bearer token, log out on 401 — except on `/auth/login` itself, so a bad password shows an error instead of a redirect.
- **One namespace boundary, enforced:** `/api/*` belongs to the server, `/health` and `/stats` are also mounted at the root for container health checks, and **everything else belongs to the Angular Router**.
- In development, `ng serve` on 4200 proxies `/api` to 8000 untouched — no path rewriting, so the URL the browser requests is the URL the server sees in both modes.
- In production, `ng build` writes `ui/dist/browser` and a catch-all route serves it: an existing file is returned as a file, a missing path *with* an extension is a 404, and anything else returns `index.html`.
- CORS middleware is added **only** when `ENVIRONMENT` is `dev` or `test`.

## Rationale

**Angular because that is the working language.** Raz writes Angular daily. A portfolio project defended in an interview should be built in the stack the candidate can actually answer questions about, and "why signals instead of a BehaviorSubject" is a conversation worth being able to have.

**One origin because it removes an entire class of configuration.** No CORS in production, no preflight, no cookie `SameSite` puzzle, no second deployable. One container, one port, one health check.

**The `/api` prefix is not cosmetic — it is the whole reason this works.** The first version registered every route twice, unprefixed *and* under `/api`, so that scripts would not need the prefix. That broke immediately: `/traces` and `/documents` are Angular routes too, so refreshing on `/traces/<uuid>` matched the API route and returned a `401` JSON body instead of the application. A deep link into a trace is exactly what you send someone when you want them to look at a specific retrieval, so this was not cosmetic. One namespace shared by two consumers is a bug waiting for a URL to collide; the prefix is the boundary. The three test clients now use `base_url=".../api"`, which is a one-line change and keeps every test path readable.

**The catch-all is hand-written rather than `StaticFiles(html=True)`.** `html=True` only falls back to `index.html` for *directory* paths — `/chat` and `/traces/abc` still 404, which is the one case the fallback exists for. The explicit route also lets three things be correct that the mount cannot express: an unknown `/api/...` path stays a JSON 404 instead of becoming HTML the client tries to parse as JSON; a missing hashed chunk returns 404 rather than an HTML body with a JavaScript MIME type; and `index.html` is served `no-cache` while hashed assets are `immutable` for a year — safe precisely because the hash changes on every build.

**Client-side roles are display state only.** `AuthService.roles()` hides buttons. It decides nothing. The server re-resolves permissions from the database on every request (ADR 0002), so editing `localStorage` changes what a user sees and not what they receive. `auth.service.spec.ts` and `api.service.spec.ts` encode this: the client never sends `user_id` or a document allowlist, because a client-supplied allowlist is a forgeable one.

## Consequences

**Positive**

- Node is needed only to build, not to run — the Dockerfile's first stage compiles Angular and the runtime image carries only `dist/browser`, so `node_modules` never reaches production.
- The dev loop keeps HMR without weakening the production configuration.
- 17 Angular unit tests run headless in CI alongside the Python suite, and `tests/test_spa_routing.py` pins the namespace split — including an assertion that the unprefixed API routes never come back.

**Negative**

- Two toolchains and two lockfiles in one repository, and a Node version to keep current.
- A stale `ui/dist` silently serves an old UI. Mitigated: when `dist` is absent the server returns a placeholder page naming `make ui-dev` and `make ui-build` rather than a bare 404. Not mitigated when `dist` is merely *old* — there is no freshness check.
- The catch-all at `/{full_path:path}` must stay last in `main.py`; anything registered after it is unreachable. This is load-bearing and commented as such.
- Every non-API path now returns 200 with the app shell, including genuine typos. That is inherent to client-side routing — the router shows its own 404 — but it does mean the server can no longer tell you a URL was wrong.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Keep the server-rendered pages** | Cheapest, but the trace viewer and the role-switch demo are the two things worth showing, and both fight the format. |
| **Separate Angular deployment (nginx + API)** | Production-realistic, but adds CORS, a second container, and a second thing to get wrong — for a demo that is meant to run with one command. |
| **React or Svelte** | Fine choices; wrong ones here. The point is to be defensible in an interview, and that means the framework used daily. |
| **Angular SSR** | Real cost (a Node process in production), no benefit — the app is behind a login, so there is nothing to render for a crawler. |

## Revisit when

- The UI needs to be deployed independently of the API, or served from a CDN — at that point the single-origin trade flips and CORS becomes worth its cost.
- Angular gains stable zoneless change detection in a version worth upgrading to; the app is already signal-based, so the migration should be small.
