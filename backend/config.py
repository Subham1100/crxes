from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://crxes:crxes@localhost:5432/crxes"

    # Redis (Celery broker + SSE pub/sub)
    redis_url: str = "redis://localhost:6379/0"

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # Encryption — Fernet.generate_key()
    credential_encryption_key: str = ""

    # Auth — shared with the Next.js frontend for JWT verification
    nextauth_secret: str = ""
    session_cookie_name: str = "crxes_session"
    session_ttl_hours: int = 24 * 7
    # Dev runs over plain http on localhost; set true behind TLS in production.
    session_cookie_secure: bool = False
    # Unset in dev so the cookie is host-only on `localhost` and reaches both
    # the API (:8001) and the Next server (:3001). In production: ".crxes.app".
    session_cookie_domain: str | None = None

    # Email
    resend_api_key: str = ""

    # App
    cors_origins: str = "http://localhost:3000,http://localhost:3001"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
