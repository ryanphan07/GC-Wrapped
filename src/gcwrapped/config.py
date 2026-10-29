"""Runtime configuration, read from environment variables (and an optional local .env)."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed view of the environment. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str
    postgres_password: SecretStr
    postgres_port: int = 5432
    database_url: SecretStr
    chat_timezone: str = "America/Los_Angeles"
    landing_dir: Path = Path("data/landing")
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process."""
    return Settings()
