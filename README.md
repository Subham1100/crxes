# crxes.app

Multi-agent AI bug prediction. Connect a log source (Datadog, CloudWatch, GCP,
Sentry, Loki, webhook), and four agents run in sequence — Log Parser → Pattern
Detector → Root Cause Analyzer → Bug Predictor — to forecast what's about to break.

## Local ports

The defaults from the spec (5432, 6379, 8000, 3000) are all taken on this
machine by another project, so crxes uses shifted ports:

| Service  | Port | Runs in                  |
| -------- | ---- | ------------------------ |
| Postgres | 5433 | Docker                   |
| Redis    | 6380 | Docker                   |
| Backend  | 8001 | Docker                   |
| Frontend | 3001 | Host                     |

Inside the compose network the datastores use their standard ports —
`postgres:5432` and `redis:6379`. Only the host-side ports are shifted.

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

### Verify

```bash
docker compose ps                  # postgres, redis, backend all healthy
curl localhost:8001/api/health/    # {"status":"ok","database":"ok"}
```

and http://localhost:3001 serves the landing page.

## Everyday commands

```bash
docker compose up -d               # start detached
docker compose logs -f backend     # follow the API log (Ctrl+C only detaches)
docker compose stop                # stop, keeping data
docker compose down -v             # stop and DELETE the database volumes
docker compose exec backend bash   # shell inside the API container
```

### Running the backend natively instead

Still supported, and useful for attaching a debugger:

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/uvicorn main:app --reload --port 8001
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
(:8001) and the Next server (:3001) — `lib/session.ts` forwards it back to
`/api/auth/me` to resolve the user in server components. In production set
`SESSION_COOKIE_DOMAIN=.crxes.app` and `SESSION_COOKIE_SECURE=true`.

| Route              | Method | Purpose                                  |
| ------------------ | ------ | ---------------------------------------- |
| `/api/auth/signup` | POST   | Create account, set session cookie       |
| `/api/auth/login`  | POST   | Verify password, set session cookie      |
| `/api/auth/logout` | POST   | Clear the cookie                         |
| `/api/auth/me`     | GET    | Current user, or 401                     |

Pages: `/signup`, `/login`, and a placeholder `/dashboard` behind the session.
OAuth (GitHub, Google) lands in Phase 2 and will write to the same `users` table
with `password_hash` left null — the provider buttons are on the forms, disabled.

## Generating secrets

```bash
# CREDENTIAL_ENCRYPTION_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# NEXTAUTH_SECRET (must be identical in backend/.env and frontend/.env.local)
openssl rand -base64 32
```

## Migrations

`docker compose up` applies pending migrations on every boot, so the schema is
current without a separate step. To author a new one:

```bash
docker compose exec backend alembic revision --autogenerate -m "what changed"
docker compose exec backend alembic upgrade head
```

The generated revision appears in `backend/migrations/versions/` on the host —
the source directory is bind-mounted, not copied.

Migrations run through the async `asyncpg` engine — there is no second, sync
Postgres driver in the project.

## Build phases

- **Phase 0 — scaffolding & contracts** ✅ two services, 5-table schema, design tokens, shared types
- Phase 1 — agent pipeline, run synchronously against pasted logs
- Phase 2 — NextAuth (GitHub + Google) + JWT verification
- Phase 3 — Celery, Redis pub/sub, SSE streaming, live analysis UI
- Phase 4 — Datadog / CloudWatch / Sentry integrations + source wizard
- Phase 5 — scheduled pulls via Celery Beat
- Phase 6 — dashboard, predictions, history, settings, feedback
- Phase 7 — GCP / Loki / webhook ingest
- Phase 8 — rate limits, email alerts, landing page
- Phase 9 — deploy
