"""Shared fixtures, including the session-start test-isolation guard."""

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from gcwrapped.config import get_database_settings
from gcwrapped.db import database_url
from gcwrapped.migrate import migrate
from isolation_guard import IsolationError, check_session
from synthetic.instagram import SYNTHETIC_THREAD_IDS

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _test_isolation_guard() -> None:
    """Abort the whole run if the session's configuration can reach the real stack.

    Checks the configuration the session starts with (env file plus process environment). Values
    that a test monkeypatches later are deliberately not inspected. Never weaken or skip this.
    """
    try:
        check_session(os.environ, cwd=Path.cwd(), repo_root=REPO_ROOT)
    except IsolationError as exc:
        pytest.exit(f"test-isolation guard: {exc}", returncode=3)


TEST_DB_NAME = "gcw_test"


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """URL of gcw_test on the dev Postgres, created if missing and migrated (D68).

    Host and credentials come from the session's own settings (the synthetic dev stack or CI);
    only the database name changes. CREATE DATABASE cannot run in a transaction, hence autocommit.
    """
    dev_url = database_url()
    url = make_conninfo(dev_url, dbname=TEST_DB_NAME)
    with psycopg.connect(dev_url, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,))
        if exists.fetchone() is None:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB_NAME)))
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    migrate(url)
    return url


@pytest.fixture
def clean_db(test_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Connection URL of gcw_test with no `raw` schema and no migration history.

    Points GCW_DB_NAME and DATABASE_URL at gcw_test too, so code that reads settings (the CLI)
    hits the same database. Refuses to run anywhere else, since it drops tables.
    """
    monkeypatch.setenv("GCW_DB_NAME", TEST_DB_NAME)
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    get_database_settings.cache_clear()
    url = database_url()
    with psycopg.connect(url, autocommit=True) as conn:
        row = conn.execute("SELECT current_database()").fetchone()
        assert row == (TEST_DB_NAME,), "integration tests may only reset the gcw_test database"
        conn.execute("DROP SCHEMA IF EXISTS raw CASCADE")
        conn.execute("DROP TABLE IF EXISTS schema_migrations")
        conn.execute("DROP TABLE IF EXISTS migration_probe")
    yield url
    get_database_settings.cache_clear()


@pytest.fixture
def migrated_db(clean_db: str) -> str:
    """`clean_db` with all migrations applied (empty raw tables)."""
    migrate(clean_db)
    return clean_db


@pytest.fixture
def synthetic_allowlist(monkeypatch: pytest.MonkeyPatch) -> frozenset[str]:
    """GCW_THREAD_ALLOWLIST set to the synthetic thread IDs, as the dev .env and CI do."""
    monkeypatch.setenv("GCW_THREAD_ALLOWLIST", ",".join(SYNTHETIC_THREAD_IDS))
    return frozenset(SYNTHETIC_THREAD_IDS)
