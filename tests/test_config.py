import pytest
from pydantic import ValidationError

from app.core.config import Settings

DB = "postgresql+asyncpg://u:p@localhost/db"


def test_production_rejects_placeholder_secret():
    with pytest.raises(ValidationError):
        Settings(
            environment="production", secret_key="change-me-to-a-random-secret", database_url=DB
        )


def test_production_accepts_real_secret():
    settings = Settings(environment="production", secret_key="x" * 48, database_url=DB)
    assert settings.environment == "production"


def test_development_allows_placeholder_secret():
    Settings(environment="development", secret_key="change-me", database_url=DB)
