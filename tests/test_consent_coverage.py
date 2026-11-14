"""make consent-coverage (D88): ranking, fixed output, the private priority file, guards.

The database read is replaced by a synthetic Coverage (the real queries run in
test_consent_coverage_integration.py); the guards, the file write and the rendering run for real.
"""

import csv
import os
import re
import stat
from pathlib import Path

import pytest

from gcwrapped import cli
from gcwrapped import consent_coverage as cc
from gcwrapped.config import get_database_settings
from gcwrapped.labeling.commands import LABEL_DIR_MESSAGE
from gcwrapped.ledger import REPO_ROOT

SENTINEL = "SENTINELNAME"


def _row(
    thread: str, name: str, texts: int, consent: str = "yes", title: str = "SENTINELTITLE"
) -> cc.SenderRow:
    local = texts if consent in ("yes", "local") else 0
    external = texts if consent == "yes" else 0
    return cc.SenderRow(thread, title, f"{SENTINEL} {name}", texts, local, external, consent)


ROWS = [
    _row("1", "small", 10, "no"),
    _row("1", "big", 50, "yes"),
    _row("2", "mid", 30, "local"),
    _row("2", "tie b", 10, ""),
    _row("2", "tie a", 10, cc.MISSING),
]


def _coverage(rows: list[cc.SenderRow]) -> cc.Coverage:
    ranked = cc.rank_senders(rows)
    return cc.Coverage(
        database="gcw_test",
        text_messages=sum(r.text_messages for r in ranked),
        ledger_rows=4,
        yes=1,
        local=1,
        no=1,
        blank=1,
        missing_senders=1,
        local_messages=80,
        external_messages=50,
        threads_local=2,
        threads_total=3,
        needed={p: cc.senders_needed(ranked, p) for p in cc.SHARE_TARGETS},
        ranked=ranked,
    )


@pytest.fixture
def label_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "labels"
    directory.mkdir()
    monkeypatch.setenv("GCW_LABEL_DIR", str(directory))
    monkeypatch.setenv("GCW_DB_NAME", "gcw_test")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_database_settings.cache_clear()
    monkeypatch.setattr(cc, "build_coverage", lambda url, database: _coverage(ROWS))
    return directory


def _no_docker() -> None:
    raise AssertionError("docker must not start before the guards pass")


def _ok_docker() -> None:
    return None


# --- Ranking ---------------------------------------------------------------------------------


def test_ranking_is_by_text_messages_then_thread_then_name() -> None:
    ranked = cc.rank_senders(ROWS)

    assert [(r.thread_id, r.sender_name.split()[-1]) for r in ranked] == [
        ("1", "big"),
        ("2", "mid"),
        ("1", "small"),  # 10 messages, thread 1 before thread 2
        ("2", "a"),  # then names break the tie
        ("2", "b"),
    ]


def test_senders_needed_is_the_smallest_prefix_reaching_the_share() -> None:
    ranked = cc.rank_senders(ROWS)  # 50, 30, 10, 10, 10 of 110

    assert cc.senders_needed(ranked, 50) == 2  # 55 needed: 50 is short, 50 + 30 is enough
    assert cc.senders_needed(ranked, 80) == 3  # 88 needed: 80 is short, 90 is enough
    assert cc.senders_needed(ranked, 90) == 4  # 99 needed: 90 is short, 100 is enough
    assert cc.senders_needed([], 50) == 0


def test_senders_needed_uses_exact_integer_math_at_the_boundary() -> None:
    ranked = cc.rank_senders([_row("1", "a", 50), _row("1", "b", 50)])

    assert cc.senders_needed(ranked, 50) == 1  # exactly 50%: one sender is enough
    assert cc.senders_needed(ranked, 80) == 2


def test_priority_rows_have_a_running_share_ending_at_one() -> None:
    rows = cc.priority_rows(cc.rank_senders(ROWS))

    assert rows[0] == ["1", "1", "SENTINELTITLE", f"{SENTINEL} big", "50", "0.4545", "yes"]
    assert [r[0] for r in rows] == ["1", "2", "3", "4", "5"]
    assert rows[-1][5] == "1.0000"
    assert [r[6] for r in rows] == ["yes", "local", "no", cc.MISSING, ""]


# --- Output ----------------------------------------------------------------------------------


def test_the_output_is_exactly_the_fixed_lines_and_leaks_nothing(
    label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cc.run(_ok_docker) == 0

    out = capsys.readouterr().out
    assert out.splitlines() == [
        "target database: gcw_test",
        "text messages: 110",
        "ledger rows: 4",
        "consent yes: 1",
        "consent local: 1",
        "consent no: 1",
        "consent blank: 1",
        "senders in the warehouse missing from the ledger: 1",
        "local-eligible text messages: 80",
        "external-eligible text messages: 50",
        "threads with a local-eligible message: 2 of 3",
        "senders needed for 50% of text messages: 2",
        "senders needed for 80% of text messages: 3",
        "senders needed for 90% of text messages: 4",
        "consent-coverage: ok",
    ]
    for line, shape in zip(out.splitlines(), cc.LINE_SHAPES, strict=True):
        assert re.fullmatch(shape, line)
    assert SENTINEL not in out and "SENTINELTITLE" not in out and str(label_dir) not in out


def test_the_priority_file_is_private_complete_and_never_printed(
    label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cc.run(_ok_docker) == 0

    path = label_dir / cc.PRIORITIES_FILE
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with path.open(encoding="utf-8", newline="") as handle:
        records = list(csv.reader(handle))
    assert tuple(records[0]) == cc.PRIORITIES_HEADER
    assert len(records) == 1 + len(ROWS)
    assert records[1][3] == f"{SENTINEL} big"
    assert [p.name for p in label_dir.iterdir()] == [cc.PRIORITIES_FILE]  # no temp file left
    assert SENTINEL not in capsys.readouterr().out


def test_a_second_run_replaces_the_file(label_dir: Path) -> None:
    (label_dir / cc.PRIORITIES_FILE).write_text("stale", encoding="utf-8")

    assert cc.run(_ok_docker) == 0

    assert (label_dir / cc.PRIORITIES_FILE).read_text(encoding="utf-8").startswith("rank,")


def test_a_write_failure_prints_a_fixed_line_and_leaves_no_temp_file(
    label_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(src: object, dst: object) -> None:
        raise OSError(f"{SENTINEL} detail")

    monkeypatch.setattr(os, "replace", boom)

    assert cc.run(_ok_docker) == 1

    out = capsys.readouterr().out
    assert out == "consent-coverage: failed at write (OSError)\n"
    assert list(label_dir.iterdir()) == []


def test_an_unexpected_failure_prints_only_the_step_and_type(
    label_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(url: str, database: str) -> cc.Coverage:
        raise RuntimeError(f"{SENTINEL} detail")

    monkeypatch.setattr(cc, "build_coverage", boom)

    assert cc.run(_ok_docker) == 1
    assert capsys.readouterr().out == "consent-coverage: failed at warehouse (RuntimeError)\n"
    assert list(label_dir.iterdir()) == []


def test_render_refuses_a_line_outside_its_shape() -> None:
    bad = _coverage(ROWS)
    object.__setattr__(bad, "database", "gcw_prod")

    with pytest.raises(cc.ShapeError):
        cc.render(bad)


# --- Guards ----------------------------------------------------------------------------------


def test_a_label_dir_inside_the_repo_is_refused_before_docker(
    label_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GCW_LABEL_DIR", str(REPO_ROOT / "docs"))

    assert cc.run(_no_docker) == 1

    assert capsys.readouterr().out.strip() == LABEL_DIR_MESSAGE
    assert list((REPO_ROOT / "docs").glob("consent_priorities*")) == []


def test_an_unset_label_dir_is_refused(
    label_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GCW_LABEL_DIR", "")

    assert cc.run(_no_docker) == 1
    assert capsys.readouterr().out.strip() == LABEL_DIR_MESSAGE


@pytest.mark.parametrize("name", ["gcw_prod", "postgres", "gcw_real_backup"])
def test_a_database_outside_the_allowed_set_is_refused_before_anything_starts(
    name: str,
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GCW_DB_NAME", name)
    get_database_settings.cache_clear()

    assert cc.run(_no_docker) == 1

    assert capsys.readouterr().out == (
        "consent-coverage: failed at database (UnexpectedDatabaseError)\n"
    )
    assert list(label_dir.iterdir()) == []


def test_the_cli_runs_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cc, "run", lambda: 0)

    assert cli.main(["consent-coverage"]) == 0
