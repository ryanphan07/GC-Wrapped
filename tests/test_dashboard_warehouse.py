"""Dashboard tests against the built warehouse (synthetic data only).

These need gcw_dev after `gcw migrate`, `gcw ingest` and `dbt build`, and the gcw_reader role, so
they carry the `warehouse` marker instead of `integration` (which runs before dbt in CI).
"""

import tomllib
from pathlib import Path

import psycopg
import pytest
import streamlit as st
from psycopg import errors
from streamlit.testing.v1 import AppTest

from gcwrapped.config import get_dashboard_settings
from gcwrapped.dashboard import queries

pytestmark = pytest.mark.warehouse

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dashboard" / "app.py"
SENTINEL = "sentinel-pw-7f3a9c"


@pytest.fixture(scope="module")
def reader_url() -> str:
    return get_dashboard_settings().dashboard_database_url.get_secret_value()


@pytest.fixture(scope="module")
def thread_id(reader_url: str) -> str:
    threads = queries.list_threads(reader_url)
    assert not threads.empty, "run ingest and dbt build before the warehouse tests"
    return str(threads["thread_id"].iloc[0])


def test_list_threads(reader_url: str) -> None:
    threads = queries.list_threads(reader_url)
    assert list(threads.columns) == ["thread_id", "messages"]
    assert not threads.empty


def test_daily_activity(reader_url: str, thread_id: str) -> None:
    frame = queries.daily_activity(thread_id, reader_url)
    assert list(frame.columns) == [
        "activity_date",
        "messages",
        "active_members",
        "sessions_started",
    ]
    assert not frame.empty


def test_member_leaderboard(reader_url: str, thread_id: str) -> None:
    frame = queries.member_leaderboard(thread_id, reader_url)
    assert list(frame.columns) == [
        "member_name",
        "messages",
        "words",
        "median_reply_seconds",
        "p90_reply_seconds",
        "top_reaction_given",
        "top_reaction_received",
    ]
    assert not frame.empty


def test_sessions(reader_url: str, thread_id: str) -> None:
    frame = queries.sessions(thread_id, reader_url)
    assert list(frame.columns) == [
        "session_index",
        "started_at",
        "ended_at",
        "duration_seconds",
        "message_count",
        "turn_count",
        "participant_count",
    ]
    assert not frame.empty


def test_hour_of_day_covers_all_hours(reader_url: str, thread_id: str) -> None:
    frame = queries.hour_of_day(thread_id, reader_url)
    assert list(frame.columns) == ["hour_of_day", "messages"]
    assert frame["hour_of_day"].tolist() == list(range(24))
    assert frame["messages"].sum() > 0


def test_median_reply_seconds(reader_url: str, thread_id: str) -> None:
    value = queries.median_reply_seconds(thread_id, reader_url)
    assert value is not None
    assert value >= 0


def test_unknown_thread_is_empty_not_an_error(reader_url: str) -> None:
    assert queries.daily_activity("no-such-thread", reader_url).empty
    assert queries.median_reply_seconds("no-such-thread", reader_url) is None


@pytest.mark.parametrize(
    ("schema", "relation"),
    [
        ("raw", "raw.messages"),
        ("staging", "staging.stg_instagram__messages"),
        ("staging", "staging.int_messages_sessionized"),
    ],
)
def test_reader_is_denied_the_text_bearing_schemas(
    reader_url: str, schema: str, relation: str
) -> None:
    # A fresh connection per case: a failed statement aborts the transaction.
    with (
        psycopg.connect(reader_url) as conn,
        pytest.raises(errors.InsufficientPrivilege, match=f"permission denied for schema {schema}"),
    ):
        conn.execute(f"SELECT 1 FROM {relation} LIMIT 1")


def test_reader_has_usage_on_analytics_only(reader_url: str) -> None:
    with psycopg.connect(reader_url) as conn:
        rows = conn.execute(
            "SELECT s, has_schema_privilege(s, 'USAGE') "
            "FROM unnest(ARRAY['analytics', 'staging', 'raw']) AS s"
        ).fetchall()
    assert dict(rows) == {"analytics": True, "staging": False, "raw": False}


def test_no_staging_relation_lives_in_analytics(reader_url: str) -> None:
    # pg_class is readable by every role, so this sees relations the reader cannot select from.
    with psycopg.connect(reader_url) as conn:
        rows = conn.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'analytics' AND c.relname ~ '^(stg|int)_'"
        ).fetchall()
    assert rows == []


def test_reader_can_read_marts(reader_url: str) -> None:
    with psycopg.connect(reader_url) as conn:
        row = conn.execute("SELECT count(*) FROM analytics.fct_messages").fetchone()
    assert row is not None
    assert row[0] > 0


def test_no_mart_the_reader_can_see_has_a_content_column(reader_url: str) -> None:
    with psycopg.connect(reader_url) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'analytics' AND column_name = 'content'"
        ).fetchall()
    assert rows == []


def test_error_details_are_disabled_in_streamlit_config() -> None:
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["client"]["showErrorDetails"] is False


def _rendered_text(at: AppTest) -> str:
    """Everything the app put on the page, plus any exception element Streamlit would show."""
    elements = (*at.error, *at.exception, *at.info, *at.markdown, *at.caption)
    return " | ".join(str(el.value) for el in elements)


@pytest.fixture
def fresh_app_state(monkeypatch: pytest.MonkeyPatch) -> object:
    """No cached query results or settings from other tests, and none left behind."""
    for var in ("DASHBOARD_DATABASE_URL", "GCW_DB_NAME", "GCW_READER_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    st.cache_data.clear()
    get_dashboard_settings.cache_clear()
    yield None
    st.cache_data.clear()
    get_dashboard_settings.cache_clear()


@pytest.mark.usefixtures("fresh_app_state")
def test_invalid_config_shows_a_fixed_message_and_no_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The port is not an integer, so pydantic's validation error would quote the input value.
    monkeypatch.setenv("GCW_DB_NAME", "gcw_dev")
    monkeypatch.setenv("GCW_READER_PASSWORD", SENTINEL)
    monkeypatch.setenv("POSTGRES_PORT", f"{SENTINEL}-port")
    monkeypatch.setenv("DASHBOARD_DATABASE_URL", f"postgresql://gcw_reader:{SENTINEL}@h/other")

    at = AppTest.from_file(str(APP), default_timeout=60).run()

    assert not at.exception
    assert [e.value for e in at.error] == [
        "The dashboard could not load its data. Check the database settings and that the "
        "database is running, then reload. Details are in the server logs."
    ]
    assert SENTINEL not in _rendered_text(at)
    assert not at.metric


@pytest.mark.usefixtures("fresh_app_state")
def test_unreachable_database_shows_a_fixed_message_and_no_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GCW_DB_NAME", "gcw_dev")
    monkeypatch.setenv("GCW_READER_PASSWORD", SENTINEL)
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_PORT", "1")  # nothing listens here
    monkeypatch.setenv("PGCONNECT_TIMEOUT", "2")  # libpq: fail fast where refusals are slow

    at = AppTest.from_file(str(APP), default_timeout=60).run()

    assert not at.exception
    assert len(at.error) == 1
    assert SENTINEL not in _rendered_text(at)
    assert "127.0.0.1" not in _rendered_text(at)


def test_app_runs_without_exceptions(reader_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_DATABASE_URL", reader_url)
    get_dashboard_settings.cache_clear()
    st.cache_data.clear()
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    assert at.title[0].value == "GC Wrapped"
    assert [m.label for m in at.metric] == ["Messages", "Members", "Sessions", "Median reply time"]
    assert len(at.selectbox) == 1
    get_dashboard_settings.cache_clear()
