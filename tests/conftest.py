"""Shared fixtures, including the session-start test-isolation guard."""

import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote, urlencode

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from gcwrapped.config import get_database_settings
from gcwrapped.db import database_url
from gcwrapped.migrate import migrate
from isolation_guard import IsolationError, check_database_url, check_session
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


def to_test_db_url(dev_url: str) -> str:
    """The dev database URL with the database changed to gcw_test, as a postgresql:// URL.

    `dev_url` may be a URL or a keyword DSN (config builds one when DATABASE_URL is empty), so it
    is parsed with conninfo_to_dict and rebuilt. isolation_guard.check_database_url rejects
    keyword DSNs (PR 28), and the guard ran only at session start, before this swap, so the result
    is asserted to pass it here.
    """
    parts = {k: str(v) for k, v in conninfo_to_dict(dev_url).items() if v is not None}
    userinfo = quote(parts.pop("user", ""), safe="")
    password = parts.pop("password", None)
    if password is not None:
        userinfo += ":" + quote(password, safe="")
    host = parts.pop("host", "")
    port = parts.pop("port", "")
    parts.pop("dbname", None)
    netloc = (f"{userinfo}@" if userinfo else "") + host + (f":{port}" if port else "")
    query = urlencode(parts)
    url = f"postgresql://{netloc}/{TEST_DB_NAME}" + (f"?{query}" if query else "")
    check_database_url("test_database_url", url)
    assert conninfo_to_dict(url).get("dbname") == TEST_DB_NAME
    return url


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """URL of gcw_test on the dev Postgres, created if missing and migrated (D68).

    Host and credentials come from the session's own settings (the synthetic dev stack or CI);
    only the database name changes. CREATE DATABASE cannot run in a transaction, hence autocommit.
    """
    dev_url = database_url()
    url = to_test_db_url(dev_url)
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
