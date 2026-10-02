from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    secret_key: str = "change-me"
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    algorithm: str = "HS256"
    # Comma-separated list of origins the frontend is served from, e.g.
    # "http://localhost:3000,https://couponza.example.com". Without this,
    # browsers block every client-side fetch from the Next.js app to this API
    # (server-side fetches in Next.js Server Components aren't affected —
    # only same-origin-policy-enforcing browser requests are).
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def _reject_placeholder_secret_in_production(self) -> "Settings":
        # JWTs and the admin session cookie are both signed with this key.
        if self.environment == "production" and self.secret_key.startswith("change-me"):
            raise ValueError("SECRET_KEY must be set to a real random value in production")
        return self


@lru_cache
def get_settings() -> Settings:
    # mypy can't see that required fields are populated from the environment
    # at runtime, not from this call site — known pydantic-settings false positive.
    return Settings()  # type: ignore[call-arg]
