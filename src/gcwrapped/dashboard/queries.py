"""Typed, parameterized queries behind the dashboard.

Every query reads `analytics.*` mart tables and filters by thread with a bound parameter. The
connection is the read-only gcw_reader role (DASHBOARD_DATABASE_URL), so even a mistaken query
could not reach message text: it lives only in raw and staging, which that role cannot read.
"""

from typing import Any

import pandas as pd
import psycopg

from gcwrapped.config import get_dashboard_settings


def _frame(sql: str, params: tuple[object, ...], url: str | None) -> pd.DataFrame:
    """Run one query and return the rows as a DataFrame.

    Built from the cursor directly: pandas.read_sql only officially supports SQLAlchemy and
    sqlite3 connections, and warns on a psycopg one.
    """
    dsn = url or get_dashboard_settings().dashboard_database_url.get_secret_value()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [c.name for c in cur.description or []]
        rows: list[Any] = cur.fetchall()
    return pd.DataFrame(rows, columns=columns)


def list_threads(url: str | None = None) -> pd.DataFrame:
    """Threads that have activity, busiest first. Columns: thread_id, messages."""
    return _frame(
        "SELECT thread_id, sum(messages)::bigint AS messages FROM analytics.agg_activity_daily "
        "GROUP BY thread_id ORDER BY messages DESC, thread_id",
        (),
        url,
    )


def daily_activity(thread_id: str, url: str | None = None) -> pd.DataFrame:
    """Messages per local day.

    Columns: activity_date, messages, active_members, sessions_started.
    """
    return _frame(
        "SELECT activity_date, messages, active_members, sessions_started "
        "FROM analytics.agg_activity_daily WHERE thread_id = %s ORDER BY activity_date",
        (thread_id,),
        url,
    )


def member_leaderboard(thread_id: str, url: str | None = None) -> pd.DataFrame:
    """One row per member, most messages first.

    Columns: member_name, messages, words, median_reply_seconds, p90_reply_seconds,
    top_reaction_given, top_reaction_received.
    """
    return _frame(
        "SELECT member_name, messages, words, median_reply_seconds, p90_reply_seconds, "
        "top_reaction_given, top_reaction_received "
        "FROM analytics.agg_member_activity WHERE thread_id = %s "
        "ORDER BY messages DESC, member_name",
        (thread_id,),
        url,
    )


def sessions(thread_id: str, url: str | None = None) -> pd.DataFrame:
    """One row per conversation session, oldest first.

    Columns: session_index, started_at, ended_at, duration_seconds, message_count, turn_count,
    participant_count.
    """
    return _frame(
        "SELECT session_index, started_at, ended_at, duration_seconds, message_count, "
        "turn_count, participant_count "
        "FROM analytics.fct_sessions WHERE thread_id = %s ORDER BY started_at",
        (thread_id,),
        url,
    )


def hour_of_day(thread_id: str, url: str | None = None) -> pd.DataFrame:
    """Message count for every local hour 0-23 (zero-filled). Columns: hour_of_day, messages."""
    return _frame(
        "SELECT h AS hour_of_day, count(m.message_key) AS messages "
        "FROM generate_series(0, 23) AS h "
        "LEFT JOIN analytics.fct_messages AS m "
        "ON m.thread_id = %s AND extract(hour FROM m.sent_at_local)::int = h "
        "GROUP BY h ORDER BY h",
        (thread_id,),
        url,
    )


def median_reply_seconds(thread_id: str, url: str | None = None) -> float | None:
    """Median reply time across the thread's replies, or None when there are no replies."""
    frame = _frame(
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY reply_seconds) AS median_reply_seconds "
        "FROM analytics.fct_messages WHERE thread_id = %s",
        (thread_id,),
        url,
    )
    value = frame["median_reply_seconds"].iloc[0]
    return None if pd.isna(value) else float(value)
