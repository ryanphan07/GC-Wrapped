"""The tagging run against the real gcw_test database (D88, D89).

SQL, the runner, the commands and the llama.cpp client run for real, against the stub server in
tests/tagging_stub.py. A scripted fake provider stands in for the model where a test needs control
of the answers. The dbt view analytics.fct_message_eligibility is stood in for by a view with the
same definition over the raw tables, as in the labeling and coverage tests.
"""

import hashlib
import logging
import re
from collections.abc import Iterator

import psycopg
import pytest

from gcwrapped.config import get_database_settings
from gcwrapped.labeling.source import load_eligible
from gcwrapped.tagging import commands, report
from gcwrapped.tagging.llamacpp import LlamaCppProvider
from gcwrapped.tagging.prompt import PROMPT_VERSION
from gcwrapped.tagging.provider import ProviderUnavailable, Tag, TagFailure
from gcwrapped.tagging.runner import run_tagging
from tagging_stub import StubLlm

pytestmark = pytest.mark.integration

SENTINEL = "SENTINELTEXT"
# (thread, sender, content, minute). Ana yes, Bo no (declined), Cy blank, Eve local. SENDERNAME
# strings appear only as sender names: they must never reach a prompt.
ROWS = [
    ("111", "SENDERANA", f"{SENTINEL} a1", 1),
    ("111", "SENDERBO", f"{SENTINEL} declined", 2),
    ("111", "SENDERANA", f"{SENTINEL} a2", 3),
    ("111", "SENDERCY", f"{SENTINEL} undecided", 4),
    ("111", "SENDERANA", f"{SENTINEL} a3", 5),
    ("111", "SENDERANA", "   ", 6),
    ("111", "SENDERANA", None, 7),
    ("222", "SENDERDEE", f"{SENTINEL} unlisted", 1),
    ("333", "SENDEREVE", f"{SENTINEL} e1", 1),
    ("333", "SENDEREVE", f"{SENTINEL} e2", 2),
]
CONSENT = [
    ("111", "SENDERANA", "yes"),
    ("111", "SENDERBO", "no"),
    ("111", "SENDERCY", ""),
    ("333", "SENDEREVE", "local"),
]
ELIGIBLE = 5  # a1 a2 a3 e1 e2


def _key(thread: str, n: int) -> str:
    return hashlib.sha256(f"{thread}/{n}".encode()).hexdigest()


@pytest.fixture
def warehouse(migrated_db: str) -> Iterator[str]:
    get_database_settings.cache_clear()
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        conn.execute("DROP VIEW IF EXISTS analytics.fct_message_eligibility")
        run = conn.execute(
            "INSERT INTO raw.load_runs (roots_count) VALUES (1) RETURNING load_id"
        ).fetchone()
        assert run is not None
        for n, (thread, sender, content, minute) in enumerate(ROWS):
            conn.execute(
                "INSERT INTO raw.messages (message_key, platform, thread_id, sender_name, sent_at, "
                "content, media_count, source_file, load_id) VALUES "
                "(%s, 'instagram', %s, %s, '2024-01-01T00:00:00Z'::timestamptz "
                "+ make_interval(mins => %s), %s, 0, 'x', %s)",
                (_key(thread, n), thread, sender, minute, content, run[0]),
            )
        for thread, sender, consent in CONSENT:
            conn.execute(
                "INSERT INTO raw.sender_consent (thread_id, sender_name, consent) "
                "VALUES (%s, %s, %s)",
                (thread, sender, consent),
            )
        conn.execute(
            "CREATE VIEW analytics.fct_message_eligibility AS "
            "SELECT m.message_key, m.thread_id, m.sender_name, "
            "coalesce(c.consent IN ('yes', 'local'), false) AS local_ok, "
            "coalesce(c.consent = 'yes', false) AS external_ok "
            "FROM raw.messages AS m LEFT JOIN raw.sender_consent AS c "
            "ON c.thread_id = m.thread_id AND c.sender_name = m.sender_name"
        )
    yield migrated_db
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DROP VIEW IF EXISTS analytics.fct_message_eligibility")


class Fake:
    """A scripted TagProvider: records every prompt, answers deterministically."""

    def __init__(
        self, model_id: str = "fake", fail_on: str = "", unavailable_after: int | None = None
    ) -> None:
        self.model_id = model_id
        self.fail_on = fail_on
        self.unavailable_after = unavailable_after
        self.prompts: list[tuple[str, str]] = []

    def tag(self, system: str, user: str) -> Tag:
        if self.unavailable_after is not None and len(self.prompts) >= self.unavailable_after:
            raise ProviderUnavailable
        self.prompts.append((system, user))
        if self.fail_on and self.fail_on in user.splitlines()[-1]:  # the message, not context:
            raise TagFailure
        return Tag("food", "neutral")


def _rows(url: str) -> list[tuple[str, str, str, str, str]]:
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT message_key, topic, sentiment, model_id, prompt_version "
            "FROM enrich.message_tags ORDER BY message_key"
        ).fetchall()


def test_every_local_eligible_message_is_tagged_and_a_rerun_tags_none(warehouse: str) -> None:
    first = run_tagging(warehouse, Fake())
    assert (first.eligible, first.tagged, first.failures, first.pruned) == (
        ELIGIBLE,
        ELIGIBLE,
        0,
        0,
    )
    assert len(_rows(warehouse)) == ELIGIBLE
    again = run_tagging(warehouse, Fake())
    assert (again.tagged, again.already_tagged, again.failures) == (0, ELIGIBLE, 0)
    assert len(_rows(warehouse)) == ELIGIBLE


def test_declined_blank_unlisted_and_textless_messages_are_never_sent_or_tagged(
    warehouse: str,
) -> None:
    fake = Fake()
    run_tagging(warehouse, fake)
    sent = "\n".join(user for _, user in fake.prompts)
    for excluded in ("declined", "undecided", "unlisted"):
        assert excluded not in sent
    assert len(fake.prompts) == ELIGIBLE
    tagged = {r[0] for r in _rows(warehouse)}
    assert tagged == {
        _key("111", 0),
        _key("111", 2),
        _key("111", 4),
        _key("333", 8),
        _key("333", 9),
    }


def test_prompts_carry_the_labelers_context_and_no_sender_names(warehouse: str) -> None:
    fake = Fake()
    run_tagging(warehouse, fake)
    index = load_eligible(warehouse)
    a3 = index.get(_key("111", 4))
    expected_context = [m.text for m in index.before(a3.message_id, 2)]
    assert expected_context == [f"{SENTINEL} a1", f"{SENTINEL} a2"]  # "declined" is skipped
    user = next(u for _, u in fake.prompts if u.endswith(f"Message to label:\n{a3.text}"))
    assert user.splitlines()[1:3] == [f"- {t}" for t in expected_context]
    everything = "\n".join(s + u for s, u in fake.prompts)
    assert "SENDER" not in everything  # no sender names, in any prompt
    assert "111" not in everything and "333" not in everything  # no thread ids either


def test_a_failure_is_counted_stores_nothing_and_is_retried_on_the_next_run(
    warehouse: str,
) -> None:
    result = run_tagging(warehouse, Fake(fail_on=f"{SENTINEL} a2"))
    assert (result.tagged, result.failures) == (ELIGIBLE - 1, 1)
    assert _key("111", 2) not in {r[0] for r in _rows(warehouse)}
    retry = run_tagging(warehouse, Fake())
    assert (retry.tagged, retry.failures, retry.already_tagged) == (1, 0, ELIGIBLE - 1)


def test_limit_caps_the_attempts_and_a_rerun_finishes(warehouse: str) -> None:
    assert run_tagging(warehouse, Fake(), limit=2).tagged == 2
    rest = run_tagging(warehouse, Fake())
    assert (rest.already_tagged, rest.tagged) == (2, ELIGIBLE - 2)


def test_an_unreachable_server_stops_the_run_but_keeps_what_was_tagged(warehouse: str) -> None:
    with pytest.raises(ProviderUnavailable):
        run_tagging(warehouse, Fake(unavailable_after=3), batch_size=100)
    assert len(_rows(warehouse)) == 3  # the pending batch was committed on the way out
    finish = run_tagging(warehouse, Fake())
    assert (finish.already_tagged, finish.tagged) == (3, ELIGIBLE - 3)


def test_batches_are_committed_as_the_run_goes(warehouse: str) -> None:
    class Watching(Fake):
        def __init__(self, url: str) -> None:
            super().__init__()
            self.url = url
            self.seen: list[int] = []

        def tag(self, system: str, user: str) -> Tag:
            self.seen.append(len(_rows(self.url)))  # what a second connection can see
            return super().tag(system, user)

    watcher = Watching(warehouse)
    run_tagging(warehouse, watcher, batch_size=2)
    assert watcher.seen == [0, 0, 2, 2, 4]


def test_a_new_prompt_version_or_model_adds_rows_and_keeps_the_old(warehouse: str) -> None:
    run_tagging(warehouse, Fake("model-a"), prompt_version="p1")
    second = run_tagging(warehouse, Fake("model-a"), prompt_version="p2")
    third = run_tagging(warehouse, Fake("model-b"), prompt_version="p2")
    assert (second.already_tagged, second.tagged) == (0, ELIGIBLE)
    assert (third.already_tagged, third.tagged) == (0, ELIGIBLE)
    assert len(_rows(warehouse)) == 3 * ELIGIBLE


def test_revoking_a_senders_consent_prunes_their_tags(warehouse: str) -> None:
    run_tagging(warehouse, Fake("model-a"), prompt_version="p1")
    run_tagging(warehouse, Fake("model-b"))
    assert len(_rows(warehouse)) == 2 * ELIGIBLE
    with psycopg.connect(warehouse, autocommit=True) as conn:
        conn.execute("UPDATE raw.sender_consent SET consent = 'no' WHERE sender_name = 'SENDEREVE'")
    result = run_tagging(warehouse, Fake("model-b"))
    assert result.pruned == 4  # Eve's two messages, under both models
    assert result.eligible == ELIGIBLE - 2 and result.tagged == 0
    remaining = {r[0] for r in _rows(warehouse)}
    assert remaining == {_key("111", 0), _key("111", 2), _key("111", 4)}
    # A blank value prunes too, and a later "local" brings the sender back as untagged work.
    with psycopg.connect(warehouse, autocommit=True) as conn:
        conn.execute(
            "UPDATE raw.sender_consent SET consent = 'local' WHERE sender_name = 'SENDEREVE'"
        )
    back = run_tagging(warehouse, Fake("model-b"))
    assert (back.pruned, back.tagged) == (0, 2)


def test_the_table_rejects_an_invalid_label(warehouse: str) -> None:
    with (
        psycopg.connect(warehouse, autocommit=True) as conn,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        conn.execute(
            "INSERT INTO enrich.message_tags (message_key, topic, sentiment, model_id, "
            "prompt_version, run_id) VALUES (%s, 'cooking', 'neutral', 'm', 'p', 'r')",
            (_key("111", 0),),
        )


def test_deleting_a_message_deletes_its_tags(warehouse: str) -> None:
    run_tagging(warehouse, Fake())
    with psycopg.connect(warehouse, autocommit=True) as conn:
        conn.execute("DELETE FROM raw.messages WHERE message_key = %s", (_key("111", 0),))
    assert _key("111", 0) not in {r[0] for r in _rows(warehouse)}


# --- the command, end to end through the real client and the stub server --------------------------


def test_make_tag_prints_fixed_lines_only_and_logs_no_text(
    warehouse: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with StubLlm() as stub:
        monkeypatch.setenv("GCW_LLM_URL", stub.url)
        assert commands.run_tag(start=None) == 0
        out = capsys.readouterr()
        assert len(stub.requests) == ELIGIBLE
        sent = "\n".join(str(r) for r in stub.requests)
    lines = out.out.splitlines()
    report.check_lines(lines, report.TAG_SHAPES)
    assert lines[0] == "target database: gcw_test"
    assert lines[2] == f"prompt version: {PROMPT_VERSION}"
    assert f"tagged this run: {ELIGIBLE}" in lines and "failures: 0" in lines
    assert sum(int(m) for m in re.findall(r"^topic \w+: (\d+)$", out.out, re.M)) == ELIGIBLE
    assert sum(int(m) for m in re.findall(r"^sentiment \w+: (\d+)$", out.out, re.M)) == ELIGIBLE
    assert lines[-1] == "tag: ok"
    assert SENTINEL in sent  # the sentinel really was in the requests, so the checks below bite
    for stream in (out.out, out.err, caplog.text):
        assert SENTINEL not in stream and "SENDER" not in stream
    # An immediate rerun tags nothing.
    with StubLlm() as stub:
        monkeypatch.setenv("GCW_LLM_URL", stub.url)
        assert commands.run_tag(start=None) == 0
        assert stub.requests == []
    again = capsys.readouterr().out
    assert "tagged this run: 0" in again and f"already tagged: {ELIGIBLE}" in again


def test_make_tag_failure_line_has_a_step_and_a_class_name_only(
    warehouse: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with StubLlm(mode="invalid") as stub:
        monkeypatch.setenv("GCW_LLM_URL", stub.url)
        assert commands.run_tag(start=None) == 0  # failures are counted, not fatal
    out = capsys.readouterr().out
    assert f"failures: {ELIGIBLE}" in out and "tagged this run: 0" in out
    # Nothing listening: the run fails at tagging, naming only the exception class.
    monkeypatch.setenv("GCW_LLM_URL", "http://127.0.0.1:1")
    assert commands.run_tag(start=None) == 1
    assert capsys.readouterr().out == "tag: failed at tagging (ProviderUnavailable)\n"


def test_make_tag_refuses_a_non_local_url_before_starting_anything(
    warehouse: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[bool] = []
    monkeypatch.setenv("GCW_LLM_URL", "http://example.com:8081")
    assert commands.run_tag(start=lambda: started.append(True)) == 1
    assert capsys.readouterr().out == "tag: failed at llm url (NonLocalUrlError)\n"
    assert started == [] and _rows(warehouse) == []


def test_make_tag_refuses_a_database_outside_the_allowed_list(
    warehouse: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[bool] = []
    monkeypatch.setenv("GCW_DB_NAME", "gcw_other")
    monkeypatch.setenv("DATABASE_URL", "")
    get_database_settings.cache_clear()
    assert commands.run_tag(start=lambda: started.append(True)) == 1
    assert capsys.readouterr().out == "tag: failed at database (UnexpectedDatabaseError)\n"
    assert started == []


def test_make_tag_prunes_after_a_consent_change_through_the_command(
    warehouse: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with StubLlm() as stub:
        monkeypatch.setenv("GCW_LLM_URL", stub.url)
        assert commands.run_tag(start=None) == 0
        with psycopg.connect(warehouse, autocommit=True) as conn:
            conn.execute(
                "UPDATE raw.sender_consent SET consent = 'no' WHERE sender_name = 'SENDERANA'"
            )
        capsys.readouterr()
        assert commands.run_tag(start=None) == 0
    assert "pruned: 3" in capsys.readouterr().out
    assert len(_rows(warehouse)) == 2


def test_the_real_client_against_the_stub_stores_valid_labels(warehouse: str) -> None:
    with StubLlm() as stub:
        result = run_tagging(warehouse, LlamaCppProvider(stub.url, model_id="stub-model"))
    assert result.tagged == ELIGIBLE
    assert {r[3] for r in _rows(warehouse)} == {"stub-model"}


# --- tag-bench ---------------------------------------------------------------------------------


def test_bench_scores_against_labels_and_stores_nothing(
    warehouse: str, capsys: pytest.CaptureFixture[str]
) -> None:
    class Gold(Fake):
        def tag(self, system: str, user: str) -> Tag:
            super().tag(system, user)
            return (
                Tag("food", "neutral") if "a1" in user.splitlines()[-1] else Tag("work", "neutral")
            )

    labels = {_key("111", 0): ("food", "neutral"), _key("111", 2): ("plans", "positive")}
    result, benchmarked, topic_acc, sentiment_acc = commands.bench(warehouse, Gold(), labels)
    assert benchmarked == 2 and result.tagged == 2 and result.failures == 0
    assert (topic_acc, sentiment_acc) == (0.5, 0.5)
    assert _rows(warehouse) == []
    lines = report.render_bench("gcw_dev", result, benchmarked, topic_acc, sentiment_acc)
    assert "synthetic topic accuracy: 0.500" in lines


def test_bench_refuses_anything_but_gcw_dev(
    warehouse: str, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[bool] = []
    assert commands.run_bench(start=lambda: started.append(True)) == 1  # the database is gcw_test
    assert capsys.readouterr().out == "tag-bench: failed at database (UnexpectedDatabaseError)\n"
    assert started == []
