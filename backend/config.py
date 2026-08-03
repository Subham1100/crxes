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
