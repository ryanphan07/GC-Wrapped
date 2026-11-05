import json
from pathlib import Path

import pytest

from gcwrapped.cli import main
from gcwrapped.config import get_settings
from gcwrapped.parsers import instagram_format as fmt
from gcwrapped.parsers.instagram import parse_export
from synthetic.instagram import THREADS, generate_pair


@pytest.fixture(autouse=True)
def no_db_env(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Run as a machine with no DB env vars and no .env file."""
    for var in (
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_PORT",
        "DATABASE_URL",
        "GCW_DB_NAME",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
    get_settings.cache_clear()


def test_parse_prints_summary_without_db_env(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a, _ = generate_pair(tmp_path)

    assert main(["parse", str(a)]) == 0

    out = capsys.readouterr().out
    assert f"threads: {len(THREADS)}" in out
    assert "messages: " in out
    assert "duplicates dropped: 0" in out
    assert "elapsed seconds: " in out


def test_parse_counts_duplicates_across_overlapping_exports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a, b = generate_pair(tmp_path)
    expected = sum(1 for _ in parse_export([a])) + sum(1 for _ in parse_export([b]))
    union = sum(1 for _ in parse_export([a, b]))

    assert main(["parse", str(a), str(b)]) == 0

    out = capsys.readouterr().out
    assert f"messages: {union}" in out
    assert f"duplicates dropped: {expected - union}" in out


def test_parse_output_contains_no_message_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sentinels = ["SENTINEL_BODY_7q", "SENTINEL_SENDER_7q", "SENTINEL_TITLE_7q", "SENTINEL_URI_7q"]
    thread = tmp_path / fmt.MESSAGES_DIR / "sentinel_thread_1"
    thread.mkdir(parents=True)
    doc = {
        fmt.KEY_MESSAGES: [
            {
                fmt.KEY_SENDER: sentinels[1],
                fmt.KEY_TIMESTAMP_MS: 1_700_000_000_000,
                fmt.KEY_CONTENT: sentinels[0],
                fmt.KEY_PHOTOS: [{fmt.KEY_URI: sentinels[3]}],
            }
        ],
        fmt.KEY_THREAD_TITLE: sentinels[2],
    }
    (thread / "message_1.json").write_text(json.dumps(doc), encoding="utf-8")

    assert main(["parse", str(tmp_path)]) == 0

    captured = capsys.readouterr()
    for s in sentinels:
        assert s not in captured.out
        assert s not in captured.err


def test_parse_output_has_no_synthetic_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a, b = generate_pair(tmp_path)
    main(["parse", str(a), str(b)])
    captured = capsys.readouterr()
    text = captured.out + captured.err
    for m in parse_export([a, b]):
        for secret in (m.content, m.sender_name, m.thread_title, m.share_url):
            if secret and len(secret) > 3:
                assert secret not in text


def test_parse_error_message_has_no_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    thread = tmp_path / fmt.MESSAGES_DIR / "t_1"
    thread.mkdir(parents=True)
    bad = {fmt.KEY_MESSAGES: [{fmt.KEY_SENDER: "SENTINEL_SENDER_7q"}]}
    (thread / "message_1.json").write_text(json.dumps(bad), encoding="utf-8")

    assert main(["parse", str(tmp_path)]) == 1

    out = capsys.readouterr().out
    assert "error:" in out
    assert "SENTINEL_SENDER_7q" not in out


def test_parse_empty_path_reports_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["parse", str(tmp_path)]) == 1
    assert "no message threads" in capsys.readouterr().out


def test_ingest_with_no_threads_fails_before_touching_the_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("DATABASE_URL", "GCW_DB_NAME", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        monkeypatch.delenv(var, raising=False)

    assert main(["ingest", str(tmp_path)]) == 1

    out = capsys.readouterr().out
    assert "no message threads" in out
    assert "database not configured" not in out  # failed on the path, before any DB config
