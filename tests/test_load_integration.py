from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from gcwrapped.cli import main
from gcwrapped.load import load_messages
from gcwrapped.models import ParsedMessage
from gcwrapped.parsers.instagram import parse_export
from synthetic.instagram import generate_pair

pytestmark = pytest.mark.integration


def _keys(url: str) -> set[str]:
    with psycopg.connect(url) as conn:
        return {r[0] for r in conn.execute("SELECT message_key FROM raw.messages")}


def _runs(url: str) -> list[tuple[object, ...]]:
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT status, roots_count, rows_parsed, rows_inserted, error, finished_at "
            "FROM raw.load_runs ORDER BY load_id"
        ).fetchall()


def test_ingest_export_a_inserts_all_its_messages(migrated_db: str, tmp_path: Path) -> None:
    a, _ = generate_pair(tmp_path)
    expected = {m.message_key for m in parse_export([a])}

    result = load_messages(migrated_db, parse_export([a]), roots_count=1)

    assert result.rows_parsed == len(expected)
    assert result.rows_inserted == len(expected)
    assert _keys(migrated_db) == expected


def test_reingesting_the_same_export_inserts_nothing(migrated_db: str, tmp_path: Path) -> None:
    a, _ = generate_pair(tmp_path)
    load_messages(migrated_db, parse_export([a]), roots_count=1)
    before = _keys(migrated_db)

    again = load_messages(migrated_db, parse_export([a]), roots_count=1)

    assert again.rows_parsed == len(before)
    assert again.rows_inserted == 0
    assert again.duplicates_skipped == len(before)
    assert _keys(migrated_db) == before


def test_overlapping_export_inserts_only_the_difference(migrated_db: str, tmp_path: Path) -> None:
    a, b = generate_pair(tmp_path)
    keys_a = {m.message_key for m in parse_export([a])}
    keys_b = {m.message_key for m in parse_export([b])}
    assert keys_a & keys_b, "fixture should overlap"
    load_messages(migrated_db, parse_export([a]), roots_count=1)

    result = load_messages(migrated_db, parse_export([b]), roots_count=1)

    assert result.rows_parsed == len(keys_b)
    assert result.rows_inserted == len(keys_b - keys_a)
    assert _keys(migrated_db) == keys_a | keys_b


def test_load_runs_record_counts_and_status(migrated_db: str, tmp_path: Path) -> None:
    a, b = generate_pair(tmp_path)
    n_a = sum(1 for _ in parse_export([a]))
    n_union = sum(1 for _ in parse_export([a, b]))
    first = load_messages(migrated_db, parse_export([a]), roots_count=1)
    load_messages(migrated_db, parse_export([a]), roots_count=1)
    load_messages(migrated_db, parse_export([a, b]), roots_count=2)

    runs = _runs(migrated_db)

    assert [r[:5] for r in runs] == [
        ("succeeded", 1, n_a, n_a, None),
        ("succeeded", 1, n_a, 0, None),
        ("succeeded", 2, n_union, n_union - n_a, None),
    ]
    assert all(r[5] is not None for r in runs)  # finished_at set
    with psycopg.connect(migrated_db) as conn:
        row = conn.execute(
            "SELECT count(*) FROM raw.messages WHERE load_id = %s", (first.load_id,)
        ).fetchone()
    assert row == (n_a,)


def test_failed_load_leaves_no_partial_messages_and_a_failed_run(
    migrated_db: str, tmp_path: Path
) -> None:
    a, _ = generate_pair(tmp_path)
    good = list(parse_export([a]))
    assert len(good) > 5

    def explode_midway() -> Iterator[ParsedMessage]:
        yield from good[:5]
        raise RuntimeError("SENTINEL_FAILURE_TEXT")

    with pytest.raises(RuntimeError):
        load_messages(migrated_db, explode_midway(), roots_count=1)

    assert _keys(migrated_db) == set()
    [(status, roots, parsed, inserted, error, finished_at)] = _runs(migrated_db)
    assert (status, roots, parsed, inserted) == ("failed", 1, None, None)
    assert error == "RuntimeError"  # type name only, never the exception text
    assert finished_at is not None


def test_failed_load_does_not_disturb_earlier_data(migrated_db: str, tmp_path: Path) -> None:
    a, b = generate_pair(tmp_path)
    load_messages(migrated_db, parse_export([a]), roots_count=1)
    before = _keys(migrated_db)

    def explode() -> Iterator[ParsedMessage]:
        yield from parse_export([b])
        raise RuntimeError

    with pytest.raises(RuntimeError):
        load_messages(migrated_db, explode(), roots_count=1)

    assert _keys(migrated_db) == before
    assert [r[0] for r in _runs(migrated_db)] == ["succeeded", "failed"]


def test_cli_ingest_prints_counts_only(
    migrated_db: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a, b = generate_pair(tmp_path)
    n_a = sum(1 for _ in parse_export([a]))
    union = {m.message_key for m in parse_export([a, b])}
    assert main(["ingest", str(a)]) == 0
    capsys.readouterr()

    assert main(["ingest", str(a), str(b)]) == 0

    out = capsys.readouterr().out
    assert "target database: gcw_dev" in out
    assert f"rows parsed: {len(union)}" in out
    assert f"rows inserted: {len(union) - n_a}" in out
    assert f"duplicates skipped: {n_a}" in out
    assert "elapsed seconds: " in out
    for m in parse_export([a, b]):
        for secret in (m.content, m.sender_name, m.thread_title, m.share_url):
            if secret and len(secret) > 3:
                assert secret not in out
    assert "ci-password" not in out


def test_cli_ingest_without_migration_says_so(
    clean_db: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a, _ = generate_pair(tmp_path)

    assert main(["ingest", str(a)]) == 1

    assert "gcw migrate" in capsys.readouterr().out
