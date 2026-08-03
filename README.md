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
| Backend  | 8001 |
| Frontend | 3001 |

## Setup

```bash
# 1. Infrastructure
docker compose up -d

# 2. Backend
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # then fill in the secrets
.venv/bin/alembic upgrade head
.venv/bin/uvicorn main:app --reload --port 8001

# 3. Frontend
cd frontend
npm install
cp .env.example .env.local    # then fill in the secrets
npm run dev
```

Verify: `curl localhost:8001/api/health/` → `{"status":"ok","database":"ok"}`,
and http://localhost:3001 shows the backend health inline.

## Generating secrets

```bash
# CREDENTIAL_ENCRYPTION_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# NEXTAUTH_SECRET (must be identical in backend/.env and frontend/.env.local)
openssl rand -base64 32
```

## Migrations

```bash
cd backend
.venv/bin/alembic revision --autogenerate -m "what changed"
.venv/bin/alembic upgrade head
```

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
