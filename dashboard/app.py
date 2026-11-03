"""Streamlit dashboard over the analytics marts. Shows counts and timings, never message text."""

import logging

import pandas as pd
import streamlit as st

from gcwrapped.dashboard import queries


def _format_duration(seconds: float | None) -> str:
    """Human-friendly reply time, e.g. 45s, 12m, 1.5h; a dash when unknown."""
    if seconds is None or pd.isna(seconds):
        return "-"
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


log = logging.getLogger("gcwrapped.dashboard")

# Fixed on purpose: config validation errors echo input values (passwords), and connection
# errors can name hosts and users. The reason goes to the server log as an exception type only.
_UNAVAILABLE = (
    "The dashboard could not load its data. Check the database settings and that the database "
    "is running, then reload. Details are in the server logs."
)


@st.cache_data(ttl=60)
def _threads() -> pd.DataFrame:
    return queries.list_threads()


@st.cache_data(ttl=60)
def _daily(thread_id: str) -> pd.DataFrame:
    return queries.daily_activity(thread_id)


@st.cache_data(ttl=60)
def _members(thread_id: str) -> pd.DataFrame:
    return queries.member_leaderboard(thread_id)


@st.cache_data(ttl=60)
def _sessions(thread_id: str) -> pd.DataFrame:
    return queries.sessions(thread_id)


@st.cache_data(ttl=60)
def _hours(thread_id: str) -> pd.DataFrame:
    return queries.hour_of_day(thread_id)


@st.cache_data(ttl=60)
def _median_reply(thread_id: str) -> float | None:
    return queries.median_reply_seconds(thread_id)


st.set_page_config(page_title="GC Wrapped", layout="wide")
st.title("GC Wrapped")
st.caption(
    "Privacy: this dashboard reads aggregate tables only. No message text is displayed, and "
    "the database role it connects as cannot read it."
)

try:
    threads = _threads()
except Exception as exc:  # settings validation or connection failure; never show its text
    log.error("dashboard data unavailable (%s)", type(exc).__name__)
    st.error(_UNAVAILABLE)
    st.stop()

if threads.empty:
    st.info("No activity yet. Run the pipeline (ingest, then dbt build) and reload.")
    st.stop()

thread_id = st.selectbox("Thread", threads["thread_id"].tolist())
try:
    daily = _daily(thread_id)
    members = _members(thread_id)
    sessions = _sessions(thread_id)
    median_reply = _median_reply(thread_id)
    hours = _hours(thread_id)
except Exception as exc:  # the database can go away after the first query succeeded
    log.error("dashboard data unavailable (%s)", type(exc).__name__)
    st.error(_UNAVAILABLE)
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Messages", f"{int(daily['messages'].sum()):,}")
c2.metric("Members", f"{int((members['messages'] > 0).sum()):,}")
c3.metric("Sessions", f"{len(sessions):,}")
c4.metric("Median reply time", _format_duration(median_reply))

st.subheader("Messages per day")
st.line_chart(daily, x="activity_date", y="messages")

st.subheader("Member leaderboard")
board = members.assign(
    median_reply=members["median_reply_seconds"].map(_format_duration),
    p90_reply=members["p90_reply_seconds"].map(_format_duration),
)[
    [
        "member_name",
        "messages",
        "words",
        "median_reply",
        "p90_reply",
        "top_reaction_given",
        "top_reaction_received",
    ]
]
st.dataframe(board, hide_index=True, use_container_width=True)

st.subheader("Messages by hour of day")
st.bar_chart(hours, x="hour_of_day", y="messages")
