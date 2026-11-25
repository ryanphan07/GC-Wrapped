"""The dashboard's Ask and Wrapped pages (D123), run by Streamlit's AppTest against gcw_test.

Ask: the real pipeline, SQL and clients, against the stub embed and llm servers, on
test_retrieval_integration's warehouse. Wrapped: the real page and query over a stand-in for the
mart table (its SQL is tested by dbt unit and data tests).

Streamlit is in the dashboard dependency group (`make sync`), which the unit-test job lacks.
"""

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

pytest.importorskip("streamlit")

import streamlit as st
from streamlit.testing.v1 import AppTest

from gcwrapped.answers import comparison
from gcwrapped.answers.prompt import system_prompt
from gcwrapped.config import get_dashboard_settings
from gcwrapped.dashboard import ask, queries, wrapped
from gcwrapped.retrieval.embed import run_embed
from tagging_stub import StubLlm
from test_answers_integration import REPLY_SENTINEL, _output, _reply, _user, embedded, llm
from test_retrieval_integration import (
    ID,
    SENTINEL,
    _assert_clean,
    _client,
    _revoke,
    stub,
    warehouse,
)

__all__ = ["embedded", "llm", "stub", "warehouse"]  # fixtures, used by name

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[1]
VIEWS = REPO / "dashboard" / "views"
ASKED = f"{SENTINEL}ASKED"


def _page(name: str) -> AppTest:
    return AppTest.from_file(str(VIEWS / f"{name}.py"), default_timeout=60).run()


def _ask(question: str) -> AppTest:
    return _page("ask").chat_input[0].set_value(question).run()


def _shown(at: AppTest) -> list[str]:
    return [str(element.value) for element in at.text]


def _everything(at: AppTest) -> str:
    elements = (*at.text, *at.markdown, *at.caption, *at.error, *at.info, *at.exception)
    return "\n".join(str(e.value) for e in elements)


# --- Ask ---------------------------------------------------------------------------------------


def test_an_answer_shows_the_answer_and_its_cited_messages_as_plain_text(
    embedded: str, llm: StubLlm
) -> None:
    at = _ask("when is the midterm")
    assert not at.exception and not at.error
    shown = _shown(at)
    assert shown[0] == "Question: when is the midterm"
    (answer,) = [line for line in shown if line.startswith(REPLY_SENTINEL)]
    cited = shown[shown.index(answer) + 2 :]
    assert len(cited) == 1
    assert cited[0].endswith(f"SENDERANA, 2023-12-31 19:20: echo midterm tomorrow {SENTINEL}")
    # chat and model text reach the page only through st.text
    rendered_as_markdown = "\n".join(str(e.value) for e in (*at.markdown, *at.caption))
    assert SENTINEL not in rendered_as_markdown and "SENDER" not in rendered_as_markdown


def test_the_page_uses_the_promoted_prompt_variant(embedded: str, llm: StubLlm) -> None:
    _ask("when is the midterm")
    comparison.promote(embedded, "b")
    _ask("when is the midterm")
    sent = [r["messages"][0]["content"] for r in llm.requests]
    assert sent == [system_prompt("a"), system_prompt("b")]


def test_an_abstention_shows_not_in_the_chat_and_no_message(embedded: str, llm: StubLlm) -> None:
    at = _ask("where is the ramen")
    assert _shown(at) == ["Question: where is the ramen", "not in the chat"]
    assert "SENDER" not in _everything(at)


def test_an_invalid_output_shows_the_fixed_failure(embedded: str, llm: StubLlm) -> None:
    at = _ask("who else")  # the stub cites a message that does not exist
    assert _shown(at)[1:] == ["answer failed: invalid model output"]
    assert REPLY_SENTINEL not in _everything(at)


def test_a_revoked_senders_message_never_appears(
    embedded: str, stub: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cite_everything(body: dict[str, Any]) -> str:
        numbers = body["response_format"]["json_schema"]["schema"]["properties"]["citations"]
        return _output(True, numbers["items"]["enum"][:5], "yes")

    with StubLlm(reply=cite_everything) as server:
        monkeypatch.setenv("GCW_LLM_URL", server.url)
        before = _ask("charlie bringing soda")
        assert "charlie bringing soda" in _everything(before)
        _revoke(embedded, "SENDERCY")  # nothing pruned, re-embedded or rebuilt since
        server.requests.clear()
        after = _ask("charlie bringing soda")
        (request,) = server.requests
    assert not after.error and len(_shown(after)) > 2
    cited = "\n".join(_shown(after)[1:])
    assert "SENDERCY" not in cited and "charlie bringing soda" not in cited
    # the question holds the words; the messages shown to the model must not
    assert "SENDERCY" not in _user(request)
    assert _user(request).count("charlie bringing soda") == 1  # the question line only


def test_a_message_with_markup_is_shown_literally(embedded: str, stub: Any, llm: StubLlm) -> None:
    markup = "midterm tomorrow ![x](http://example.com/pixel) <img src=x>"
    with psycopg.connect(embedded, autocommit=True) as conn:
        conn.execute(
            "UPDATE raw.messages SET content = %s WHERE message_key = %s", (markup, ID["a3"])
        )
    run_embed(embedded, _client(stub))
    at = _ask("when is the midterm")
    assert any(line.endswith(markup) for line in _shown(at))
    assert not any("example.com" in str(e.value) for e in (*at.markdown, *at.caption))


def _files_with(needle: bytes, *roots: Path) -> list[str]:
    return [
        str(path)
        for root in roots
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and needle in path.read_bytes()
    ]


def test_a_question_is_written_logged_and_kept_nowhere(
    embedded: str,
    llm: StubLlm,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    home, temp = tmp_path / "home", tmp_path / "temp"
    home.mkdir()
    temp.mkdir()
    monkeypatch.setenv("HOME", str(home))  # Streamlit's cache and state live under it
    monkeypatch.setenv("TMPDIR", str(temp))
    monkeypatch.setattr("tempfile.tempdir", str(temp))

    def counts() -> list[int]:
        with psycopg.connect(embedded) as conn:
            tables = conn.execute(
                "SELECT schemaname || '.' || tablename FROM pg_tables "
                "WHERE schemaname IN ('raw', 'enrich', 'analytics') ORDER BY 1"
            ).fetchall()
            found = []
            for (table,) in tables:
                row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
                assert row is not None
                found.append(row[0])
        return found

    before = counts()
    at = _ask(f"when is the midterm {ASKED}")
    assert ASKED in _everything(at) and REPLY_SENTINEL in _everything(at)

    again = at.run()  # the next render holds neither the question nor the answer
    for needle in (ASKED, REPLY_SENTINEL, "echo midterm"):
        assert needle not in _everything(again)
        assert needle not in repr(again.session_state.to_dict())
        assert needle not in repr(again.session_state._state._state)  # widget values too

    out = capfd.readouterr()
    _assert_clean(caplog.text, out.out, out.err, extra=(ASKED, REPLY_SENTINEL, "midterm"))
    for needle in (ASKED, REPLY_SENTINEL):
        assert _files_with(needle.encode(), home, temp, REPO / ".streamlit") == []
    assert not (REPO / ".streamlit" / "cache").exists()
    assert counts() == before


def test_the_page_caches_nothing_and_connects_read_only() -> None:
    page = (VIEWS / "ask.py").read_text(encoding="utf-8")
    logic = Path(ask.__file__).read_text(encoding="utf-8")
    for source in (page, logic):
        assert "st.cache" not in source.replace("no st.cache", "")
        assert "st.session_state" not in source and "lru_cache" not in source
    assert "SET default_transaction_read_only = on" in logic


def test_a_failure_shows_a_fixed_message_and_logs_the_type_only(
    embedded: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with StubLlm() as gone:
        monkeypatch.setenv("GCW_LLM_URL", gone.url)
    at = _ask(f"when is the midterm {ASKED}")
    assert not at.exception
    assert [e.value for e in at.error] == [ask.UNAVAILABLE]
    assert "ProviderUnavailable" in caplog.text
    _assert_clean(caplog.text, extra=(ASKED, "midterm"))


def test_a_blank_question_asks_nothing(embedded: str, llm: StubLlm) -> None:
    at = _ask("   ")
    assert _shown(at) == [] and llm.requests == []


def test_the_pipeline_here_is_make_asks(embedded: str, llm: StubLlm) -> None:
    found = ask.answer_question("when is the midterm")
    assert found.outcome == "answered" and llm.requests
    assert _reply(llm.requests[0])  # the same stub reply the make ask tests use


# --- Wrapped -----------------------------------------------------------------------------------


@pytest.fixture
def profiles(clean_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[psycopg.Connection]:
    monkeypatch.setenv("DASHBOARD_DATABASE_URL", clean_db)
    monkeypatch.setenv("GCW_READER_PASSWORD", "unused")
    get_dashboard_settings.cache_clear()
    st.cache_data.clear()
    with psycopg.connect(clean_db, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        conn.execute("DROP TABLE IF EXISTS analytics.mart_member_wrapped")
        conn.execute(
            "CREATE TABLE analytics.mart_member_wrapped ("
            "member_key text, thread_id text, member_name text, messages_sent bigint, "
            "thread_share float8, most_active_hour int, most_active_weekday int, "
            "median_reply_seconds float8, first_message_date date, last_message_date date, "
            "top_topic_1 text, top_topic_2 text, top_topic_3 text, positive_share float8, "
            "neutral_share float8, negative_share float8)"
        )
        yield conn
        conn.execute("DROP TABLE IF EXISTS analytics.mart_member_wrapped")
    get_dashboard_settings.cache_clear()


def _insert_profiles(conn: psycopg.Connection) -> None:
    conn.execute(
        "INSERT INTO analytics.mart_member_wrapped VALUES "
        "('k1', '111', 'SynthAna', 1234, 0.25, 22, 2, 190, '2024-01-02', '2026-03-05', "
        " 'plans', 'food', 'banter', 0.4, 0.5, 0.1), "
        "('k2', '111', 'SynthBo', 1, 0.001, 0, 7, NULL, '2024-01-02', '2024-01-02', "
        " NULL, NULL, NULL, NULL, NULL, NULL)"
    )


def test_the_wrapped_page_shows_one_card_for_the_picked_member(
    profiles: psycopg.Connection,
) -> None:
    _insert_profiles(profiles)
    at = _page("wrapped")
    assert not at.exception and not at.error
    (picker,) = at.selectbox
    assert picker.options == ["SynthAna", "SynthBo"]  # only members in the mart
    assert _shown(at)[:2] == ["SynthAna", "Messages sent: 1,234"]
    assert "Most active: Tuesdays at 10 pm" in _shown(at)
    assert "Typical reply time: 3 minutes" in _shown(at)
    assert wrapped.CARD_NOTE in [c.value for c in at.caption]

    other = picker.set_value(1).run()
    shown = _shown(other)
    assert shown[0] == "SynthBo" and "SynthAna" not in shown
    assert "Typical reply time: no replies yet" in shown
    assert "Top topics: not tagged yet" in shown and "Mood: not tagged yet" in shown
    assert "Most active: Sundays at 12 am" in shown


def test_the_wrapped_page_is_not_cached(profiles: psycopg.Connection) -> None:
    _insert_profiles(profiles)
    at = _page("wrapped")
    profiles.execute("DELETE FROM analytics.mart_member_wrapped WHERE member_key = 'k1'")
    assert at.run().selectbox[0].options == ["SynthBo"]


def test_with_no_profiles_the_page_says_so(profiles: psycopg.Connection) -> None:
    at = _page("wrapped")
    assert [i.value for i in at.info] == [wrapped.NO_PROFILES]
    assert not at.selectbox and not at.error


def test_without_the_mart_the_page_shows_a_fixed_message(
    profiles: psycopg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    profiles.execute("DROP TABLE analytics.mart_member_wrapped")
    at = _page("wrapped")
    assert not at.exception and len(at.error) == 1
    assert "UndefinedTable" in caplog.text and "mart_member_wrapped" not in _everything(at)


def test_the_query_names_every_card_column(profiles: psycopg.Connection, clean_db: str) -> None:
    _insert_profiles(profiles)
    frame = queries.wrapped_profiles(clean_db)
    assert tuple(frame.columns) == queries.WRAPPED_COLUMNS
    assert wrapped.card_lines(frame.to_dict("records")[1])[3].endswith("no replies yet")
