"""Shared fixtures, including the session-start test-isolation guard."""

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

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


@pytest.fixture
def clean_db() -> Iterator[str]:
    """Connection URL of a database with no `raw` schema and no migration history.

    Refuses to run anywhere but the synthetic dev database, since it drops tables.
    """
    url = database_url()
    with psycopg.connect(url, autocommit=True) as conn:
        row = conn.execute("SELECT current_database()").fetchone()
        assert row == ("gcw_dev",), "integration tests may only reset the synthetic dev database"
        conn.execute("DROP SCHEMA IF EXISTS raw CASCADE")
        conn.execute("DROP TABLE IF EXISTS schema_migrations")
        conn.execute("DROP TABLE IF EXISTS migration_probe")
    yield url


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
