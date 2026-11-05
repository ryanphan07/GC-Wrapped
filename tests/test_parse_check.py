"""parse-check prints counts only; the sentinel export proves no content or name leaks."""

import json
from pathlib import Path

import pytest

from gcwrapped.cli import main
from gcwrapped.parse_check import check_export
from gcwrapped.parsers import instagram_format as fmt
from gcwrapped.parsers.instagram import parse_export
from synthetic import sentinel
from synthetic.instagram import THREADS, generate_pair

MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"


@pytest.fixture
def sentinel_root(tmp_path: Path) -> Path:
    sentinel.build(tmp_path, malformed=True)
    return tmp_path


def test_sentinel_never_appears_in_output(
    sentinel_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["parse-check", str(sentinel_root)])

    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert "threads:" in out  # it did run and report
    assert "ValueError" in out  # the folders without a numeric suffix
    assert "ParseError" in out  # the malformed message, by class name only
    assert code == 1  # errors were found
    haystack = out.lower()
    for needle in (*sentinel.SENTINELS, *sentinel.THREADS, str(sentinel.T0)):
        assert needle.lower() not in haystack, f"leak: {needle!r}"
    assert sentinel.MOJIBAKE_TEXT not in out


def test_sentinel_counts(sentinel_root: Path) -> None:
    report = check_export(sentinel_root)
    assert dict(report.folder_shapes) == sentinel.EXPECTED["thread_shapes"]
    # 19 messages in the fixture; the 2 in the non-conforming folders are never read.
    assert report.messages == sentinel.EXPECTED["messages"] - 2
    assert report.errors["ValueError"] == 2  # one per non-conforming folder
    assert report.errors["ParseError"] == 1  # the malformed message
    # One of the 5 photos sits in a skipped folder.
    assert dict(report.attachments) == {"photos": 4, "videos": 1, "audio_files": 1}
    assert report.mojibake_fields["content"] == sentinel.EXPECTED["mojibake_messages"]


def test_clean_export_matches_the_production_parser(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a, _ = generate_pair(tmp_path, max_per_file=30)
    expected = list(parse_export([a]))

    report = check_export(a)

    assert report.errors == {}
    assert report.messages == len(expected)
    assert report.duplicates_dropped == 0
    assert report.threads == len(THREADS)
    assert sum(report.attachments.values()) == sum(m.media_count for m in expected)
    assert report.external_uris > 0
    assert report.mojibake_fields["content"] > 0
    assert report.mojibake_fields["sender_name"] > 0
    assert report.mojibake_fields[fmt.KEY_THREAD_TITLE] > 0
    assert main(["parse-check", str(a)]) == 0
    out = capsys.readouterr().out
    assert f"messages parsed: {len(expected)}" in out
    assert "errors: 0" in out
    assert "elapsed seconds: " in out


def test_errors_are_counted_per_message_and_do_not_stop_the_run(tmp_path: Path) -> None:
    thread = tmp_path / fmt.MESSAGES_DIR / "t_1"
    thread.mkdir(parents=True)
    good = {fmt.KEY_SENDER: "a", fmt.KEY_TIMESTAMP_MS: 1}
    doc = {
        fmt.KEY_MESSAGES: [good, {fmt.KEY_SENDER: "a"}, "junk", {**good, fmt.KEY_TIMESTAMP_MS: 2}]
    }
    (thread / "message_1.json").write_text(json.dumps(doc), encoding="utf-8")

    report = check_export(tmp_path)

    assert report.messages == 2
    assert report.errors["ParseError"] == 2


def test_unreadable_message_file_is_counted(tmp_path: Path) -> None:
    thread = tmp_path / fmt.MESSAGES_DIR / "t_1"
    thread.mkdir(parents=True)
    (thread / "message_1.json").write_text("{not json", encoding="utf-8")

    assert check_export(tmp_path).errors["ParseError"] == 1


def test_no_threads_is_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["parse-check", str(tmp_path)]) == 1
    assert "no message threads" in capsys.readouterr().out


def test_make_target_requires_export_dir() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert 'test -n "$(EXPORT_DIR)"' in text.split("parse-check:", 1)[1]
