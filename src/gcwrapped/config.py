"""Runtime configuration, read from environment variables (and an optional local .env).

Split in two so that code which never touches the database (e.g. `gcw parse`) does not need
DB credentials: `Settings` has defaults only, `DatabaseSettings` has required fields.
"""

import os
from functools import lru_cache
from typing import Any

from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_HOST = "localhost"
ENV_FILE_VAR = "ENV_FILE"
DEFAULT_ENV_FILE = ".env"


class EnvFileMissingError(ValueError):
    """ENV_FILE names a file that does not exist. The message never includes the path."""


def env_file_path() -> str:
    """The one env file host-side settings read: ENV_FILE if set and non-empty, else `.env`.

    A named file other than `.env` must exist (decisions.md, 2026-11-05): a typo would otherwise
    fall back to process-environment settings only. Unset, empty or `.env` is allowed to be
    missing, because containers have no `.env`.

    A run that names another file never falls back to the repo's `.env` (decisions.md,
    2026-11-05), so a key missing from that file cannot be filled with a dev value.
    """
    named = os.environ.get(ENV_FILE_VAR) or DEFAULT_ENV_FILE
    if named != DEFAULT_ENV_FILE and not os.path.isfile(named):
        raise EnvFileMissingError(f"{ENV_FILE_VAR} names a file that does not exist")
    return named


class _HostSettings(BaseSettings):
    """Base of every settings class: reads the process environment plus `env_file_path()`.

    Process environment wins over the file. An explicit `_env_file=` argument (tests) overrides it.
    """

    model_config = SettingsConfigDict(extra="ignore")

    def __init__(self, **values: Any) -> None:
        values.setdefault("_env_file", env_file_path())
        super().__init__(**values)


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


class Settings(_HostSettings):
    """Typed view of the environment that needs no credentials. Fields map to upper-case vars."""

    log_level: str = "INFO"


class ConsentSettings(_HostSettings):
    """The consent allowlist as written, parsed by `gcwrapped.consent`.

    Kept as a plain string on purpose: a pydantic validator error would echo the raw value, and a
    mistyped entry could be a person's name.
    """

    gcw_thread_allowlist: str = ""


class DatabaseSettings(_HostSettings):
    """Postgres connection settings. Required only by code that connects to the database."""

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


class DashboardSettings(_HostSettings):
    """Dashboard connection. Must be the read-only gcw_reader role, never the pipeline user."""

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
