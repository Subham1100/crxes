# crxes.app

Multi-agent AI bug prediction. Connect a log source (Datadog, CloudWatch, GCP,
Sentry, Loki, webhook), and four agents run in sequence — Log Parser → Pattern
Detector → Root Cause Analyzer → Bug Predictor — to forecast what's about to break.

## Local ports

The defaults from the spec (5432, 6379, 8000, 3000) are all taken on this
machine by another project, so crxes uses shifted ports:

| Service  | Port |
| -------- | ---- |
| Postgres | 5433 |
| Redis    | 6380 |
| Backend  | 8002 |
| Frontend | 3002 |

## Setup

The backend and its datastores run in Docker. The frontend runs on the host,
where Fast Refresh is faster than it would be through a bind-mounted container.

### 1. Secrets

Both env files are gitignored, so create them from the examples and fill in the
values — see [Generating secrets](#generating-secrets). Compose will not start
without `backend/.env`.

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
```

### 2. Backend, Postgres, Redis

```bash
docker compose up --build
```

One command: it builds the API image, waits for Postgres and Redis to pass their
healthchecks, runs `alembic upgrade head`, then starts uvicorn with `--reload`.
Source is bind-mounted, so edits under `backend/` reload live.

The first build takes a few minutes while pip resolves the boto3/aioboto3 pin
documented in `requirements.txt`. Later starts reuse the cached layer and take
seconds — and drop `--build` unless dependencies changed.

### 3. Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Verify: `curl localhost:8002/api/health/` → `{"status":"ok","database":"ok"}`,
and http://localhost:3002 serves the landing page.

## Everyday commands

```bash
docker compose up -d               # start detached
docker compose logs -f backend     # follow the API log (Ctrl+C only detaches)
docker compose stop                # stop, keeping data
docker compose down -v             # stop and DELETE the database volumes
docker compose exec backend bash   # shell inside the API container

docker compose exec backend alembic upgrade head   # apply migrations without a restart
```

Pull someone else's schema change — or write your own — and that last command is
the one you need; code reloads on save, the schema does not. See
[Migrations](#migrations).

### Running the backend natively instead

Still supported, and useful for attaching a debugger:

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/uvicorn main:app --reload --port 8002
```

Start only the datastores first with `docker compose up -d postgres redis`. The
`DATABASE_URL` and `REDIS_URL` in `backend/.env` already point at the shifted
host ports, which is what a native process needs — compose overrides them with
in-network addresses only for the containerized backend.

## Auth

Email + password, owned by the backend. `POST /api/auth/signup` and
`/api/auth/login` hash/verify with bcrypt against `users.password_hash`, then set
an httpOnly `crxes_session` cookie holding a JWT signed with `NEXTAUTH_SECRET`.

The cookie is host-only on `localhost`, so the browser sends it to both the API
(:8002) and the Next server (:3002) — `lib/session.ts` forwards it back to
`/api/auth/me` to resolve the user in server components. In production set
`SESSION_COOKIE_DOMAIN=.crxes.app` and `SESSION_COOKIE_SECURE=true`.

| Route              | Method | Purpose                                  |
| ------------------ | ------ | ---------------------------------------- |
| `/api/auth/signup` | POST   | Create account, set session cookie       |
| `/api/auth/login`  | POST   | Verify password, set session cookie      |
| `/api/auth/logout` | POST   | Clear the cookie                         |
| `/api/auth/me`     | GET    | Current user, or 401                     |

Pages: `/signup`, `/login`, `/dashboard`, and `/analyze` behind the session.
OAuth (GitHub, Google) lands in Phase 2 and will write to the same `users` table
with `password_hash` left null — the provider buttons are on the forms, disabled.

## Ingest (Phase A)

```
files ──▶ 1 detect ──▶ 2 parse ──▶ 3 normalize ──▶ 4 redact ──▶ Entry[]
```

`ingest/` turns pasted text or uploaded files into `Entry` objects. Everything
after it — enrichment, correlation, digest, root-cause localization — consumes
`Entry` and never touches raw text again. Adding a log format means adding a
parser; no later stage learns about it. Deterministic and side-effect free: same
bytes in, same entries out, no network and no model.

**Detect** (`ingest/detect.py`) scores every candidate format over the same
sample and takes the highest, rather than first-match — Postgres and plain ISO
lines share a prefix, and CRI is a timestamp wrapped around anything. It returns
a confidence, and the UI shows it as the guess it is. A user override
short-circuits it.

**Parse** (`ingest/parsers/`) — one module per format:

| Format | Source | What it gives correlation |
| ------ | ------ | ------------------------- |
| `json` | OTLP (envelope + flat), GCP, Datadog, ECS, bunyan/pino, Docker | real `trace_id`/`span_id` |
| `plain` | anything human-readable | bracketed logger as the service |
| `logfmt` | Go, Heroku, Grafana stack | `trace_id`, `request_id` |
| `clf` | nginx, Apache, ALB | status, latency, `X-Request-ID` |
| `postgres` | Postgres server log | **backend PID** — one PID is one session |
| `syslog` / `syslog5424` | daemons, journald | tag, PID, structured data |
| `cri` | `kubectl logs`, Docker | stream, rejoined partial lines |

Unmapped fields are kept in `attributes` rather than dropped — the field no
parser has heard of is routinely the one that identifies the request.

**Normalize** (`ingest/normalize.py`) makes timestamps comparable, which
everything downstream depends on. Naive times resolve against the file's
declared timezone; missing ones carry forward, marked `time_source="carried"`.
Clock skew is **detected and reported, not corrected** — two files covering
different windows look identical to two hosts disagreeing about the time, and
only shared correlation anchors tell them apart. Supply `offset_seconds` on a
file to correct it yourself.

**Redact** (`ingest/redact.py`) runs after parsing, so a `password=` field is
removed by key rather than by guessing at a blob. `trace_id`, `span_id` and
`correlation_keys` are never touched — they are what later stages join on. Card
numbers are Luhn-checked so order IDs survive, and IPs are kept by default
because they are a correlation key more often than they are PII.

| Route                   | Method | Purpose                                      |
| ----------------------- | ------ | -------------------------------------------- |
| `/api/ingest/formats`   | GET    | Formats and roles, for the upload UI pickers  |
| `/api/ingest/preview`   | POST   | Run the phase over JSON-supplied files        |
| `/api/ingest/upload`    | POST   | Same, multipart, for real dumps               |

Preview persists nothing and calls no model, so the UI can re-run it on every
change to the file list — rename a service, fix a timezone, flip a rule.

**Throughput** is ~20k lines/s single-threaded (~2.5 MB/s), measured over 200k
lines. Redaction is about two thirds of that: one regex alternation scan per
entry, which is already the floor after the containment shortcut in
`redact_entry` cut it from one scan per *field*. Fine for the paste path;
`MAX_UPLOAD_LINES` (500k) is ~25s and belongs on the Celery worker rather than
in a request.

### Tests

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
pytest                    # 152 tests, ~1s
ruff check .
```

No database or API key needed — the ingest phase is pure, and the route tests
override the auth dependency.

## Agent pipeline

Four agents run in sequence on `claude-opus-5` with adaptive thinking. Only the
Log Parser sees raw logs; each later agent receives the prose its predecessors
produced, so a large paste is sent to the API once rather than four times.

| # | Agent               | Input                    | Output                          |
| - | ------------------- | ------------------------ | ------------------------------- |
| 0 | Log Parser          | Normalized log entries   | Factual account: scope, grouped events, notable entries |
| 1 | Pattern Detector    | Parser output            | Recurring patterns, correlations, trends, anomalies |
| 2 | Root Cause Analyzer | Parser + patterns        | Ranked hypotheses with supporting and contrary evidence |
| 3 | Bug Predictor       | All three                | Structured `Prediction` rows (JSON schema-constrained) |

Pasted text reaches the agents through [Ingest](#ingest-phase-a), which produces
the `Entry` shape stored on `log_pulls.normalized_logs`. Pastes get a per-user
`provider="manual"` source and a `log_pulls` row, keeping the schema uniform.

`ingest/prompt.py` renders those entries as a transcript for the agents. That is
a stopgap: once the digest and correlation stages land, the agents read a digest
and a handful of reconstructed flows instead, which is what takes the token count
down by orders of magnitude.

Phase 1 runs the pipeline **inline in the request** — `POST /api/analyses` blocks
for a minute or two. `ANTHROPIC_EFFORT` defaults to `medium` for that reason;
raise it once Phase 3 moves the run onto Celery and streams progress over SSE.
The `on_agent_done` hook in `agents/pipeline.py` is the seam Phase 3 publishes
from — today it checkpoints each agent's output onto the `analyses` row as it
lands, so a mid-pipeline failure still shows the agents that did finish.

| Route                          | Method | Purpose                                 |
| ------------------------------ | ------ | --------------------------------------- |
| `/api/analyses/estimate`       | POST   | Price a paste before running it         |
| `/api/analyses`                | POST   | Run the pipeline against pasted logs    |
| `/api/analyses`                | GET    | Recent analyses for the current user    |
| `/api/analyses/{id}`           | GET    | Agent outputs + predictions             |
| `/api/analyses/{id}/logs`      | GET    | The normalized entries the agents saw   |

A pipeline failure is persisted as `status="failed"` with `error_message` and
returned as 200 — the partial agent output is worth showing, so the client
renders the error from the row rather than from an HTTP status. Its token
usage is persisted too: the agents that finished before the failure were
still billed.

## Cost

Every paste is priced before it runs. The console calls
`POST /api/analyses/estimate` as you type, and a finished analysis reports what
it actually cost from the token usage the API returned.

The estimate is not tokens × rate. The pipeline makes four sequential calls
whose prompts are built out of each other, so the log sample is billed once, at
the parser, while the three later agents pay for a prompt that grows as their
predecessors write. `core/cost.py` walks those same four stages and prices each
one; it reads the prompt-assembly templates out of `agents/pipeline.py`, so
changing how a prompt is built changes the estimate with it.

Two inputs are unknowable in advance — how much prose each agent writes, and
how much it thinks first (adaptive thinking bills as output, and at `medium`
effort it is the larger half of the bill). Those come from calibration
constants at the top of `core/cost.py`; everything else is computed. Input
tokens are counted by the Claude API when the key works and fall back to a
character ratio when it does not, and the response says which happened.

`core/pricing.py` is a hand-maintained catalog — twelve models across
Anthropic, OpenAI, Google, Meta and DeepSeek, each with its source URL and a
shared `PRICES_UPDATED` date. Non-Anthropic figures scale the Claude token
count by that tokenizer's typical ratio, so they are a comparison, not a quote;
the Anthropic rows are exact. Re-check the rates and bump the date together.

## Generating secrets

```bash
# CREDENTIAL_ENCRYPTION_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# NEXTAUTH_SECRET (must be identical in backend/.env and frontend/.env.local)
openssl rand -base64 32
```

## Migrations

`docker compose up` applies pending migrations **on boot** — and only on boot.
The bind mount hot-reloads Python, so a model change is live the moment you save
it, but the table behind it is not. A container that has been up since before
you wrote a revision is running new code against an old schema.

Apply a new migration to the running stack yourself:

```bash
docker compose exec backend alembic upgrade head
```

To author one:

```bash
docker compose exec backend alembic revision --autogenerate -m "what changed"
docker compose exec backend alembic upgrade head
```

The generated revision appears in `backend/migrations/versions/` on the host —
the source directory is bind-mounted, not copied.

Running natively, the same commands are `.venv/bin/alembic upgrade head` from
`backend/`.

Migrations run through the async `asyncpg` engine — there is no second, sync
Postgres driver in the project.

### When the schema is behind

The symptom is a 500 on a write path, with this in `docker compose logs backend`:

```
asyncpg.exceptions.UndefinedColumnError: column "..." of relation "..." does not exist
```

A frontend that only reports "can't reach the API" will point you at ports and
CORS; check the backend log before either. Compare the applied revision against
the newest one on disk:

```bash
docker compose exec backend alembic current   # what the database has
docker compose exec backend alembic heads     # what the code expects
```

Different values mean a pending migration, not a connectivity problem. Run
`alembic upgrade head` and retry the request. `docker compose restart backend`
does the same thing, since the entrypoint upgrades before starting uvicorn.

## Build phases

- **Phase 0 — scaffolding & contracts** ✅ two services, 5-table schema, design tokens, shared types
- **Phase 1 — agent pipeline, run synchronously against pasted logs** ✅ four agents, log normalizer, `/analyze`
- **Phase A — ingest** ✅ multi-file upload, format detection, 8 parsers, timezone/skew normalization, redaction

The phases below are the original build order. Phases A–E are the trace-
reconstruction track that replaces the "paste and ask a model" approach with a
deterministic engine; the agents become the last step of it rather than the
whole of it.

- Phase B — enrich: Drain3 template mining, numeric + entity-key extraction
- Phase C — correlate: the L1–L5 ladder → flows with confidence and evidence
- Phase D — digest + diagnose: per-service feature timeseries, root-cause localization
- Phase E — render: swimlane timeline, evidence panel, one summarizing agent
- Phase 2 — NextAuth (GitHub + Google) + JWT verification
- Phase 3 — Celery, Redis pub/sub, SSE streaming, live analysis UI
- Phase 4 — Datadog / CloudWatch / Sentry integrations + source wizard
- Phase 5 — scheduled pulls via Celery Beat
- Phase 6 — dashboard, predictions, history, settings, feedback
- Phase 7 — GCP / Loki / webhook ingest
- Phase 8 — rate limits, email alerts, landing page
- Phase 9 — deploy
