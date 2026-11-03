"""Dashboard tests against the built warehouse (synthetic data only).

These need gcw_dev after `gcw migrate`, `gcw ingest` and `dbt build`, and the gcw_reader role, so
they carry the `warehouse` marker instead of `integration` (which runs before dbt in CI).
"""

from pathlib import Path

import psycopg
import pytest
from psycopg import errors
from streamlit.testing.v1 import AppTest

from gcwrapped.config import get_dashboard_settings
from gcwrapped.dashboard import queries

pytestmark = pytest.mark.warehouse

APP = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"


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
    "relation",
    [
        "raw.messages",
        "analytics.stg_instagram__messages",
        "analytics.int_messages_sessionized",
    ],
)
def test_reader_cannot_read_text_bearing_relations(reader_url: str, relation: str) -> None:
    # A fresh connection per case: a failed statement aborts the transaction.
    with (
        psycopg.connect(reader_url) as conn,
        pytest.raises(errors.InsufficientPrivilege),
    ):
        conn.execute(f"SELECT 1 FROM {relation} LIMIT 1")


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


def test_app_runs_without_exceptions(reader_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_DATABASE_URL", reader_url)
    get_dashboard_settings.cache_clear()
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    assert at.title[0].value == "GC Wrapped"
    assert [m.label for m in at.metric] == ["Messages", "Members", "Sessions", "Median reply time"]
    assert len(at.selectbox) == 1
    get_dashboard_settings.cache_clear()
