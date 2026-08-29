# Dev image. Source is bind-mounted by docker-compose, so only the dependency
# layer is baked in and `--reload` still picks up edits on the host.
# Production images (no reload, no dev deps) land in Phase 9.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# watchfiles' inotify does not fire through a macOS bind mount, so without
# polling `--reload` silently never reloads.
ENV WATCHFILES_FORCE_POLLING=true

WORKDIR /app

# curl is only here for the container healthcheck compose runs against /api/health/.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copied before the source so a code edit does not invalidate the pip layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8001

# Migrations run on every boot so `docker compose up` needs no second command.
# `alembic upgrade head` is a no-op when the schema is already current.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --reload --host 0.0.0.0 --port 8001"]
