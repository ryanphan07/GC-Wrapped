"""Runtime configuration, read from environment variables (and an optional local .env).

Split in two so that code which never touches the database (e.g. `gcw parse`) does not need
DB credentials: `Settings` has defaults only, `DatabaseSettings` has required fields.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed view of the environment that needs no credentials. Fields map to upper-case vars."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    chat_timezone: str = "America/Los_Angeles"
    landing_dir: Path = Path("data/landing")
    log_level: str = "INFO"


class DatabaseSettings(BaseSettings):
    """Postgres connection settings. Required only by code that connects to the database."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str
    postgres_password: SecretStr
    postgres_port: int = 5432
    database_url: SecretStr


class DashboardSettings(BaseSettings):
    """Dashboard connection. Must be the read-only gcw_reader role, never the pipeline user."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    dashboard_database_url: SecretStr


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process."""
    return Settings()


@lru_cache
def get_database_settings() -> DatabaseSettings:
    """Load DB settings once per process. Raises if the DB env vars are missing."""
    return DatabaseSettings()


@lru_cache
def get_dashboard_settings() -> DashboardSettings:
    """Load dashboard DB settings once per process. Raises if DASHBOARD_DATABASE_URL is unset."""
    return DashboardSettings()
