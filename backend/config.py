from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://crxes:crxes@localhost:5432/crxes"

    @field_validator("database_url")
    @classmethod
    def _asyncpg_url(cls, v: str) -> str:
        # Hosted Postgres (Neon via the Vercel integration) hands out a libpq
        # URL: no driver, so SQLAlchemy picks psycopg, which is not installed,
        # and libpq-only params asyncpg rejects. Accept it verbatim.
        v = v.strip().strip("'\"")
        scheme, _, rest = v.partition("://")
        if scheme not in ("postgres", "postgresql", "postgresql+asyncpg"):
            return v
        parts = urlsplit("postgresql+asyncpg://" + rest)
        query = []
        for key, value in parse_qsl(parts.query):
            if key == "sslmode":
                query.append(("ssl", value))
            elif key != "channel_binding":
                query.append((key, value))
        return urlunsplit(parts._replace(query=urlencode(query)))

    # Redis (Celery broker + SSE pub/sub)
    redis_url: str = "redis://localhost:6379/0"

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    anthropic_max_tokens: int = 8000
    # Phase 1 runs the pipeline inline in the request, so latency is visible to
    # the user waiting on the page. "medium" keeps a four-agent run tolerable;
    # raise to "high" once Phase 3 moves this onto Celery.
    anthropic_effort: str = "medium"
    # Four sequential Opus calls — the SDK's 10-minute default can expire
    # mid-pipeline on a large log sample.
    anthropic_timeout: float = 900.0

    # Encryption — Fernet.generate_key()
    credential_encryption_key: str = ""

    # Auth — shared with the Next.js frontend for JWT verification
    nextauth_secret: str = ""
    session_cookie_name: str = "crxes_session"
    session_ttl_hours: int = 24 * 7
    # Dev runs over plain http on localhost; set true behind TLS in production.
    session_cookie_secure: bool = False
    # Unset in dev so the cookie is host-only on `localhost` and reaches both
    # the API (:8002) and the Next server (:3002). In production: ".crxes.app".
    session_cookie_domain: str | None = None

    # Email
    resend_api_key: str = ""

    # App
    cors_origins: str = "http://localhost:3000,http://localhost:3002"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
