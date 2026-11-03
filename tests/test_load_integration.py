import json
import traceback
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from gcwrapped.cli import main
from gcwrapped.load import LoadError, load_messages
from gcwrapped.models import ParsedMessage
from gcwrapped.parsers import instagram_format as fmt
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

    with pytest.raises(LoadError):
        load_messages(migrated_db, explode_midway(), roots_count=1)

    assert _keys(migrated_db) == set()
    [(status, roots, parsed, inserted, error, finished_at)] = _runs(migrated_db)
    assert (status, roots, parsed, inserted) == ("failed", 1, None, None)
    assert error == "RuntimeError at row 5"  # class and row index only, never the exception text
    assert finished_at is not None


def test_failed_load_does_not_disturb_earlier_data(migrated_db: str, tmp_path: Path) -> None:
    a, b = generate_pair(tmp_path)
    load_messages(migrated_db, parse_export([a]), roots_count=1)
    before = _keys(migrated_db)

    def explode() -> Iterator[ParsedMessage]:
        yield from parse_export([b])
        raise RuntimeError

    with pytest.raises(LoadError):
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


SENTINEL = "SENTINEL_SECRET"


def _everything_raised(exc: BaseException) -> str:
    return "\n".join([str(exc), repr(exc), *traceback.format_exception(exc)])


def test_iterator_failure_is_value_free_everywhere(migrated_db: str, tmp_path: Path) -> None:
    a, _ = generate_pair(tmp_path)
    good = list(parse_export([a]))

    def explode() -> Iterator[ParsedMessage]:
        yield from good[:3]
        raise RuntimeError(f"boom {SENTINEL}")

    with pytest.raises(LoadError) as info:
        load_messages(migrated_db, explode(), roots_count=1)

    assert SENTINEL not in _everything_raised(info.value)
    assert (info.value.cause, info.value.row_index) == ("RuntimeError", 3)
    [(*_, error, _finished)] = _runs(migrated_db)
    assert error == "RuntimeError at row 3"


def test_message_with_nul_loads_with_the_nul_removed(migrated_db: str, tmp_path: Path) -> None:
    nul = chr(0)  # Postgres text cannot store it, so the parser must have stripped it
    thread = tmp_path / fmt.MESSAGES_DIR / "nul_1"
    thread.mkdir(parents=True)
    doc = {
        fmt.KEY_PARTICIPANTS: [],
        fmt.KEY_THREAD_TITLE: "t",
        fmt.KEY_MESSAGES: [
            {fmt.KEY_SENDER: "a", fmt.KEY_TIMESTAMP_MS: 1, fmt.KEY_CONTENT: f"hi{nul} there"}
        ],
    }
    (thread / "message_1.json").write_text(json.dumps(doc), encoding="utf-8")

    result = load_messages(migrated_db, parse_export([tmp_path]), roots_count=1)

    assert result.rows_inserted == 1
    with psycopg.connect(migrated_db) as conn:
        [(content,)] = conn.execute("SELECT content FROM raw.messages").fetchall()
    assert content == "hi there"


def test_bad_row_value_never_reaches_the_error(migrated_db: str, tmp_path: Path) -> None:
    a, _ = generate_pair(tmp_path)
    good = list(parse_export([a]))
    # A lone surrogate cannot be encoded for COPY; the error path must still not carry the value.
    bad = good[1].model_copy(update={"content": f"{SENTINEL}{chr(0xD800)} trailing"})

    with pytest.raises(LoadError) as info:
        load_messages(migrated_db, iter([good[0], bad, good[2]]), roots_count=1)

    assert SENTINEL not in _everything_raised(info.value)
    assert _keys(migrated_db) == set()
    [(status, *_, error, _finished)] = _runs(migrated_db)
    assert status == "failed"
    assert isinstance(error, str)
    assert SENTINEL not in error


def test_cli_ingest_failure_output_is_value_free(
    migrated_db: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, _ = generate_pair(tmp_path)
    good = list(parse_export([a]))

    def explode(*_args: object, **_kwargs: object) -> Iterator[ParsedMessage]:
        yield from good[:2]
        raise RuntimeError(f"boom {SENTINEL}")

    monkeypatch.setattr("gcwrapped.cli.parse_export", explode)

    assert main(["ingest", str(a)]) == 1

    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err
    assert "RuntimeError at row 2" in captured.out


def test_cli_ingest_with_no_threads_creates_no_run_row(
    migrated_db: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["ingest", str(tmp_path)]) == 1

    assert "no message threads" in capsys.readouterr().out
    assert _runs(migrated_db) == []


def test_ingest_landing_dir_is_idempotent_and_last_run_reports_counts(
    migrated_db: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    generate_pair(tmp_path)
    expected = len(
        {m.message_key for m in parse_export([tmp_path / "export_a", tmp_path / "export_b"])}
    )

    assert main(["ingest", "--landing", str(tmp_path)]) == 0
    assert main(["ingest", "--landing", str(tmp_path)]) == 0
    capsys.readouterr()

    assert len(_keys(migrated_db)) == expected
    assert main(["last-run"]) == 0
    out = capsys.readouterr().out
    assert f"rows parsed: {expected}" in out
    assert "rows inserted: 0" in out
    assert "status: succeeded" in out
    assert "elapsed seconds: " in out
