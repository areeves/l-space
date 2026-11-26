# Multi-user rework (experimental)

**Status:** planning. Nothing in this document is implemented yet.

**Branch policy:** `main` stays single-user. All of this work happens on the `multi-user`
branch, which was cut from `main` at `f9b3d35` (after PR #4: `BookFilter`, column-backed
SQLite store, derived columns and child tables). Nothing here is promised to merge. Merge
`main` into `multi-user` regularly so the branches don't drift; the likely conflict hot spots
are `library/store_sqlite.py`, `api/routes.py` and `ask/executor.py`.

Tick boxes as work lands. Record decisions in section 4 as they are made.

---

## 1. Goal and non-goals

**Goal.** Turn l-space from a single-user local tool into the backend of a multi-user web
application: several accounts, each with a private library, served by one self-hosted
deployment (Docker Compose) that shares one local language model.

**Non-goals for this experiment:**

- A frontend (only an API contract that one could be built against).
- OAuth / SSO, e-mail verification, password reset by e-mail.
- Sharing libraries between users, households, teams.
- Billing, hosted LLM providers, horizontal scaling (more than one app worker).
- The planned recommendations feature (but see the risks in section 9: it will need to be
  per-user too).

**Positioning.** The README's principles are "Local first, free only" and "Your data stays
yours". A multi-user deployment is still free and self-hosted, but "local" now means "the
server you run", and "yours" means "your account, isolated from other accounts". Reword the
README when (and if) this merges.

---

## 2. Audit: where the code is single-user today

| Area | Where | Problem |
|---|---|---|
| One global library | `library/store_sqlite.py` (`books` has no owner; `source_id UNIQUE`; `replace_all` runs `DELETE FROM books`) | Two users owning the same ASIN collide; one user's import wipes everyone's library. |
| No identity | `api/routes.py`, `main.py` | Every route is open. |
| Server-path import | `POST /library/import?path=...` in `api/routes.py` | Reads an arbitrary file path on the server. Fine for a local tool, an arbitrary file read once strangers can call it. |
| Store per request | `get_store` -> `LibraryStore.from_settings` -> new `SqliteLibraryStore` for every call | Opens a connection and runs `_ensure_schema` (including a possible full rebuild from `data`) inside a user's request. |
| Whole-library loads | routes call `all_books()`; `/ask` passes `books` to `answer_question`; `ask/executor.py` `execute(plan, books)` runs the pure functions in `library/queries.py`; `cli.py` does the same | Per request, per user, the whole library is read and parsed. PR #4's `filter_books`/`count_books` are not used by the app yet. |
| Synchronous model calls | `models/provider.py` `OllamaProvider.generate` (blocking `httpx2.post`, timeout from `Settings.ollama_timeout`, default 300 s); `ask` is a plain `def` route | Each `/ask` holds a worker thread for the whole call (two calls: router, then writer). Measured p50 about 18 s per question on CPU. A handful of simultaneous users exhausts the threadpool, and CPU Ollama effectively serves one generation at a time. |
| Settings | `core/settings.py` | No secret key, no database URL, no CORS / allowed hosts, no signup mode, no quotas. |
| CLI | `cli.py` | Operates on "the" library; `import` "replaces the current library". |
| Deployment | `compose.yaml`, `Dockerfile` | App + Ollama only, `./data` bind mount, Ollama's port 11434 published to the host; the image installs `.[dev]` and copies `tests/` and `evals/` into the runtime image. |
| Unpaginated listing | `GET /library/books` | Returns the entire library. |
| Raw rows stored verbatim | `Book.raw`, in `books.data` | "Originals preserved" means long publisher blurbs are stored per user; storage grows per account. |
| Import parser limits | `ingestion/loaders.py` sets `csv.field_size_limit(10**9)` at import time | Reasonable for a trusted local file, a memory-abuse vector for uploads. |

**Things that already help:** the `LibraryStore` ABC (a seam for new backends), the
`ModelProvider` protocol, FastAPI `Depends` seams, `Settings` from the environment,
deterministic query execution, the pure-Python `queries.py` as a differential-test oracle, and
`FakeProvider` for tests.

---

## 3. Target shape

```
client -> reverse proxy (TLS) -> FastAPI app (one worker)
                                   |-- auth dependency -> current user
                                   |-- UserLibrary (store scoped to that user) -> database
                                   `-- LLM gate (queue, limits, cache) -> Ollama
```

- **Scoped store.** `store.for_user(user_id)` returns an object whose every method is already
  restricted to that user, so a query cannot forget the filter. Admin and migration code uses
  the unscoped store explicitly.
- **LLM gate.** The one place that decides whether a question may run now, later, or not at
  all.
- **One app worker.** The in-process limiter is only global with a single worker. Ollama is the
  shared resource, so more app workers do not add capacity anyway.

---

## 4. Decisions to make

Mark each as decided and record the outcome.

- [ ] **D1. Database.** SQLite (WAL) first, or Postgres from the start?
  *Recommendation:* do tenancy on SQLite (it is database-agnostic work), move to Postgres in
  phase 5. Porting notes: `instr` -> `strpos`; `data` JSON -> JSONB; hand-rolled
  `PRAGMA user_version` rebuild -> Alembic; `AUTOINCREMENT` -> identity column; the casefolded
  columns stay computed in Python, so matching semantics do not change.
- [ ] **D2. Ownership key.** `user_id` on `books` now, or a `libraries` table with `library_id`?
  *Recommendation:* `user_id` (one library per user). Revisit only if sharing becomes a goal.
- [ ] **D3. Authentication.** Password + argon2 with a cookie session, bearer JWT, or OAuth?
  *Recommendation:* password + argon2, HttpOnly signed-cookie (or server-side) session if the
  first client is a same-origin web UI; JWT only for a cross-origin SPA or API clients. Signup
  is invite-code or admin-created, never open, so strangers cannot spend your model time.
- [ ] **D4. `/ask` execution model.** Synchronous behind a bounded queue (429 when full), a
  job + poll API, or streaming?
  *Recommendation:* synchronous with the gate first (phase 3), jobs only if the latency
  hurts.
- [ ] **D5. Public identifiers.** Do not expose integer row ids. Address books by `source_id`
  within the user's library (or a UUID).
- [ ] **D6. `Book.raw`.** Keep, compress, or drop? (Affects per-user storage and every
  `data` blob.)
- [ ] **D7. Import semantics.** Replace-all (today), or upsert + delete-missing so ids stay
  stable across re-imports? ALE exports are full snapshots, so the end state is the same;
  stable ids matter for caching and any future per-book user state.
  *Recommendation:* upsert + delete-missing.
- [ ] **D8. Connection strategy on SQLite.** One connection per request from a factory (WAL +
  `busy_timeout`) versus one shared connection with a lock. Today `check_same_thread=False`
  is set on a per-request connection.

---

## 5. Work plan

Each phase is meant to be one or a few reviewable PRs against `multi-user`.

### Phase 0: Preparation

- [ ] Decide D1-D8 above and record them.
- [ ] Test scaffolding: a fixture that creates two users, each with a distinct library.
- [ ] README positioning note (see section 1), drafted on this branch only.

### Phase 1: Tenancy in the data layer (the core change)

- [ ] **Schema.** `users` table (`id`, unique casefolded `username`, `password_hash`,
  `created_at`, `is_admin`, `disabled`). `books.user_id NOT NULL REFERENCES users(id) ON
  DELETE CASCADE`. Replace `UNIQUE(source_id)` with `UNIQUE(user_id, source_id)`. Lead the
  indexes with `user_id`: `(user_id, title)` can also serve `ORDER BY title` without the temp
  sort, `(user_id, duration_minutes)`, `(user_id, my_rating)`. Child tables keep `book_id` and
  inherit ownership.
- [ ] **Migration.** `SCHEMA_VERSION` 3. A single-user database upgrades by creating one
  default owner and assigning all existing books to it, with no data loss (see section 8).
- [ ] **Scoped store API.** `LibraryStore.for_user(user_id)`; `replace_all` becomes
  `DELETE ... WHERE user_id = ?` plus insert, in one transaction; `all_books`, `filter_books`,
  `count_books` all scoped. There should be no store method that reads books without a scope.
- [ ] **Use the store, not lists.** `execute(plan, store)` and
  `answer_question(question, store, ...)` replace the `books: list[Book]` parameter (this
  also touches `evals/run.py`, `tests/test_ask_pipeline.py` and `tests/test_ask_executor.py`,
  which all pass a `books` list today). `/ask`'s empty-library
  check becomes `count_books()`, not `all_books()`.
- [ ] **Push down the remaining operations**, each with a differential test against the
  pure-Python oracle: `narrator_counts` and `author_counts` as `GROUP BY` over `book_people`
  (ties ordered like `_ranked`: count descending, then name, binary order), `in_progress` as
  `finished = 0`. Keep `series_gaps` in Python (set logic over positions and omnibus ranges)
  but read only the series rows it needs. Preserve existing semantics: `series_gaps` does not
  exclude non-audiobook formats.
- [ ] **Isolation tests** (the most important tests in the project): two users with the same
  ASIN; one user's import does not touch the other's books or child rows; every route returns
  only the caller's data; deleting a user cascades to books and child rows; no scoped method
  can be called without a user.
- [ ] Defense in depth (Postgres phase): row-level security on `books` keyed on a session
  setting.

### Phase 2: Authentication and import

- [ ] `core/security.py`: argon2 password hashing, constant-time comparison, minimum password
  policy.
- [ ] Endpoints: `POST /auth/register` (gated by D3: invite code or admin), `POST
  /auth/login`, `POST /auth/logout`, `GET /me`. Rotate the session on login. Cookie flags:
  `HttpOnly`, `Secure`, `SameSite=Lax`. CSRF protection on state-changing routes if cookie
  auth is chosen. Login throttling per username and per client address.
- [ ] A `current_user` dependency on every `/library/*` and `/ask` route. `/health` stays
  public.
- [ ] **Upload import.** `POST /library/import` takes a multipart file. Enforce a size cap
  while streaming (for example 20 MB), sanity-check type and encoding, parse from a temp file,
  take a per-user import lock, and never echo server paths in errors.
- [ ] Make the CSV field-size limit a loader parameter (or cap it for uploads) instead of
  setting `10**9` globally at import time; also cap row count.
- [ ] Remove the path-based import from the API. Keep a path-based import in the CLI as an
  admin tool.
- [ ] CLI: `create-user`, `import --user`, and the other commands take a user.
- [ ] Tests: every route in `app.routes` except an explicit public allowlist returns 401
  without credentials (enumerate the routes so a new route cannot be forgotten); wrong-user
  access; login throttling; session rotation.

### Phase 3: Gate the model

- [ ] **`LlmGate`.** A semaphore sized from the model server's real parallelism (default 1)
  around `provider.generate`, a bounded wait queue, and a maximum wait. Overflow returns 429 or
  503 with `Retry-After`.
- [ ] Stop tying up threadpool workers: async endpoint with `httpx2.AsyncClient`, or an
  explicit worker pool. Replace the single 300 s timeout with separate connect and read
  timeouts, and warm the model at startup so the long timeout is not needed for normal
  requests.
- [ ] **Rate limits.** In-memory token bucket per user, plus a daily quota stored in the
  database.
- [ ] **Answer cache.** Key: `(user_id, library_version, normalized question, writer model,
  router model)`. `library_version` is bumped on import. Plans are deterministic at
  temperature 0, so caching is sound; do not cache router errors or fallback answers without
  deciding that deliberately.
- [ ] Optional (D4): `POST /ask` returns 202 with a job id, `GET /ask/{id}` polls (or SSE);
  jobs table; cancel when the client disconnects.
- [ ] Tests with a `FakeProvider` that blocks: the gate admits N and rejects the rest;
  queue-wait timeout; fairness between users; quota enforcement.
- [ ] Observability: add `queue_wait` to `timings_ms`; log queue depth.

### Phase 4: Application lifecycle and API hygiene

- [ ] FastAPI lifespan: build the engine or connection factory once and keep it on
  `app.state`; delete the per-request `LibraryStore.from_settings`.
- [ ] **Migrations out of the request path.** `l-space migrate` runs at deploy time (or once at
  startup before serving). The app refuses to serve on a schema version mismatch. The current
  rebuild-on-open in `SqliteLibraryStore._ensure_schema` is right for a local tool and wrong
  for a web service; keep it only behind the explicit migrate command.
- [ ] Pagination on `GET /library/books` (limit/offset or keyset on `(title, id)`) with a
  maximum page size.
- [ ] Settings: secret key, database URL, CORS origins, allowed hosts, signup mode, quotas.
  Secrets only from the environment. Update `.env.example`.
- [ ] CORS allowlist, security headers, trusted-proxy handling, request-id middleware,
  structured logs. Do not log question text or library contents.
- [ ] `DELETE /me` (cascades) and `GET /me/export`.

### Phase 5: Packaging and deployment

- [ ] Postgres service in `compose.yaml`; a `PostgresLibraryStore` behind the same interface;
  Alembic migrations. **Run the existing differential store tests against both
  implementations** (parametrized fixture). That is what the pure-Python oracle was built for.
- [ ] Reverse proxy with TLS (for example Caddy), or documented plain-HTTP dev mode.
- [ ] Split the Dockerfile into a dev/test image and a runtime image (the runtime image
  currently installs `.[dev]` and copies `tests/` and `evals/`); run as non-root; add a
  healthcheck; pin the Ollama image tag.
- [ ] Do not publish Ollama's port 11434 to the host.
- [ ] Backup instructions for the database volume.
- [ ] CI: pytest and ruff, on both databases once Postgres exists.

### Phase 6: Frontend (out of scope here)

Notes for whoever builds it: the OpenAPI schema is the contract; decide cookie vs token
(D3); CORS (phase 4); expect `/ask` latency of many seconds and design for a pending state.

---

## 6. Security checklist (cross-cutting)

- [ ] Tenant isolation proven by tests, not by convention (phase 1).
- [ ] No object is addressed by a guessable id without an ownership check (D5).
- [ ] Auth required on every route except an explicit public allowlist, enforced by a test.
- [ ] Upload limits: size, row count, field size, encoding; no server paths in errors.
- [ ] Rate limits and quotas on login and on `/ask`.
- [ ] Passwords: argon2, throttled login, session rotation, CSRF if cookies.
- [ ] CORS allowlist; secrets from the environment only; no secrets in the image.
- [ ] Logs contain no question text, titles or other library content.
- [ ] Prompt injection: imported titles and blurbs reach the writer prompt. With correct
  scoping the blast radius is the user's own data, and the grounding check stays in place.
  Re-verify that nothing from another user can enter a prompt.
- [ ] LLM cost control: queue bounds, quotas, answer cache.
- [ ] Account deletion removes all of a user's rows.
- [ ] Ollama is not reachable from outside the compose network.

---

## 7. Testing strategy

- Keep the pure-Python `queries.py` as the oracle; every pushed-down operation gets a
  differential test (the pattern in `tests/test_store_filters.py`), parametrized over store
  implementations once there is more than one.
- Isolation tests (phase 1) and the route-enumeration auth test (phase 2).
- Concurrency tests for the gate with a blocking fake provider (phase 3).
- Migration tests: a v2 single-user database upgrades to v3 with all books owned by the
  default user, ids and `data` blobs intact.
- Evals: `evals/run.py` and `tests/test_evals.py` assume one library; give them a test user.

---

## 8. Migrating an existing single-user install

- Back up the database file first.
- `l-space migrate` bumps the schema to v3, creates the owner account (name from a CLI flag or
  prompt), and assigns all existing books to it. No re-import is needed because `books.data`
  remains the source of truth and the derived columns are rebuilt from it.
- Document the one-way nature of the migration, or ship a downgrade that drops `user_id` for
  single-owner databases.

---

## 9. Risks and open questions

- **The model is the capacity limit.** On CPU at about 18 s per question, a few active users
  already queue. Mitigations: gate, cache, smaller router model, honest 429s. No amount of
  code changes the hardware ceiling.
- **Positioning** versus "local first" (section 1).
- **SQLite write contention.** Imports serialize; with WAL and short imports this is likely
  fine for a small user base, and it is the main reason to move to Postgres.
- **User-supplied CSV is untrusted input.** Parser hardening is part of phase 2, not an
  afterthought.
- **Branch drift.** Merge `main` into `multi-user` often.
- **Recommendations (planned feature)** will need per-user signals and per-user caches.
- **Sharing libraries** would change D2; decide before building features that assume
  `user_id` is the only owner.

---

## 10. Definition of done for the experiment

- [ ] Two accounts can register (under the chosen signup policy), import, and ask questions
  without ever seeing each other's data.
- [ ] Every protected route requires authentication; enforced by a test.
- [ ] `/ask` cannot be used to exhaust the server: bounded queue, per-user limits, honest
  errors.
- [ ] A single-user database migrates to multi-user without data loss.
- [ ] The test suite, including isolation and differential tests, is green.
- [ ] README and deployment docs describe the multi-user mode.

## Suggested order and rough size

1. Phase 0 decisions (small).
2. Phase 1 tenancy and scoped store (large; the foundation, and the part most reusable if the
   experiment is abandoned, since it also removes the whole-library loads).
3. Phase 2 auth and upload (medium).
4. Phase 3 model gate (medium; this is where the design risk is).
5. Phase 4 lifecycle and hygiene (medium).
6. Phase 5 Postgres and deployment (medium to large; optional for a first demo).
