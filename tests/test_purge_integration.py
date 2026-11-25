"""`make purge-unlisted` (D91) against the real gcw_test database.

Only docker (the db start) is replaced. The catalog check, the deletes, the rollback and the
output run for real. Thread, sender and title strings are sentinels that must never be printed.
"""

import hashlib
import re
import shutil
from pathlib import Path

import psycopg
import pytest

from gcwrapped import purge
from gcwrapped.config import get_database_settings
from gcwrapped.migrate import MIGRATIONS_DIR, migrate

pytestmark = pytest.mark.integration

# (thread, sender, number of reactions). Thread 222 is the one removed from the allowlist.
MESSAGES = [
    ("111", "SENTINELNAME a", 2),
    ("111", "SENTINELNAME a", 0),
    ("222", "SENTINELNAME b", 3),
    ("222", "SENTINELNAME b", 1),
    ("222", "SENTINELNAME c", 0),
    ("333", "SENTINELNAME d", 1),
]
CONSENT = [
    ("111", "SENTINELNAME a", "yes"),
    ("222", "SENTINELNAME b", "local"),
    ("222", "SENTINELNAME c", "no"),
    ("333", "SENTINELNAME d", "yes"),
]
TABLES = (
    "raw.messages",
    "raw.sender_consent",
    "enrich.message_tags",
    "enrich.message_embeddings",
    "raw.load_runs",
)
KEYED = ("raw.messages", "raw.sender_consent", "enrich.message_tags", "enrich.message_embeddings")


def _no_start() -> None:
    return None


def _key(n: int) -> str:
    return hashlib.sha256(str(n).encode()).hexdigest()


def _counts(url: str) -> dict[str, int]:
    with psycopg.connect(url) as conn:
        counts = {}
        for table in TABLES:
            row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            assert row is not None
            counts[table] = int(row[0])
        return counts


def _threads_in(url: str, table: str) -> set[str]:
    with psycopg.connect(url) as conn:
        if table.startswith("enrich."):
            rows = conn.execute(
                f"SELECT m.thread_id FROM {table} t JOIN raw.messages m USING (message_key)"
            ).fetchall()
        else:
            rows = conn.execute(f"SELECT thread_id FROM {table}").fetchall()
    return {r[0] for r in rows}


@pytest.fixture
def warehouse(migrated_db: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """gcw_test with three threads, reactions, a tag per message and consent rows."""
    monkeypatch.setenv("GCW_THREAD_ALLOWLIST", "111,333")
    with psycopg.connect(migrated_db) as conn:
        run = conn.execute(
            "INSERT INTO raw.load_runs (roots_count) VALUES (1) RETURNING load_id"
        ).fetchone()
        assert run is not None
        for n, (thread, sender, reactions) in enumerate(MESSAGES):
            conn.execute(
                "INSERT INTO raw.messages (message_key, platform, thread_id, thread_title, "
                "sender_name, sent_at, content, media_count, reactions, source_file, load_id) "
                "VALUES (%s, 'instagram', %s, 'SENTINELTITLE', %s, now(), 'SENTINELTEXT', 0, "
                "%s::jsonb, 'x', %s)",
                (
                    _key(n),
                    thread,
                    sender,
                    "[" + ",".join('{"actor": "SENTINELNAME r"}' for _ in range(reactions)) + "]",
                    run[0],
                ),
            )
            conn.execute(
                "INSERT INTO enrich.message_tags (message_key, topic, sentiment, model_id, "
                "prompt_version, run_id) VALUES (%s, 'food', 'neutral', 'm', 'p', 'r')",
                (_key(n),),
            )
            conn.execute(
                "INSERT INTO enrich.message_embeddings (message_key, window_message_keys, "
                "embed_version, model_id, embedding, window_tsv) VALUES (%s, ARRAY[%s], 'v', 'm', "
                "array_fill(1, ARRAY[1024])::vector, to_tsvector('simple', 'SENTINELTEXT'))",
                (_key(n), _key(n)),
            )
        for thread, sender, consent in CONSENT:
            conn.execute(
                "INSERT INTO raw.sender_consent (thread_id, sender_name, consent) "
                "VALUES (%s, %s, %s)",
                (thread, sender, consent),
            )
    return migrated_db


def test_every_keyed_table_is_covered(warehouse: str) -> None:
    with psycopg.connect(warehouse) as conn:
        assert purge.keyed_tables(conn) == purge.COVERED_TABLES


def test_a_shorter_allowlist_purges_exactly_the_removed_thread_in_every_table(
    warehouse: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert purge.run(confirm=True, start_db=_no_start) == 0

    out = capsys.readouterr().out
    assert out.splitlines() == [
        "target database: gcw_test",
        "threads in the warehouse: 3",
        "threads allowlisted: 2",
        "threads to purge: 1",
        "messages: 3",
        "reactions: 4",
        "tags: 3",
        "embeddings: 3",
        "consent rows: 2",
        "mode: deleted",
        "purge-unlisted: ok",
    ]
    for line, shape in zip(out.splitlines(), purge.SHAPES, strict=True):
        assert re.fullmatch(shape, line)
    for table in KEYED:
        assert _threads_in(warehouse, table) == {"111", "333"}
    assert _counts(warehouse) == {
        "raw.messages": 3,
        "raw.sender_consent": 2,
        "enrich.message_tags": 3,
        "enrich.message_embeddings": 3,
        "raw.load_runs": 1,  # no thread or message key: stays
    }
    assert "sentinel" not in out.lower()
    assert not re.search(r"\b(111|222|333)\b", out)  # no thread ID


def test_a_dry_run_reports_the_same_counts_and_deletes_nothing(
    warehouse: str, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _counts(warehouse)

    assert purge.run(start_db=_no_start) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[3:10] == [
        "threads to purge: 1",
        "messages: 3",
        "reactions: 4",
        "tags: 3",
        "embeddings: 3",
        "consent rows: 2",
        "mode: dry run",
    ]
    assert _counts(warehouse) == before
    assert _threads_in(warehouse, "raw.messages") == {"111", "222", "333"}


def test_a_failure_during_the_delete_leaves_every_table_unchanged(
    warehouse: str, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _counts(warehouse)
    with psycopg.connect(warehouse, autocommit=True) as conn:
        # The message delete runs after the tag and consent deletes, so a failure there proves the
        # earlier deletes were rolled back with it.
        conn.execute(
            "CREATE FUNCTION raw.boom() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'SENTINELTEXT boom'; END $$"
        )
        conn.execute(
            "CREATE TRIGGER boom BEFORE DELETE ON raw.messages "
            "FOR EACH ROW EXECUTE FUNCTION raw.boom()"
        )

    assert purge.run(confirm=True, start_db=_no_start) == 1

    out = capsys.readouterr().out
    assert out == "purge-unlisted: failed at purge (RaiseException)\n"
    assert _counts(warehouse) == before
    assert _threads_in(warehouse, "enrich.message_tags") == {"111", "222", "333"}
    assert _threads_in(warehouse, "raw.sender_consent") == {"111", "222", "333"}
    assert _threads_in(warehouse, "enrich.message_embeddings") == {"111", "222", "333"}


@pytest.mark.parametrize("value", ["", "  "])
def test_an_empty_allowlist_is_refused_and_purges_nothing(
    warehouse: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str,
) -> None:
    monkeypatch.setenv("GCW_THREAD_ALLOWLIST", value)
    before = _counts(warehouse)

    assert purge.run(confirm=True, start_db=_no_start) == 1

    assert capsys.readouterr().out == "purge-unlisted: failed at allowlist (AllowlistError)\n"
    assert _counts(warehouse) == before


def test_a_rerun_purges_nothing(warehouse: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert purge.run(confirm=True, start_db=_no_start) == 0
    capsys.readouterr()

    assert purge.run(confirm=True, start_db=_no_start) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[1:9] == [
        "threads in the warehouse: 2",
        "threads allowlisted: 2",
        "threads to purge: 0",
        "messages: 0",
        "reactions: 0",
        "tags: 0",
        "embeddings: 0",
        "consent rows: 0",
    ]


def test_an_allowlist_of_unknown_threads_purges_the_whole_warehouse(
    warehouse: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GCW_THREAD_ALLOWLIST", "999")

    assert purge.run(confirm=True, start_db=_no_start) == 0

    assert _counts(warehouse)["raw.messages"] == 0
    assert _counts(warehouse)["raw.load_runs"] == 1


def test_a_table_keyed_by_thread_that_the_purge_does_not_know_is_refused(
    warehouse: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with psycopg.connect(warehouse, autocommit=True) as conn:
        conn.execute("CREATE TABLE enrich.thread_summaries (thread_id text, body text)")
    before = _counts(warehouse)

    assert purge.run(confirm=True, start_db=_no_start) == 1

    assert capsys.readouterr().out == "purge-unlisted: failed at purge (UncoveredTableError)\n"
    assert _counts(warehouse) == before


def test_the_database_guard_runs_before_anything_starts(
    warehouse: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[int] = []
    monkeypatch.setenv("GCW_DB_NAME", "gcw_other")
    monkeypatch.setenv("DATABASE_URL", "")
    get_database_settings.cache_clear()

    assert purge.run(confirm=True, start_db=lambda: started.append(1)) == 1

    assert (
        capsys.readouterr().out == "purge-unlisted: failed at database (UnexpectedDatabaseError)\n"
    )
    assert started == []


def test_a_database_missing_migration_004_is_migrated_and_then_purged_normally(
    clean_db: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The real purge once refused with UncoveredTableError because 004 had not been applied."""
    old = tmp_path / "migrations"
    old.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name < "004":
            shutil.copy(path, old / path.name)
    migrate(clean_db, old)
    with psycopg.connect(clean_db) as conn:
        assert conn.execute("SELECT to_regclass('enrich.message_tags')").fetchone() == (None,)
        run = conn.execute(
            "INSERT INTO raw.load_runs (roots_count) VALUES (1) RETURNING load_id"
        ).fetchone()
        assert run is not None
        for n, thread in enumerate(("111", "222")):
            conn.execute(
                "INSERT INTO raw.messages (message_key, platform, thread_id, sender_name, "
                "sent_at, content, media_count, source_file, load_id) VALUES "
                "(%s, 'instagram', %s, 'SENTINELNAME a', now(), 'SENTINELTEXT', 0, 'x', %s)",
                (_key(n), thread, run[0]),
            )
    monkeypatch.setenv("GCW_THREAD_ALLOWLIST", "111")
    get_database_settings.cache_clear()

    assert purge.run(confirm=True, start_db=_no_start) == 0

    lines = capsys.readouterr().out.splitlines()
    assert "messages: 1" in lines and "tags: 0" in lines and "embeddings: 0" in lines
    assert lines[-1] == "purge-unlisted: ok"
    with psycopg.connect(clean_db) as conn:
        assert conn.execute("SELECT to_regclass('enrich.message_tags')").fetchone() == (
            "enrich.message_tags",
        )
        assert conn.execute("SELECT thread_id FROM raw.messages").fetchall() == [("111",)]
