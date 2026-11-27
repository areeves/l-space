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

- [x] **D1. Database.** *Decided:* support both SQLite and Postgres behind the store
  interface. Start with SQLite (WAL); add the Postgres implementation later (phase 5).
  Porting notes: `instr` -> `strpos`; `AUTOINCREMENT` -> identity column; `ORDER BY title`
  needs `COLLATE "C"` on Postgres to match SQLite's binary order (and the Python oracle). The
  casefolded columns stay computed in Python, so matching semantics do not change. `data` stays
  plain TEXT on both (nothing queries into it any more, and JSONB would not preserve the exact
  bytes that make rebuilds reproducible), so no JSONB is needed. With D9 the Postgres
  "implementation" is mostly the same SQLAlchemy-based store pointed at a Postgres engine, plus
  the dialect-specific pieces above.
- [x] **D2. Ownership key and data model.** *Decided:* `user_id` on `books` (one library per
  user), and every user owns their own copy of every book: no shared catalog. One user's bad
  data can never affect another's; per-user state (`my_rating`, `finished`, `minutes_left`)
  stays on the same row as the book; tenant isolation stays a single filter. Costs: duplicated
  `data` blobs (see D6) and possibly inconsistent spellings of the same book across users.
  *Forward compatibility:* `source_id` (the ASIN) is the join key if ownership-based
  recommendations are ever wanted. At that point add a plain index on `source_id` (the planned
  indexes lead with `user_id`) and, optionally, a derived and rebuildable consensus catalog
  that users can never edit. Privacy and abuse constraints on that are in section 9. Revisit
  `libraries` / `library_id` only if sharing libraries becomes a goal.
- [x] **D3. Authentication.** *Decided:* password + argon2; login returns a random opaque
  session token sent as `Authorization: Bearer ...`; the server stores only a hash of each
  token in a `sessions` table, so sessions can be revoked. Signup is admin-created or
  invite-code, never open, so strangers cannot spend your model time. No UI exists yet and
  Swagger UI is the first client; the bearer scheme gives it a working Authorize button and
  removes CSRF handling. Cookie transport on top of the same `sessions` table is deferred
  until a same-origin web UI wants HttpOnly cookies. Not chosen: JWT (hard to revoke), OAuth /
  SSO (external accounts, conflicts with self-hosted / offline use).
- [x] **D4. `/ask` execution model.** *Decided:* synchronous `POST /ask` behind a gate with a
  small bounded queue (429 with `Retry-After` when full). Not building the job + poll API now:
  inference on real hardware should be a fraction of today's CPU time, so the wait should be
  short, and building B first would be premature optimisation. Revisit with data (queue wait,
  429 rate, total latency; see phase 3). Streaming is not planned: the grounding check
  validates the finished draft, so token streaming would show unverified text.
- [x] **D5. Public identifiers.** *Decided (relies on D7's upsert import):* each book
  gets an opaque random `public_id`, generated when the row is first inserted, unique per user.
  Routes address books by `(user_id, public_id)`; another user's id returns 404, the same as
  an unknown id. The internal integer `id` is never exposed. `source_id` (the ASIN) is shown
  as an ordinary field in the response body, not used in URLs: it is a vendor identifier, so
  building URLs on it would assume every library comes from Audible, and it is user-supplied
  text. A `source` field can sit beside it if other import sources are ever added. Responses
  use a dedicated `BookOut` schema, not the domain `Book`. The id is only stable across
  re-imports if import updates existing rows (D7); with replace-all it would change on every
  import.
- [x] **D6. `Book.raw`.** *Decided:* keep `raw` inside the `data` blob, as today. Measured on
  a real export of about 300 books: `raw` is about 77% of the stored blob (roughly 4 KB per
  book, about 1.3 MB per library). A synthetic benchmark of that shape put parsing a whole
  library at about 3 ms with `raw` and about 1.5 ms without, so there is no speed problem, and
  moving `raw` to its own table would not reduce total storage. Nothing in the app reads
  `raw` (the loader fills it, `/library/books` excludes it, two tests assert on it), and D5's
  `BookOut` keeps it out of responses, so splitting it out later stays a contained migration.
  Revisit if database size or backups start to matter (roughly hundreds of users), or to
  retain less personal data per account. Likely moves, in order: compress the stored text,
  then split it into a `book_raw` table loaded on demand. Dropping it would lose the ability
  to re-derive fields after a parser fix without asking users to re-upload.
- [x] **D7. Import semantics.** *Decided:* upsert + delete-missing, replacing replace-all. In
  one transaction, per user, rows are matched by `source_id`: changed rows are updated in
  place (keeping `id` and `public_id`), identical rows are skipped, new rows are inserted, and
  books missing from the CSV are deleted. The import returns added / updated / unchanged /
  removed counts. Why: D5's `public_id` needs rows to survive re-imports; skipping unchanged
  rows lets the library version (the phase 3 cache key) change only when something really
  changed; and stable rows leave room for per-book user state (the README's planned
  "correct the recommendations"). No soft-delete until there is per-book state to lose.
- [x] **D8. Connection strategy on SQLite.** *Decided:* a SQLAlchemy engine (D9) with a pool
  of one (`pool_size=1, max_overflow=0`), so database access is serialized. Rationale: SQLite
  here is for development and demos, each database call takes milliseconds, and the real
  bottleneck is the model, so serialization costs nothing measurable and removes the need for
  `BEGIN IMMEDIATE`, WAL and pool tuning. Rules: (1) connection scope is one store call or one
  transaction, never a whole request: a request holding the only connection across the
  `/ask` queue wait or the model call would block everything, including login and `/health`;
  (2) the connect hook sets `PRAGMA foreign_keys=ON` and a `busy_timeout`, because the CLI and
  `migrate` are separate processes on the same file; (3) keep the database on a named Docker
  volume and back it up with SQLite's online backup. Cost: serialization hides race
  conditions that would appear under Postgres's real concurrency, so rely on database
  constraints and atomic updates (not check-then-insert) and keep concurrency tests. Moving to
  a real pool later is configuration plus WAL and `BEGIN IMMEDIATE`; Postgres uses a normal
  pool anyway.
- [x] **D9. Query layer.** *Decided:* SQLAlchemy 2.0 **Core** (tables and query expressions,
  not ORM entities, not SQLModel) plus **Alembic** for migrations, introduced in phase 0.5,
  before the tenancy work. Rationale: the data layer is query-heavy and `Book` is already a
  Pydantic model, so entity mappings would only duplicate it; the first real migration (the
  tenancy change) should not be written in the hand-rolled `user_version` scheme and then
  redone; and the Postgres port shrinks to dialect-specific pieces. The `LibraryStore`
  interface stays, for the list-backed fake and tests.

---

## 5. Work plan

Each phase is meant to be one or a few reviewable PRs against `multi-user`.

### Phase 0: Preparation

- [x] Decide D1-D9 and record them (all recorded in section 4).
- [ ] Test scaffolding: a fixture that creates two users, each with a distinct library.
- [ ] README positioning note (see section 1), drafted on this branch only.

### Phase 0.5: Port the data layer to SQLAlchemy Core and Alembic (D9)

A behaviour-preserving refactor with no new features. The existing differential store tests
and schema tests are the safety net. It lands before phase 1 so the tenancy SQL and its
migration are written once.

**Steps.** Each step is a PR against `multi-user`, from a branch named `mu/05-N-...` (git
cannot create `multi-user/...` branches while a branch called `multi-user` exists). Merge each
PR before starting one that depends on it. Every step keeps the whole suite green. The
checklist after this list is grouped by step.

1. [x] `mu/05-1-schema-as-code`: dependencies; the tables as `Table` objects; a schema parity
   test against the hand-written schema.
2. [x] `mu/05-2-engine`: `Settings.database_url`; engine factory with D8's pool of one and the
   connect hook; tests. Independent of step 1.
3. [x] `mu/05-3-store-on-core`: `SqlLibraryStore(engine)` for the write path, hydration and
   schema init (including the legacy rebuild, now transactional: the engine emits `BEGIN`
   itself so DDL can roll back); queries still run as SQL text; `get_engine(settings)` shares
   one engine per database URL; the hand-written `SCHEMA` and the step-1 parity test are gone
   (the store creates tables from `tables.py`; a test checks a database written by the
   previous release opens untouched); `SqliteLibraryStore` kept as an alias.
4. [x] `mu/05-4-query-expressions`: `build_where` returns a SQLAlchemy expression; a
   dialect-aware `contains()` (`instr` on SQLite, `strpos` on Postgres, never LIKE);
   `title_order()` (`COLLATE "C"` on Postgres); the store's queries are built with `select()`;
   compile tests for both dialects (Postgres is compiled, not yet run).
5. [x] `mu/05-5-alembic`: Alembic baseline `0001` (hand-written, inside the package so it
   ships in the wheel); `l-space migrate`; `Settings.auto_migrate` (default on) runs it at app
   and CLI start, and when off the app refuses to start on an out-of-date database; the whole
   migration is one transaction; `user_version` logic and `ensure_schema` removed; the store no
   longer touches the schema. **Deviation:** a pre-Alembic database (v2 or older) is not
   stamped but adopted by rebuilding from `data` into the baseline (ids and blobs unchanged),
   because the old hand-written schema has unnamed constraints that SQLite batch migrations
   cannot alter later. The `SqliteLibraryStore` alias is gone (tests use `tests/stores.py`).
6. [ ] `mu/05-6-cleanup`: query-plan check, parametrized store fixture, docs.

Order: steps 1 and 2 in parallel (both add the same dependency lines, so they merge
cleanly), then 3, then 4 and 5 (independent of each other), then 6.

- [x] Add dependencies: `sqlalchemy>=2` and `alembic`. The Postgres driver (`psycopg`) waits
  for phase 5, but write everything dialect-aware now.
- [x] **Schema as code.** Define `books`, `book_people`, `book_series`, `book_tags` once as
  SQLAlchemy `Table` objects on a shared `MetaData` (new module, for example
  `library/tables.py`): same columns, the `role` CHECK, `ON DELETE CASCADE` foreign keys and
  the indexes from the current schema. `data` is plain `Text`.
- [x] **Engine in one place (D8).** `Settings.database_url` (default
  `sqlite:///data/l-space.db`; keep `db_path` working for existing `.env` files and compose,
  then deprecate). SQLite uses a pool of one (`pool_size=1, max_overflow=0`, with a sensible
  `pool_timeout`), so access is serialized. Connection scope is one store call or one
  transaction, never a whole request. A connect hook sets `PRAGMA foreign_keys=ON` and
  `busy_timeout`; WAL and `BEGIN IMMEDIATE` wait until real concurrency is needed. Handle
  `:memory:` for tests (`StaticPool`).
- [ ] **Port the store.** `SqliteLibraryStore` becomes a store built on an engine (for
  example `SqlLibraryStore`), using Core `insert` / `select` / `delete`. `index_rows()` stays
  the single place that derives columns and child rows. Keep the `SqliteLibraryStore` name
  as an alias until the tests and callers are updated.
- [ ] **`build_where` returns a SQLAlchemy expression**, not `(sql, params)`: `exists()`
  subqueries over the child tables, comparison expressions on the numeric columns. Substring
  matching goes through one helper (`instr(...) > 0` on SQLite, `strpos(...) > 0` on
  Postgres, via a dialect-specific compile rule), always against the pre-casefolded columns
  and a needle normalised with `str.casefold().strip()`.
- [ ] **Ordering helper.** One `title_order()` used everywhere `ORDER BY title` appears, so
  Postgres can apply `COLLATE "C"` later without touching callers. On SQLite it stays binary.
- [x] **Alembic.** `alembic.ini` and `migrations/` reading the URL from `Settings`;
  `render_as_batch=True` for SQLite. Baseline revision `0001` is the current v2 schema.
- [x] **`l-space migrate`** (upgrade to head). Detect existing databases: a v2 database
  (`PRAGMA user_version = 2`, no `alembic_version`) is stamped at `0001`; a pre-columns
  database (`user_version` 0) first goes through the existing rebuild-from-`data` upgrade,
  kept as a one-time step. The app no longer creates or rebuilds the schema in `__init__`.
  *(Done differently: every pre-Alembic database is rebuilt into the baseline; see step 5.)*
- [x] Test fixtures create the schema with `metadata.create_all` (fast), plus one test that
  Alembic's head matches the metadata (no drift) and one that exercises the real migration
  path on a v2 file database and on a legacy database.
- [x] Adapt `tests/test_store_schema.py`: the derived-columns invariant and the cascade and
  re-import tests stay; the legacy-rebuild and atomic-rebuild tests become migrate tests.
- [ ] Parametrize the store fixture over database back ends (SQLite only for now; Postgres
  is added in phase 5).
- [ ] **Acceptance:** the existing suite passes with only import and fixture changes; ruff is
  clean; `EXPLAIN QUERY PLAN` on SQLite still uses the same indexes for the same queries; no
  API behaviour change.
- Out of scope: async engine (decide in phase 3), the Postgres implementation, tenancy.
- Risks: the adoption step writes with the current `insert_books`, so once a revision changes
  `books` it must write the baseline shape itself or be retired (noted in `migrate.py`).
  SQLite `:memory:` and connection pooling quirks; Alembic autogenerate quirks on
  SQLite; keeping the substring helper's behaviour identical to the pure-Python oracle.

### Phase 1: Tenancy in the data layer (the core change)

- [ ] **Schema.** `users` table (`id`, unique casefolded `username`, `password_hash`,
  `created_at`, `is_admin`, `disabled`). `books.user_id NOT NULL REFERENCES users(id) ON
  DELETE CASCADE`. Replace `UNIQUE(source_id)` with `UNIQUE(user_id, source_id)`, and add `books.public_id`
  (random, generated on first insert, `UNIQUE(user_id, public_id)`; see D5). Lead the
  indexes with `user_id`: `(user_id, title)` can also serve `ORDER BY title` without the temp
  sort, `(user_id, duration_minutes)`, `(user_id, my_rating)`. Child tables keep `book_id` and
  inherit ownership.
- [ ] **Migration.** An Alembic revision (after phase 0.5's baseline). A single-user database
  upgrades by creating one default owner and assigning all existing books to it, with no data
  loss (see section 8).
- [ ] **Scoped store API.** `LibraryStore.for_user(user_id)`; `replace_all` is replaced by an
  upsert import (next item); `all_books`, `filter_books`, `count_books` all scoped. There
  should be no store method that reads books without a scope.
- [ ] **Upsert import (D7).** In one transaction, per user: match CSV rows to existing books
  by `source_id`; update changed rows in place (keeping `id` and `public_id`), skip rows whose
  stored `data` blob is identical, insert new rows with a fresh `public_id`, delete the user's
  books that are absent from the CSV (child rows cascade); an updated book's child rows are
  replaced. Duplicate ASINs within one CSV: the last row wins, as today. Return an
  `ImportResult` with added / updated / unchanged / removed counts. Bump the user's library
  version only when something changed.
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
- [ ] **API identifiers (D5).** A `BookOut` response schema: no integer `id`, no `raw`;
  `public_id` and `source_id` as plain fields. Book routes take `public_id` and go through the
  scoped store; another user's `public_id` returns 404.
- [ ] **Isolation tests** (the most important tests in the project): two users with the same
  ASIN; one user's import does not touch the other's books or child rows; every route returns
  only the caller's data; deleting a user cascades to books and child rows; no scoped method
  can be called without a user.
- [ ] Defense in depth (Postgres phase): row-level security on `books` keyed on a session
  setting.

### Phase 2: Authentication and import

- [ ] `core/security.py`: argon2 password hashing, constant-time comparison, minimum password
  policy.
- [ ] Endpoints: `POST /auth/register` (gated by D3: invite code or admin-created), `POST
  /auth/token` (form-encoded, FastAPI's `OAuth2PasswordRequestForm`, so Swagger UI's Authorize
  button works; returns a random opaque token), `POST /auth/logout` (revokes the session),
  `GET /me`. Store only a hash of each token in a `sessions` table (user, created, expires,
  last used, revoked). Issue a new token on every login; expire idle and old sessions. Login
  throttling per username and per client address. No CSRF handling is needed while auth is
  header-only.
- [ ] Swagger UI: declare `OAuth2PasswordBearer(tokenUrl="auth/token")` as the auth
  dependency so protected routes show a padlock and the Authorize button logs in. Add
  `python-multipart` (the form login needs it, and so does the CSV upload). Make `/docs`,
  `/redoc` and `/openapi.json` configurable so they can be turned off outside development;
  `persistAuthorization` only in development.
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

- [ ] Concurrency tests (D8): concurrent registrations of the same username create exactly
  one account; concurrent imports for one user serialize correctly and for two users both
  succeed. Rely on unique constraints and atomic updates, not check-then-insert.

### Phase 3: Gate the model

- [ ] **`LlmGate`.** A semaphore sized from the model server's real parallelism (default 1)
  around `provider.generate`, a small bounded wait queue (2-3 deep), and a maximum wait.
  Overflow returns 429 with `Retry-After`. At most one in-flight `/ask` per user, so one user
  cannot fill the queue. A cache hit is answered before the gate and never queues.
- [ ] **One entry point.** The route calls a single function (for example
  `ask_service.run(user, question)`) that does cache lookup, gate, pipeline and cache store.
  A job worker could later call the same function, so the job API (below) would not mean
  rewriting the pipeline.
- [ ] Stop tying up threadpool workers: async endpoint with `httpx2.AsyncClient`, or an
  explicit worker pool. Replace the single 300 s timeout with separate connect and read
  timeouts, and warm the model at startup so the long timeout is not needed for normal
  requests.
- [ ] **Rate limits.** In-memory token bucket per user, plus a daily quota stored in the
  database.
- [ ] **Answer cache.** Key: `(user_id, library_version, normalized question, writer model,
  router model)`. `library_version` is bumped by an import only when it changed something (D7). Plans are deterministic at
  temperature 0, so caching is sound; do not cache router errors or fallback answers without
  deciding that deliberately.
- Deferred (D4): `POST /ask` returns 202 with a job id and `GET /ask/{id}` polls; jobs table;
  cancellation; restart recovery (jobs running at a restart are marked failed). Build only if
  the measurements below show a real problem. Pick the thresholds when there is data, for
  example a p95 queue wait or a 429 rate that users would notice.
- [ ] Tests with a `FakeProvider` that blocks: the gate admits N and rejects the rest;
  queue-wait timeout; fairness between users; quota enforcement. Also: while an `/ask` is held
  in the gate, other requests still reach the database (no connection is held across the
  queue wait or the model call; D8).
- [ ] Observability: add `queue_wait` to `timings_ms`; log queue depth; count 429
  rejections and record total request latency, so the decision about the job API is made from
  data.

### Phase 4: Application lifecycle and API hygiene

- [ ] FastAPI lifespan: build the engine or connection factory once and keep it on
  `app.state`; delete the per-request `LibraryStore.from_settings`.
- [ ] **Migrations out of the request path.** Alembic and `l-space migrate` exist from phase
  0.5. Here: run migrate at deploy time (compose startup) before the app serves, and make the
  app refuse to serve when the database is not at the current revision.
- [ ] Pagination on `GET /library/books` (limit/offset or keyset on `(title, id)`) with a
  maximum page size.
- [ ] Settings: secret key, database URL, CORS origins, allowed hosts, signup mode, quotas.
  Secrets only from the environment. Update `.env.example`.
- [ ] CORS allowlist, security headers, trusted-proxy handling, request-id middleware,
  structured logs. Do not log question text or library contents.
- [ ] `DELETE /me` (cascades) and `GET /me/export`.

### Phase 5: Packaging and deployment

- [ ] Postgres service in `compose.yaml` and a Postgres driver. Point the phase 0.5 engine at
  it and add the dialect-specific pieces (`strpos` substring, upsert, `COLLATE "C"` ordering).
  Verify the Alembic revisions run on Postgres. **Run the existing differential store tests
  against both databases** (parametrized fixture). That is what the pure-Python oracle was
  built for.
- [ ] Reverse proxy with TLS (for example Caddy), or documented plain-HTTP dev mode.
- [ ] Split the Dockerfile into a dev/test image and a runtime image (the runtime image
  currently installs `.[dev]` and copies `tests/` and `evals/`); run as non-root; add a
  healthcheck; pin the Ollama image tag.
- [ ] Do not publish Ollama's port 11434 to the host.
- [ ] Backup instructions for the database volume, using SQLite's online backup
  (`VACUUM INTO` or the backup API) rather than a plain file copy. Keep the SQLite database on
  a named Docker volume, not a bind mount.
- [ ] CI: pytest and ruff, on both databases once Postgres exists.

### Phase 6: Frontend (out of scope here)

Notes for whoever builds it: the OpenAPI schema is the contract; auth is bearer-token
(D3; add cookie transport if a same-origin UI wants HttpOnly cookies); CORS (phase 4); expect `/ask` latency of many seconds and design for a pending state.

---

## 6. Security checklist (cross-cutting)

- [ ] Tenant isolation proven by tests, not by convention (phase 1).
- [ ] No object is addressed by a guessable id without an ownership check (D5).
- [ ] Auth required on every route except an explicit public allowlist, enforced by a test.
- [ ] Upload limits: size, row count, field size, encoding; no server paths in errors.
- [ ] Rate limits and quotas on login and on `/ask`.
- [ ] Passwords: argon2, throttled login, hashed and revocable session tokens; CSRF
  protection only if cookie transport is added later.
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
- Migration tests: a v2 single-user database upgrades through the Alembic revisions with all
  books owned by the default user, ids and `data` blobs intact; Alembic's head matches the
  SQLAlchemy metadata (no drift).
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
  Using *other* users' libraries (ownership co-occurrence on ASIN, see D2) needs opt-in
  consent and minimum aggregation thresholds. It must also assume the ASIN is user-supplied:
  fake libraries could be submitted to skew results, so require minimum support before an
  ASIN counts.
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
2. Phase 0.5 SQLAlchemy Core and Alembic port (medium; behaviour-preserving, guarded by the
   existing tests, and it makes later phases cheaper).
3. Phase 1 tenancy and scoped store (large; the foundation, and the part most reusable if the
   experiment is abandoned, since it also removes the whole-library loads).
4. Phase 2 auth and upload (medium).
5. Phase 3 model gate (medium; this is where the design risk is).
6. Phase 4 lifecycle and hygiene (medium).
7. Phase 5 Postgres and deployment (smaller now that the store is dialect-aware; optional for
   a first demo).
