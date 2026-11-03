"""Runtime configuration, read from environment variables (and an optional local .env).

Split in two so that code which never touches the database (e.g. `gcw parse`) does not need
DB credentials: `Settings` has defaults only, `DatabaseSettings` has required fields.
"""

from functools import lru_cache
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_HOST = "localhost"


def resolve_url(
    explicit: SecretStr,
    *,
    user: str,
    password: SecretStr,
    host: str,
    port: int,
    dbname: str,
) -> SecretStr:
    """The connection string for `dbname`: the explicit URL if given and it agrees, else built.

    GCW_DB_NAME is the one variable that picks the target database. An explicit URL (empty means
    unset) is only accepted when it names that same database, so ingest and dbt cannot silently
    diverge.
    """
    if not explicit.get_secret_value():
        return SecretStr(
            make_conninfo(
                host=host, port=port, user=user, password=password.get_secret_value(), dbname=dbname
            )
        )
    url_db = conninfo_to_dict(explicit.get_secret_value()).get("dbname")
    if url_db != dbname:  # names only: database names are not secrets, the URL is
        raise ValueError(f"URL targets database {url_db!r} but GCW_DB_NAME is {dbname!r}")
    return explicit


class Settings(BaseSettings):
    """Typed view of the environment that needs no credentials. Fields map to upper-case vars."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    chat_timezone: str = "America/Los_Angeles"
    landing_dir: Path = Path("data/landing")
    log_level: str = "INFO"


class DatabaseSettings(BaseSettings):
    """Postgres connection settings. Required only by code that connects to the database."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gcw_db_name: str
    postgres_user: str
    postgres_password: SecretStr
    postgres_host: str = DEFAULT_HOST
    postgres_port: int = 5432
    # Optional: built from the parts above when empty; checked against gcw_db_name when set.
    database_url: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def _resolve_database_url(self) -> "DatabaseSettings":
        self.database_url = resolve_url(
            self.database_url,
            user=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            dbname=self.gcw_db_name,
        )
        return self


class DashboardSettings(BaseSettings):
    """Dashboard connection. Must be the read-only gcw_reader role, never the pipeline user."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gcw_db_name: str
    gcw_reader_password: SecretStr
    postgres_host: str = DEFAULT_HOST
    postgres_port: int = 5432
    dashboard_database_url: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def _resolve_dashboard_url(self) -> "DashboardSettings":
        self.dashboard_database_url = resolve_url(
            self.dashboard_database_url,
            user="gcw_reader",
            password=self.gcw_reader_password,
            host=self.postgres_host,
            port=self.postgres_port,
            dbname=self.gcw_db_name,
        )
        return self


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
