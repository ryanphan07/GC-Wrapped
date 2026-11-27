"""Embedding, retrieval, the retrieval eval and the pruning of vectors, against gcw_test.

SQL, pgvector, full-text search, the runner, the commands and the embedding client run for real,
against the stub server in tests/embedding_stub.py. The dbt objects are stood in for by views over
the raw tables: eligibility and user text with the dbt definitions (see test_tagging_integration),
and analytics.fct_messages with the dbt 60-minute session rule and local send time. The two views
search and excerpts read (D131) are the dbt models' own SQL (tests/ask_view_support.py). Sender
names and message text are sentinels that must never be printed.
"""

import hashlib
import logging
import re
import stat
import time
from collections.abc import Iterator, Sequence
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from ask_permissions import set_up_gcw_ask
from ask_view_support import create_ask_views, drop_ask_views
from conftest import after_memory_lines
from embedding_stub import StubEmbed
from gcwrapped import consent_prune, purge
from gcwrapped.config import PasswordSettings, get_database_settings
from gcwrapped.dashboard import ask_role
from gcwrapped.labeling.files import write_questions
from gcwrapped.labeling.schema import QuestionLabel
from gcwrapped.labeling.source import load_eligible
from gcwrapped.retrieval import commands, evaluation, report, window
from gcwrapped.retrieval.client import LlamaCppEmbedder
from gcwrapped.retrieval.embed import run_embed
from gcwrapped.retrieval.search import Retriever
from gcwrapped.tagging import commands as tagging
from gcwrapped.tagging import model
from gcwrapped.tagging.provider import ProviderUnavailable
from gcwrapped.tagging.report import check_lines
from placeholder_support import create_text_view, drop_text_view

pytestmark = pytest.mark.integration

EMBED_ID = model.EMBED_MODEL_ID
SENTINEL = "SENTINELTEXT"
STAMP = "2026-11-10T00:00:00+00:00"
HEX64 = re.compile(r"[0-9a-f]{64}")
# name: (thread, sender, words, minute). Ana and Dee yes, Cy local, Bo no. Minutes more than 60
# apart start a new session: thread 111 has two.
ROWS = {
    "a1": ("111", "SENDERANA", "alpha pizza friday", 0),
    "d1": ("111", "SENDERDEE", "bravo works for me", 1),
    "no": ("111", "SENDERBO", "declined secret", 2),
    "cy": ("111", "SENDERCY", "charlie bringing soda", 3),
    "a2": ("111", "SENDERANA", "delta see you", 4),
    "ph": ("111", "SENDERANA", "SENDERANA liked a message", 5),
    "a3": ("111", "SENDERANA", "echo midterm tomorrow", 200),
    "d2": ("111", "SENDERDEE", "foxtrot good luck", 201),
    "e1": ("333", "SENDEREVE", "golf ramen downtown", 1),
    "e2": ("333", "SENDEREVE", "hotel booked already", 2),
}
CONSENT = [
    ("111", "SENDERANA", "yes"),
    ("111", "SENDERDEE", "yes"),
    ("111", "SENDERBO", "no"),
    ("111", "SENDERCY", "local"),
    ("333", "SENDEREVE", "yes"),
]
ID = {name: hashlib.sha256(name.encode()).hexdigest() for name in ROWS}
NAME = {key: name for name, key in ID.items()}
ELIGIBLE = {"a1", "d1", "cy", "a2", "a3", "d2", "e1", "e2"}
WINDOWS = {
    "a1": ["a1", "d1", "cy"],
    "d1": ["a1", "d1", "cy", "a2"],
    "cy": ["a1", "d1", "cy", "a2"],
    "a2": ["d1", "cy", "a2"],
    "a3": ["a3", "d2"],
    "d2": ["a3", "d2"],
    "e1": ["e1", "e2"],
    "e2": ["e1", "e2"],
}
SESSION_VIEW_SQL = """
CREATE VIEW analytics.fct_messages AS
SELECT message_key,
       thread_id || ':s' || sum(starts) OVER (PARTITION BY thread_id ORDER BY sent_at, message_key)
           AS session_id,
       sent_at AT TIME ZONE 'America/Los_Angeles' AS sent_at_local
FROM (
    SELECT message_key, thread_id, sent_at,
           coalesce(sent_at - lag(sent_at) OVER (PARTITION BY thread_id
                    ORDER BY sent_at, message_key) > interval '60 minutes', true)::int AS starts
    FROM raw.messages
) AS s
"""


def _content(name: str) -> str:
    words = ROWS[name][2]
    return words if name == "ph" else f"{words} {SENTINEL}"


def _insert(conn: psycopg.Connection, name: str, load_id: int) -> None:
    thread, sender, _, minute = ROWS[name]
    conn.execute(
        "INSERT INTO raw.messages (message_key, platform, thread_id, sender_name, sent_at, "
        "content, media_count, source_file, load_id) VALUES (%s, 'instagram', %s, %s, "
        "'2024-01-01T00:00:00Z'::timestamptz + make_interval(mins => %s), %s, 0, 'x', %s)",
        (ID[name], thread, sender, minute, _content(name), load_id),
    )


def _drop_views(conn: psycopg.Connection) -> None:
    drop_ask_views(conn)  # they read the others
    drop_text_view(conn)
    conn.execute("DROP VIEW IF EXISTS analytics.fct_message_eligibility")
    conn.execute("DROP VIEW IF EXISTS analytics.fct_messages")


@pytest.fixture
def warehouse(migrated_db: str) -> Iterator[str]:
    get_database_settings.cache_clear()
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        _drop_views(conn)
        run = conn.execute(
            "INSERT INTO raw.load_runs (roots_count) VALUES (1) RETURNING load_id"
        ).fetchone()
        assert run is not None
        for name in ROWS:
            _insert(conn, name, run[0])
        for row in CONSENT:
            conn.execute(
                "INSERT INTO raw.sender_consent (thread_id, sender_name, consent) "
                "VALUES (%s, %s, %s)",
                row,
            )
        conn.execute(
            "CREATE VIEW analytics.fct_message_eligibility AS "
            "SELECT m.message_key, m.thread_id, m.sender_name, "
            "coalesce(c.consent IN ('yes', 'local'), false) AS local_ok, "
            "coalesce(c.consent = 'yes', false) AS external_ok "
            "FROM raw.messages AS m LEFT JOIN raw.sender_consent AS c "
            "ON c.thread_id = m.thread_id AND c.sender_name = m.sender_name"
        )
        create_text_view(conn)
        conn.execute(SESSION_VIEW_SQL)
        create_ask_views(conn)
    yield migrated_db
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        _drop_views(conn)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubEmbed]:
    with StubEmbed() as server:
        monkeypatch.setenv("GCW_EMBED_URL", server.url)
        yield server


def _client(stub: StubEmbed) -> LlamaCppEmbedder:
    return LlamaCppEmbedder(stub.url, model_id=EMBED_ID)


def _stored(url: str) -> dict[str, list[str]]:
    """name -> the names in its stored window."""
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT message_key, window_message_keys FROM enrich.message_embeddings"
        ).fetchall()
    return {NAME[key]: [NAME[m] for m in members] for key, members in rows}


def _revoke(url: str, sender: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "UPDATE raw.sender_consent SET consent = 'no' WHERE sender_name = %s", (sender,)
        )


def _search(url: str, stub: StubEmbed, method: str, query: str, k: int = 10) -> list[str]:
    with psycopg.connect(url, autocommit=True) as conn:
        retriever = Retriever(conn, _client(stub))
        search = retriever.vector if method == "vector" else getattr(retriever, method)
        return [NAME[h.message_id] for h in search(query, k)]


# --- embedding ---------------------------------------------------------------------------------


def test_every_eligible_message_gets_one_vector_over_its_eligible_session_window(
    warehouse: str, stub: StubEmbed
) -> None:
    result = run_embed(warehouse, _client(stub), batch_size=3)
    assert (result.eligible, result.embedded, result.already_current, result.failures) == (
        8,
        8,
        0,
        0,
    )
    assert _stored(warehouse) == WINDOWS
    assert [len(r["input"]) for r in stub.requests] == [3, 3, 2]
    sent = [text for r in stub.requests for text in r["input"]]
    assert (
        f"SENDERANA: alpha pizza friday {SENTINEL}\n"
        f"SENDERDEE: bravo works for me {SENTINEL}\n"
        f"SENDERCY: charlie bringing soda {SENTINEL}"
    ) in sent
    joined = "\n".join(sent)
    assert "declined" not in joined and "liked a message" not in joined
    assert "Instruct:" not in joined  # stored windows get no query prefix
    with psycopg.connect(warehouse) as conn:
        rows = conn.execute(
            "SELECT DISTINCT embed_version, model_id, vector_dims(embedding), "
            "window_tsv @@ 'sentineltext'::tsquery FROM enrich.message_embeddings"
        ).fetchall()
    assert rows == [(window.embed_version(), EMBED_ID, 1024, True)]


def test_a_second_run_embeds_nothing(warehouse: str, stub: StubEmbed) -> None:
    run_embed(warehouse, _client(stub))
    stub.requests.clear()
    again = run_embed(warehouse, _client(stub))
    assert (again.embedded, again.already_current, again.failures) == (0, 8, 0)
    assert stub.requests == []


def test_a_new_version_re_embeds_everything_in_place(
    warehouse: str, stub: StubEmbed, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = run_embed(warehouse, _client(stub))
    monkeypatch.setattr(window, "QUERY_INSTRUCTION", "Query:")
    second = run_embed(warehouse, _client(stub))
    assert second.version != first.version
    assert (second.embedded, second.already_current) == (8, 0)
    with psycopg.connect(warehouse) as conn:
        rows = conn.execute(
            "SELECT embed_version, count(*) FROM enrich.message_embeddings GROUP BY 1"
        ).fetchall()
    assert rows == [(second.version, 8)]  # one row per message, replaced


def test_a_window_that_changed_since_it_was_embedded_is_embedded_again(
    warehouse: str, stub: StubEmbed
) -> None:
    run_embed(warehouse, _client(stub))
    with psycopg.connect(warehouse, autocommit=True) as conn:
        conn.execute("UPDATE raw.sender_consent SET consent = 'yes' WHERE sender_name = 'SENDERBO'")
    result = run_embed(warehouse, _client(stub))  # Bo's message joins four windows
    assert (result.eligible, result.embedded, result.already_current) == (9, 5, 4)
    assert _stored(warehouse)["d1"] == ["a1", "d1", "no", "cy"]


def test_a_refused_input_costs_that_message_only_and_is_retried(
    warehouse: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with StubEmbed(mode="reject", reject_text="foxtrot") as bad:
        result = run_embed(warehouse, _client(bad))
    assert (result.embedded, result.failures) == (6, 2)  # a3 and d2 both carry that text
    assert set(_stored(warehouse)) == ELIGIBLE - {"a3", "d2"}
    with StubEmbed() as good:
        retry = run_embed(warehouse, _client(good))
    assert (retry.embedded, retry.already_current, retry.failures) == (2, 6, 0)


def test_a_server_that_goes_away_keeps_the_committed_batches(
    warehouse: str, stub: StubEmbed
) -> None:
    client = _client(stub)

    class Dies:
        model_id = EMBED_ID
        calls = 0

        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            self.calls += 1
            if self.calls > 1:
                raise ProviderUnavailable
            return client.embed(texts)

    with pytest.raises(ProviderUnavailable):
        run_embed(warehouse, Dies(), batch_size=3)
    assert len(_stored(warehouse)) == 3
    finish = run_embed(warehouse, client)
    assert (finish.already_current, finish.embedded) == (3, 5)


# --- search ------------------------------------------------------------------------------------


def test_vector_search_ranks_the_closest_windows_first_and_returns_centres(
    warehouse: str, stub: StubEmbed
) -> None:
    run_embed(warehouse, _client(stub))
    top = _search(warehouse, stub, "vector", "SENDEREVE golf ramen hotel", k=2)
    assert set(top) == {"e1", "e2"}
    assert len(_search(warehouse, stub, "vector", "anything", k=5)) == 5
    with psycopg.connect(warehouse, autocommit=True) as conn:
        hits = Retriever(conn, _client(stub)).vector("midterm echo", 3)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert {NAME[h.message_id] for h in hits[:2]} == {"a3", "d2"}


def test_full_text_search_matches_any_query_word_without_stemming(
    warehouse: str, stub: StubEmbed
) -> None:
    run_embed(warehouse, _client(stub))
    assert set(_search(warehouse, stub, "text", "where is the ramen midterm")) == {
        "e1",
        "e2",
        "a3",
        "d2",
    }
    assert _search(warehouse, stub, "text", "midterms") == []  # simple configuration
    for odd in ("", "🎉", "!!!", 'it\'s \\ "quoted" a&b | c:* <-> (d)', "x\x00y"):
        _search(warehouse, stub, "text", odd)  # never a syntax error


def test_hybrid_fuses_the_vector_and_full_text_lists(warehouse: str, stub: StubEmbed) -> None:
    run_embed(warehouse, _client(stub))
    hybrid = _search(warehouse, stub, "hybrid", "ramen", k=10)
    assert set(hybrid) == ELIGIBLE  # the vector list holds every window
    assert set(hybrid[:2]) == {"e1", "e2"}  # the two that also match as text
    assert len(_search(warehouse, stub, "hybrid", "ramen", k=3)) == 3


@pytest.mark.parametrize("method", ["vector", "text", "hybrid"])
def test_a_revocation_hides_every_window_it_shaped_before_anything_is_pruned(
    warehouse: str, stub: StubEmbed, method: str
) -> None:
    run_embed(warehouse, _client(stub))
    _revoke(warehouse, "SENDERCY")
    found = set(_search(warehouse, stub, method, f"{SENTINEL} charlie alpha delta bravo", k=20))
    assert found == {"a3", "d2", "e1", "e2"}  # not cy, nor a1, d1, a2 whose windows held it
    assert len(_stored(warehouse)) == 8


def _view_names(url: str, view: str) -> set[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(f"SELECT message_key FROM analytics.{view}").fetchall()
    return {NAME[key] for (key,) in rows}


def test_a_revocation_takes_the_senders_own_rows_out_of_both_views_at_once(
    warehouse: str, stub: StubEmbed
) -> None:
    run_embed(warehouse, _client(stub))
    assert _view_names(warehouse, "ask_messages") == ELIGIBLE  # not Bo, not the placeholder
    assert _view_names(warehouse, "ask_message_embeddings") == ELIGIBLE
    _revoke(warehouse, "SENDERCY")
    assert _view_names(warehouse, "ask_messages") == ELIGIBLE - {"cy"}
    # a1, d1 and a2 stay until consent-prune, though their windows held Cy's text; their vector and
    # lexemes are hidden (D134, below) and search passes over them
    assert _view_names(warehouse, "ask_message_embeddings") == ELIGIBLE - {"cy"}
    assert len(_stored(warehouse)) == 8
    with psycopg.connect(warehouse, autocommit=True) as conn:
        searchable = Retriever(conn, _client(stub)).searchable_ids()
    assert {NAME[key] for key in searchable} == {"a3", "d2", "e1", "e2"}


def test_rows_of_another_embed_version_are_never_searched(warehouse: str, stub: StubEmbed) -> None:
    run_embed(warehouse, _client(stub))
    with psycopg.connect(warehouse, autocommit=True) as conn:
        conn.execute(
            "UPDATE enrich.message_embeddings SET embed_version = 'old' WHERE message_key = %s",
            (ID["e1"],),
        )
    assert "e1" not in _search(warehouse, stub, "hybrid", "ramen", k=20)


def _vector_rows(url: str) -> dict[str, tuple[list[str], bool, bool]]:
    """name -> the vector view's window for it, and whether its vector and its lexemes are read."""
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT message_key, window_message_keys, embedding IS NOT NULL, "
            "window_tsv IS NOT NULL FROM analytics.ask_message_embeddings"
        ).fetchall()
    return {NAME[key]: ([NAME[m] for m in members], v, t) for key, members, v, t in rows}


def test_a_revocation_hides_the_vector_and_lexemes_of_every_window_it_touched(
    warehouse: str, stub: StubEmbed, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_embed(warehouse, _client(stub))
    set_up_gcw_ask(warehouse, monkeypatch)
    password = PasswordSettings().gcw_ask_password.get_secret_value()
    readers = (warehouse, make_conninfo(warehouse, user=ask_role.ROLE, password=password))
    whole = {name: (members, True, True) for name, members in WINDOWS.items()}
    for url in readers:
        assert _vector_rows(url) == whole
    _revoke(warehouse, "SENDERCY")
    expected = {name: row for name, row in whole.items() if name != "cy"}
    for name in ("a1", "d1", "a2"):
        expected[name] = (WINDOWS[name], False, False)
    for url in readers:
        assert _vector_rows(url) == expected


# --- consent-prune (D105) ----------------------------------------------------------------------


def _prune(capsys: pytest.CaptureFixture[str], apply: bool) -> list[str]:
    get_database_settings.cache_clear()
    assert consent_prune.run(apply=apply, start_db=lambda: None) == 0
    return capsys.readouterr().out.splitlines()


def test_revoking_a_sender_prunes_their_rows_and_every_window_that_held_them(
    warehouse: str,
    stub: StubEmbed,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GCW_LABEL_DIR", str(tmp_path))
    run_embed(warehouse, _client(stub))
    assert "embedding rows to prune: 0" in _prune(capsys, apply=False)
    _revoke(warehouse, "SENDERCY")

    dry = _prune(capsys, apply=False)
    assert dry[1:4] == [
        "ineligible messages with derived records: 1",  # cy
        "tags to prune: 0",
        "embedding rows to prune: 4",  # cy's own, and a1, d1, a2 whose windows held it
    ]
    assert len(_stored(warehouse)) == 8  # a dry run changes nothing
    with psycopg.connect(warehouse, autocommit=True) as conn:
        searchable = Retriever(conn, _client(stub)).searchable_ids()

    applied = _prune(capsys, apply=True)
    assert applied[1:-2] == dry[1:-2] and applied[-2] == "mode: applied"
    assert set(_stored(warehouse)) == {"a3", "d2", "e1", "e2"}
    # what is left is what search could return before: prune's delete is its complement
    assert set(_stored(warehouse)) == {NAME[key] for key in searchable}

    again = _prune(capsys, apply=False)
    assert again[1:4] == [
        "ineligible messages with derived records: 0",
        "tags to prune: 0",
        "embedding rows to prune: 0",
    ]

    rebuilt = run_embed(warehouse, _client(stub))
    assert (rebuilt.eligible, rebuilt.embedded, rebuilt.already_current) == (7, 3, 4)
    stored = _stored(warehouse)
    assert stored["a1"] == stored["d1"] == stored["a2"] == ["a1", "d1", "a2"]
    assert not any("cy" in members for members in stored.values())


def test_prune_drops_stored_retrieval_rows_that_refer_to_an_ineligible_message(
    warehouse: str,
    stub: StubEmbed,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GCW_LABEL_DIR", str(tmp_path))

    def row(n: int, source: str, gold: str, *retrieved: str) -> evaluation.Row:
        return evaluation.Row(
            f"q{n:03d}",
            "0" * 64,
            ID[source],
            (ID[gold],),
            tuple(ID[r] for r in retrieved),
            0.1,
        )

    rows = (
        row(1, "e1", "e1", "e2"),  # stays
        row(2, "e1", "e1", "e2", "cy"),  # retrieved
        row(3, "e1", "cy", "e2"),  # gold
        row(4, "cy", "e1", "e2"),  # source
    )
    evaluation.write_predictions(
        tmp_path, evaluation.Predictions("vector", "0" * 12, "real", "1" * 12, rows)
    )
    path = tmp_path / "retrieval" / "vector.json"
    _revoke(warehouse, "SENDERCY")
    before = path.read_bytes()

    dry = _prune(capsys, apply=False)
    assert "prediction rows to prune: 3" in dry
    assert "ineligible messages with derived records: 1" in dry
    assert path.read_bytes() == before

    _prune(capsys, apply=True)
    assert [r.question_id for r in evaluation.read_predictions(tmp_path, "vector").rows] == ["q001"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "prediction rows to prune: 0" in _prune(capsys, apply=False)


def test_prune_refuses_an_unknown_file_among_the_retrieval_predictions(
    warehouse: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GCW_LABEL_DIR", str(tmp_path))
    (tmp_path / "retrieval").mkdir()
    (tmp_path / "retrieval" / "notes.json").write_text("{}")
    get_database_settings.cache_clear()
    assert consent_prune.run(apply=True, start_db=lambda: None) == 1
    assert capsys.readouterr().out == (
        "consent-prune: failed at prune (UnexpectedPredictionsFileError)\n"
    )


# --- purge-unlisted (D106) ---------------------------------------------------------------------


def test_no_window_crosses_threads_so_purging_by_the_centre_removes_all_of_a_thread(
    warehouse: str,
    stub: StubEmbed,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_embed(warehouse, _client(stub))
    crossing = (
        "SELECT count(*) FROM enrich.message_embeddings e "
        "CROSS JOIN LATERAL unnest(e.window_message_keys) AS w(key) "
        "JOIN raw.messages centre ON centre.message_key = e.message_key "
        "JOIN raw.messages member ON member.message_key = w.key "
        "WHERE member.thread_id <> centre.thread_id"
    )
    with psycopg.connect(warehouse) as conn:
        assert conn.execute(crossing).fetchone() == (0,)
    monkeypatch.setenv("GCW_THREAD_ALLOWLIST", "111")

    assert purge.run(start_db=lambda: None) == 0
    assert "embeddings: 2" in capsys.readouterr().out.splitlines()  # counted by its own delete
    assert len(_stored(warehouse)) == 8

    assert purge.run(confirm=True, start_db=lambda: None) == 0
    assert "embeddings: 2" in capsys.readouterr().out.splitlines()
    stored = _stored(warehouse)
    assert set(stored) == ELIGIBLE - {"e1", "e2"}
    assert not {"e1", "e2"} & {m for members in stored.values() for m in members}


# --- the commands ------------------------------------------------------------------------------


def _assert_clean(*texts: str, extra: tuple[str, ...] = ()) -> None:
    for text in texts:
        assert SENTINEL not in text and "SENDER" not in text
        assert HEX64.search(text) is None  # no message ID
        for fragment in (*(words for _, _, words, _ in ROWS.values()), *extra):
            assert fragment not in text


QUESTIONS = (
    ("which friday is pizza", "a1", ("a1",)),
    ("when is the midterm", "a3", ("a3",)),
    ("where is the ramen", "e1", ("e1",)),
    ("what did the declined one say", "no", ("no",)),
)


def _write_questions(directory: Path) -> Path:
    path = directory / "questions.csv"
    write_questions(
        path,
        [
            QuestionLabel(
                f"q{n:03d}",
                f"{text} {SENTINEL}QUESTION",
                tuple(ID[g] for g in gold),
                ID[source],
                STAMP,
            )
            for n, (text, source, gold) in enumerate(QUESTIONS, start=1)
        ],
    )
    return path


@pytest.fixture
def label_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "labels"
    directory.mkdir()
    _write_questions(directory)
    monkeypatch.setenv("GCW_LABEL_DIR", str(directory))
    return directory


def test_embed_prints_fixed_lines_only_and_a_rerun_embeds_nothing(
    warehouse: str,
    stub: StubEmbed,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    for embedded, current in ((8, 0), (0, 8)):
        assert commands.run_embed(start_db=None, start_service=None) == 0
        out = capsys.readouterr()
        lines = out.out.splitlines()
        check_lines(lines, report.EMBED_SHAPES)
        assert lines[0] == "target database: gcw_test"
        assert lines[3:7] == [
            "messages eligible: 8",
            f"embedded this run: {embedded}",
            f"already current: {current}",
            "failures: 0",
        ]
        _assert_clean(out.out, out.err, caplog.text)


def test_embed_applies_the_migration_a_database_lacks(
    warehouse: str, stub: StubEmbed, capsys: pytest.CaptureFixture[str]
) -> None:
    with psycopg.connect(warehouse, autocommit=True) as conn:
        drop_ask_views(conn)  # a database without the table cannot have a view over it
        conn.execute("DROP TABLE enrich.message_embeddings")
        conn.execute(
            "DELETE FROM schema_migrations WHERE version = '006_enrich_message_embeddings'"
        )
    assert commands.run_embed(start_db=None, start_service=None) == 0
    assert "embedded this run: 8" in capsys.readouterr().out


def test_an_unreachable_server_prints_one_fixed_failure_line(
    warehouse: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with StubEmbed() as gone:
        monkeypatch.setenv("GCW_EMBED_URL", gone.url)
    assert commands.run_embed(start_db=None, start_service=None) == 1
    assert capsys.readouterr().out == "embed: failed at model (ProviderUnavailable)\n"


def test_retrieval_eval_and_compare_print_fixed_lines_and_store_ids_only(
    warehouse: str,
    stub: StubEmbed,
    label_dir: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    run_embed(warehouse, _client(stub))
    secrets = (str(label_dir), "which friday", "ramen")
    for method in ("vector", "hybrid"):
        assert commands.run_retrieval_eval(method, start_db=None, start_service=None) == 0
        out = capsys.readouterr()
        lines = after_memory_lines(out.out)
        check_lines(lines, report.EVAL_SHAPES)
        assert lines[:8] == [
            "target database: gcw_test",
            "questions source: real",
            f"method: {method}",
            f"embed version: {window.embed_version()}",
            "messages not embedded: 0",
            "questions: 4",
            "questions dropped (gold not embedded or ineligible): 1",
            "questions scored: 3",
        ]
        assert "recall@10: 1.000" in lines  # eight windows, ten results
        _assert_clean(out.out, out.err, caplog.text, extra=secrets)
        path = label_dir / "retrieval" / f"{method}.json"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        stored = path.read_text()
        assert SENTINEL not in stored and "SENDER" not in stored and "midterm" not in stored
        rows = evaluation.read_predictions(label_dir, method).rows
        assert [r.question_id for r in rows] == ["q001", "q002", "q003"]
        assert all(len(r.retrieved_message_ids) == 8 and r.seconds > 0 for r in rows)

    assert commands.run_retrieval_compare("vector", "hybrid", start_db=None) == 0
    out = capsys.readouterr()
    lines = out.out.splitlines()
    check_lines(lines, report.COMPARE_SHAPES)
    assert lines[0] == "questions scored: 3" and lines[-1] == "retrieval-compare: ok"
    _assert_clean(out.out, out.err, caplog.text, extra=secrets)


def _refused(missing: int) -> str:
    return (
        f"messages not embedded: {missing}\n"
        "retrieval-eval: failed at corpus (IncompleteCorpusError)\n"
    )


@pytest.mark.parametrize("method", ["vector", "hybrid"])
def test_a_partly_embedded_corpus_is_refused_and_nothing_is_written(
    warehouse: str,
    stub: StubEmbed,
    label_dir: Path,
    capsys: pytest.CaptureFixture[str],
    method: str,
) -> None:
    run_embed(warehouse, _client(stub))
    with psycopg.connect(warehouse, autocommit=True) as conn:
        conn.execute("DELETE FROM enrich.message_embeddings WHERE message_key = %s", (ID["a3"],))
    stub.requests.clear()
    assert commands.run_retrieval_eval(method, start_db=None, start_service=None) == 1
    assert capsys.readouterr().out == _refused(1)
    assert not (label_dir / "retrieval").exists()
    assert stub.requests == []  # refused before any question was searched


def test_a_row_of_an_old_version_or_with_a_revoked_neighbour_does_not_count(
    warehouse: str, stub: StubEmbed, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_embed(warehouse, _client(stub))
    with psycopg.connect(warehouse, autocommit=True) as conn:
        conn.execute(
            "UPDATE enrich.message_embeddings SET embed_version = 'old' WHERE message_key = %s",
            (ID["e1"],),
        )
    assert commands.run_retrieval_eval("vector", start_db=None, start_service=None) == 1
    assert capsys.readouterr().out == _refused(1)
    run_embed(warehouse, _client(stub))
    _revoke(warehouse, "SENDERCY")  # a1, d1 and a2 are eligible, their windows are not
    assert commands.run_retrieval_eval("vector", start_db=None, start_service=None) == 1
    assert capsys.readouterr().out == _refused(3)
    run_embed(warehouse, _client(stub))  # embedding again makes the corpus complete
    assert commands.run_retrieval_eval("vector", start_db=None, start_service=None) == 0
    assert "messages not embedded: 0" in capsys.readouterr().out.splitlines()


def test_eval_with_no_vectors_is_refused(
    warehouse: str, stub: StubEmbed, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert commands.run_retrieval_eval("vector", start_db=None, start_service=None) == 1
    assert capsys.readouterr().out == _refused(8)
    assert not (label_dir / "retrieval").exists()


def test_compare_refuses_runs_made_over_different_corpora(
    warehouse: str, stub: StubEmbed, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_embed(warehouse, _client(stub))
    assert commands.run_retrieval_eval("vector", start_db=None, start_service=None) == 0
    _revoke(warehouse, "SENDEREVE")  # between the runs: a smaller corpus is easier to search
    run_embed(warehouse, _client(stub))
    assert commands.run_retrieval_eval("hybrid", start_db=None, start_service=None) == 0
    first = evaluation.read_predictions(label_dir, "vector")
    second = evaluation.read_predictions(label_dir, "hybrid")
    assert first.corpus_hash == evaluation.corpus_hash(ID[n] for n in ELIGIBLE)
    assert second.corpus_hash == evaluation.corpus_hash(ID[n] for n in ELIGIBLE - {"e1", "e2"})
    capsys.readouterr()
    assert commands.run_retrieval_compare("vector", "hybrid", start_db=None) == 1
    assert capsys.readouterr().out == "retrieval-compare: failed at scoring (CorpusMismatch)\n"
    assert not (label_dir / "experiments").exists()  # no verdict for a refused comparison


# --- promotion (D110) --------------------------------------------------------------------------


def _default_rows(url: str) -> list[tuple[str]]:
    with psycopg.connect(url) as conn:
        return conn.execute("SELECT method FROM enrich.retrieval_default").fetchall()


def _compare_on(label_dir: Path, hybrid_wins: bool) -> None:
    """Run the real compare command over stored runs built so that the named side wins."""

    def stored(method: str, found: bool) -> evaluation.Predictions:
        rows = tuple(
            evaluation.Row(
                f"q{n:03d}",
                "0" * 64,
                ID["e1"],
                (ID["e1"],),
                (ID["e1"] if found else ID["e2"],),
                0.1,
            )
            for n in range(1, 11)
        )
        return evaluation.Predictions(method, window.embed_version(), "real", "1" * 12, rows)

    evaluation.write_predictions(label_dir, stored("vector", False))
    evaluation.write_predictions(label_dir, stored("hybrid", hybrid_wins))
    assert commands.run_retrieval_compare("vector", "hybrid", start_db=None) == 0


def test_retrieval_promote_refuses_without_a_verdict_or_when_it_names_the_other_method(
    warehouse: str, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert commands.run_retrieval_promote("hybrid", start_db=None) == 1
    assert capsys.readouterr().out == "retrieval-promote: failed at verdict (VerdictError)\n"
    _compare_on(label_dir, hybrid_wins=False)
    capsys.readouterr()
    assert commands.run_retrieval_promote("hybrid", start_db=None) == 1
    assert capsys.readouterr().out == "retrieval-promote: failed at verdict (NotWinnerError)\n"
    assert _default_rows(warehouse) == []


def test_retrieval_promote_refuses_a_verdict_made_on_another_embed_version(
    warehouse: str,
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _compare_on(label_dir, hybrid_wins=True)
    capsys.readouterr()
    monkeypatch.setattr(window, "QUERY_INSTRUCTION", "Query:")  # the code has moved on
    assert commands.run_retrieval_promote("hybrid", start_db=None) == 1
    assert capsys.readouterr().out == (
        "retrieval-promote: failed at verdict (EmbedVersionMismatch)\n"
    )
    assert _default_rows(warehouse) == []


def test_retrieval_promote_sets_the_one_row_and_a_later_verdict_can_replace_it(
    warehouse: str, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with psycopg.connect(warehouse) as conn:
        assert evaluation.default_method(conn) == "vector"  # nothing promoted
    _compare_on(label_dir, hybrid_wins=True)
    capsys.readouterr()
    assert commands.run_retrieval_promote("hybrid", start_db=None) == 0
    lines = capsys.readouterr().out.splitlines()
    check_lines(lines, report.PROMOTE_SHAPES)
    assert lines[1:3] == ["promoted method: hybrid", "previous method: none"]
    assert _default_rows(warehouse) == [("hybrid",)]
    with psycopg.connect(warehouse) as conn:
        assert evaluation.default_method(conn) == "hybrid"

    time.sleep(0.002)  # the next verdict file must sort after the first
    _compare_on(label_dir, hybrid_wins=False)
    capsys.readouterr()
    assert commands.run_retrieval_promote("hybrid", start_db=None) == 1
    assert commands.run_retrieval_promote("vector", start_db=None) == 0
    assert "previous method: hybrid" in capsys.readouterr().out.splitlines()
    assert _default_rows(warehouse) == [("vector",)]  # still exactly one row


def test_retrieval_promote_applies_migration_007_on_a_database_that_lacks_it(
    warehouse: str, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with psycopg.connect(warehouse, autocommit=True) as conn:
        conn.execute("DROP TABLE enrich.retrieval_default")
        conn.execute("DELETE FROM schema_migrations WHERE version = '007_enrich_retrieval_default'")
    with psycopg.connect(warehouse) as conn:
        assert evaluation.default_method(conn) == "vector"  # no table yet
    _compare_on(label_dir, hybrid_wins=True)
    capsys.readouterr()
    assert commands.run_retrieval_promote("hybrid", start_db=None) == 0
    assert _default_rows(warehouse) == [("hybrid",)]


def test_retrieval_promote_refuses_before_starting_anything(
    warehouse: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def start() -> None:
        pytest.fail("a service was started")

    monkeypatch.setenv("GCW_LABEL_DIR", "")
    assert commands.run_retrieval_promote("other", start_db=start) == 1
    assert commands.run_retrieval_promote("vector", start_db=start) == 1
    monkeypatch.setenv("GCW_DB_NAME", "gcw_other")
    monkeypatch.setenv("DATABASE_URL", "")
    get_database_settings.cache_clear()
    assert commands.run_retrieval_promote("vector", start_db=start) == 1
    assert capsys.readouterr().out.splitlines() == [
        "retrieval-promote: failed at method (UnknownMethodError)",
        "retrieval-promote: failed at label directory (LabelDirError)",
        "retrieval-promote: failed at database (UnexpectedDatabaseError)",
    ]


def test_compare_fails_with_a_fixed_line_on_mismatched_predictions(
    warehouse: str,
    stub: StubEmbed,
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def evaluate(method: str) -> None:
        assert commands.run_retrieval_eval(method, start_db=None, start_service=None) == 0
        capsys.readouterr()

    def compare() -> str:
        assert commands.run_retrieval_compare("vector", "hybrid", start_db=None) == 1
        return capsys.readouterr().out

    run_embed(warehouse, _client(stub))
    evaluate("vector")
    assert compare() == ("retrieval-compare: failed at predictions (RetrievalPredictionsError)\n")

    with monkeypatch.context() as changed:  # a different embed version
        changed.setattr(window, "QUERY_INSTRUCTION", "Query:")
        run_embed(warehouse, _client(stub))
        evaluate("hybrid")
    assert compare() == "retrieval-compare: failed at scoring (EmbedVersionMismatch)\n"

    run_embed(warehouse, _client(stub))
    path = _write_questions(label_dir)  # then the question file changes between the two runs
    path.write_text(path.read_text().replace("which friday", "what friday"))
    evaluate("hybrid")
    assert compare() == "retrieval-compare: failed at scoring (QuestionSetMismatch)\n"


def test_a_synthetic_run_uses_a_temporary_directory_and_the_committed_questions(
    warehouse: str,
    stub: StubEmbed,
    label_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    committed = _write_questions(tmp_path)  # stands in for the committed synthetic set
    (label_dir / "questions.csv").unlink()
    monkeypatch.setattr(tagging, "SYNTHETIC_DATABASE", "gcw_test")
    monkeypatch.setattr(commands, "SYNTHETIC_QUESTIONS", committed)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    assert commands.run_embed(synthetic=True, start_db=None, start_service=None) == 0
    for method in ("vector", "hybrid"):
        assert (
            commands.run_retrieval_eval(method, synthetic=True, start_db=None, start_service=None)
            == 0
        )
    assert commands.run_retrieval_compare("vector", "hybrid", synthetic=True, start_db=None) == 0
    assert "questions source: synthetic" in capsys.readouterr().out
    (root,) = tmp_path.glob("gcw-synthetic-eval-*")
    assert sorted(p.name for p in (root / "retrieval").iterdir()) == ["hybrid.json", "vector.json"]
    assert list(label_dir.iterdir()) == []  # GCW_LABEL_DIR untouched


def test_the_commands_refuse_other_databases_and_label_directories_before_starting(
    warehouse: str,
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def start() -> None:
        pytest.fail("a service was started")

    # SYNTHETIC=1 means gcw_dev only
    assert commands.run_embed(synthetic=True, start_db=start, start_service=start) == 1
    assert commands.run_retrieval_eval("vector", True, start_db=start, start_service=start) == 1
    assert commands.run_retrieval_compare("vector", "hybrid", True, start_db=start) == 1
    for bad in ("", str(Path(__file__).resolve().parent)):
        monkeypatch.setenv("GCW_LABEL_DIR", bad)
        assert commands.run_retrieval_eval("vector", start_db=start, start_service=start) == 1
        assert commands.run_retrieval_compare("vector", "hybrid", start_db=start) == 1
    monkeypatch.setenv("GCW_EMBED_URL", "http://example.com:8082")
    assert commands.run_embed(start_db=start, start_service=start) == 1
    monkeypatch.setenv("GCW_DB_NAME", "gcw_other")
    monkeypatch.setenv("DATABASE_URL", "")
    get_database_settings.cache_clear()
    assert commands.run_embed(start_db=start, start_service=start) == 1
    synthetic = [
        f"{c}: failed at database (UnexpectedDatabaseError)"
        for c in ("embed", "retrieval-eval", "retrieval-compare")
    ]
    directory = [
        f"{c}: failed at label directory (LabelDirError)"
        for c in ("retrieval-eval", "retrieval-compare")
    ]
    assert capsys.readouterr().out.splitlines() == [
        *synthetic,
        *directory,
        *directory,
        "embed: failed at embed url (NonLocalUrlError)",
        "embed: failed at database (UnexpectedDatabaseError)",
    ]


def test_eligibility_here_is_the_labelers(warehouse: str) -> None:
    index = load_eligible(warehouse)
    with psycopg.connect(warehouse) as conn:
        centres = {NAME[w.message_id] for w in window.load_windows(conn)}
    assert centres == ELIGIBLE == {NAME[i] for ids in index.ids_by_thread().values() for i in ids}
