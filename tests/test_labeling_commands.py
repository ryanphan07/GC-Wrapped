"""label-tags, label-questions, label-summary: guards, frozen samples, fixed output (D86).

The database read is replaced by a synthetic index (the real read is in
test_labeling_integration.py); everything else, including the guards and the files, runs for real.
"""

import json
import os
import re
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from gcwrapped import cli
from gcwrapped.config import get_database_settings
from gcwrapped.labeling import commands, summary
from gcwrapped.labeling.files import read_questions, read_tags
from gcwrapped.labeling.schema import (
    QUESTION_CANDIDATE_POOL,
    QUESTION_CANDIDATES_FILE,
    SENTIMENTS,
    TAG_SAMPLE_FILE,
    TAG_SAMPLE_SIZE,
    TOPICS,
)
from gcwrapped.labeling.source import EligibleIndex
from gcwrapped.ledger import REPO_ROOT
from labeling_support import Scripted, StepClock, make_index, message_id

SENTINEL = "SENTINELTEXT"
TEXTS = {
    "t1": [f"{SENTINEL} one {n}" for n in range(300)],
    "t2": [f"{SENTINEL} two {n}" for n in range(120)],
    "t3": [f"{SENTINEL} three {n}" for n in range(20)],
}
LABEL_COMMANDS = [commands.run_label_tags, commands.run_label_questions]


@pytest.fixture
def label_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A label directory outside the repo, the database env pointing at gcw_test, no real DB."""
    directory = tmp_path / "labels"
    directory.mkdir()
    monkeypatch.setenv("GCW_LABEL_DIR", str(directory))
    monkeypatch.setenv("GCW_DB_NAME", "gcw_test")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_database_settings.cache_clear()
    monkeypatch.setattr(commands, "load_eligible", lambda url: make_index(TEXTS))
    return directory


def _no_docker() -> None:
    raise AssertionError("docker must not start before the guards pass")


def _ok_docker() -> None:
    return None


# --- Guards -----------------------------------------------------------------------------------


@pytest.mark.parametrize("runner", LABEL_COMMANDS)
def test_a_label_dir_inside_the_repo_is_refused_with_a_fixed_message(
    runner: Callable[..., int],
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inside = REPO_ROOT / "tests" / "fixtures"
    monkeypatch.setenv("GCW_LABEL_DIR", str(inside))
    assert runner(Scripted(), _no_docker) == 1
    out = capsys.readouterr().out
    assert out.strip() == commands.LABEL_DIR_MESSAGE
    assert str(inside) not in out


@pytest.mark.parametrize("bad", ["", "   ", "does-not-exist", "FILE"])
def test_an_unset_missing_or_non_directory_label_dir_is_refused(
    bad: str,
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    if bad == "FILE":
        (tmp_path / "afile").write_text("x", encoding="utf-8")
        bad = str(tmp_path / "afile")
    elif bad == "does-not-exist":
        bad = str(tmp_path / "does-not-exist")
    monkeypatch.setenv("GCW_LABEL_DIR", bad)
    assert commands.run_label_tags(Scripted(), _no_docker) == 1
    assert capsys.readouterr().out.strip() == commands.LABEL_DIR_MESSAGE


def test_the_repo_root_and_a_symlink_into_it_are_refused(
    label_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = tmp_path / "sneaky"
    os.symlink(REPO_ROOT / "tests", link)
    for bad in (str(REPO_ROOT), str(link), str(link / "fixtures")):
        monkeypatch.setenv("GCW_LABEL_DIR", bad)
        with pytest.raises(commands.LabelDirError):
            commands.resolve_label_dir(bad)


def test_a_label_dir_outside_the_repo_is_accepted(label_dir: Path) -> None:
    assert commands.resolve_label_dir(str(label_dir)) == label_dir.resolve()


def test_a_cli_run_with_the_label_dir_in_the_repo_exits_nonzero_with_a_fixed_message(
    label_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GCW_LABEL_DIR", str(REPO_ROOT / "docs"))
    for command in ("label-tags", "label-questions", "label-summary"):
        assert cli.main([command]) == 1
        captured = capsys.readouterr()
        assert captured.out.strip() == commands.LABEL_DIR_MESSAGE
        assert captured.err == ""


@pytest.mark.parametrize("name", ["gcw_prod", "postgres", "gcw_real_backup"])
def test_a_database_outside_the_allowed_set_is_refused_before_anything_starts(
    name: str,
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GCW_DB_NAME", name)
    get_database_settings.cache_clear()
    for runner in (commands.run_label_tags, commands.run_label_questions):
        assert runner(Scripted(), _no_docker) == 1
        assert capsys.readouterr().out.strip() == (
            f"label-{'tags' if runner is commands.run_label_tags else 'questions'}: "
            "failed at database (UnexpectedDatabaseError)"
        )
    assert commands.run_label_summary(_no_docker) == 1
    assert capsys.readouterr().out.strip() == (
        "label-summary: failed at database (UnexpectedDatabaseError)"
    )
    assert name not in capsys.readouterr().out


@pytest.mark.parametrize("name", ["gcw_dev", "gcw_real", "gcw_ci", "gcw_test"])
def test_every_allowed_database_passes_the_guard(
    name: str, label_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GCW_DB_NAME", name)
    get_database_settings.cache_clear()
    assert commands.run_label_tags(Scripted("q"), _ok_docker) == 0


def test_no_eligible_messages_is_a_fixed_message_and_freezes_nothing(
    label_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(commands, "load_eligible", lambda url: make_index({}))
    assert commands.run_label_tags(Scripted(), _ok_docker) == 1
    assert capsys.readouterr().out.strip() == commands.NO_ELIGIBLE_MESSAGE
    assert list(label_dir.iterdir()) == []  # an empty pool must not be frozen as "the sample"


# --- Frozen samples ---------------------------------------------------------------------------


def test_the_first_run_freezes_a_stratified_sample_of_ids_only(label_dir: Path) -> None:
    assert commands.run_label_tags(Scripted("q"), _ok_docker) == 0
    frozen = json.loads((label_dir / TAG_SAMPLE_FILE).read_text(encoding="utf-8"))
    ids = frozen["message_ids"]
    assert len(ids) == len(set(ids)) == TAG_SAMPLE_SIZE
    assert SENTINEL not in json.dumps(frozen)  # IDs and parameters only
    index = make_index(TEXTS)
    per_thread = {
        t: sum(1 for i in ids if index.get(i).thread_id == t) for t in index.ids_by_thread()
    }
    assert per_thread["t3"] >= 5  # the floor
    assert per_thread["t1"] > per_thread["t2"] > per_thread["t3"]  # in proportion to the pool


def test_the_sample_is_the_same_on_every_run_and_after_more_messages_arrive(
    label_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands.run_label_tags(Scripted("q"), _ok_docker)
    first = (label_dir / TAG_SAMPLE_FILE).read_bytes()
    commands.run_label_tags(Scripted("q"), _ok_docker)
    assert (label_dir / TAG_SAMPLE_FILE).read_bytes() == first
    bigger = {**TEXTS, "t4": [f"{SENTINEL} four {n}" for n in range(500)]}
    monkeypatch.setattr(commands, "load_eligible", lambda url: make_index(bigger))
    commands.run_label_tags(Scripted("q"), _ok_docker)
    assert (label_dir / TAG_SAMPLE_FILE).read_bytes() == first
    # And a different label dir with the same pool draws the same sample (fixed seed).
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("GCW_LABEL_DIR", str(other))
    monkeypatch.setattr(commands, "load_eligible", lambda url: make_index(TEXTS))
    commands.run_label_tags(Scripted("q"), _ok_docker)
    assert (other / TAG_SAMPLE_FILE).read_bytes() == first


def test_question_candidates_are_a_separate_larger_frozen_pool(label_dir: Path) -> None:
    commands.run_label_questions(Scripted("q"), _ok_docker)
    ids = json.loads((label_dir / QUESTION_CANDIDATES_FILE).read_text(encoding="utf-8"))
    assert len(ids["message_ids"]) == QUESTION_CANDIDATE_POOL
    commands.run_label_tags(Scripted("q"), _ok_docker)
    tags = json.loads((label_dir / TAG_SAMPLE_FILE).read_text(encoding="utf-8"))
    assert ids["message_ids"][:TAG_SAMPLE_SIZE] != tags["message_ids"]


def test_a_scripted_end_to_end_run_labels_resumes_and_summarizes(
    label_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = StepClock()
    assert commands.run_label_tags(Scripted("1g2n3b"), _ok_docker, clock) == 0
    assert commands.run_label_tags(Scripted("4gq"), _ok_docker, clock) == 0  # resumes
    assert len(read_tags(label_dir / "tag_labels.csv")) == 4
    commands.run_label_questions(Scripted("wsq", ["a question of my own", ""]), _ok_docker, clock)
    assert len(read_questions(label_dir / "questions.csv")) == 1
    capsys.readouterr()
    monkeypatch.setattr(summary, "count_threads", lambda url, ids: 2)
    assert commands.run_label_summary(_ok_docker) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == f"tags labeled: 4 of {TAG_SAMPLE_SIZE}"
    assert "topic plans: 1" in lines and "sentiment negative: 1" in lines
    assert "questions answered: 1 of 100" in lines and "questions skipped: 1" in lines
    assert "mean gold messages per question: 1.00" in lines


# --- Output -----------------------------------------------------------------------------------


def _summary(label_dir: Path, capsys: pytest.CaptureFixture[str]) -> list[str]:
    assert commands.run_label_summary(_ok_docker) == 0
    return capsys.readouterr().out.splitlines()


def test_summary_prints_only_the_fixed_lines(
    label_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(summary, "count_threads", lambda url, ids: 3)
    commands.run_label_tags(Scripted("1g2n"), _ok_docker, StepClock())
    commands.run_label_questions(
        Scripted("w", [f"{SENTINEL} my question, in my words", "1 2"]), _ok_docker, StepClock()
    )
    capsys.readouterr()
    lines = _summary(label_dir, capsys)
    assert len(lines) == len(summary.LINE_SHAPES)
    for line, shape in zip(lines, summary.LINE_SHAPES, strict=True):
        assert re.fullmatch(shape, line), line
    assert lines[0] == "target database: gcw_test"
    assert lines[-2:] == ["threads covered: 3", "label-summary: ok"]
    assert [ln for ln in lines if ln.startswith("topic ")] == [
        f"topic {t}: {1 if t in ('plans', 'school') else 0}" for t in TOPICS
    ]
    assert [ln for ln in lines if ln.startswith("sentiment ")] == [
        f"sentiment {s}: {1 if s in ('positive', 'neutral') else 0}" for s in SENTIMENTS
    ]


def test_summary_with_nothing_labeled_is_all_zeros(
    label_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(summary, "count_threads", lambda url, ids: 0)
    lines = _summary(label_dir, capsys)
    assert f"tags labeled: 0 of {TAG_SAMPLE_SIZE}" in lines
    assert "mean gold messages per question: 0.00" in lines
    assert "threads covered: 0" in lines


def test_no_message_question_or_id_text_reaches_the_summary_or_the_logs(
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    question = "SENTINELQUESTION is my own wording"
    monkeypatch.setattr(summary, "count_threads", lambda url, ids: 1)
    with caplog.at_level(0):
        commands.run_label_tags(Scripted("1g"), _ok_docker, StepClock())
        commands.run_label_questions(Scripted("w", [question, "1"]), _ok_docker, StepClock())
        capsys.readouterr()
        lines = _summary(label_dir, capsys)
    shown_ids = {message_id(f"t1/{n}") for n in range(300)} | {
        t.message_id for t in read_tags(label_dir / "tag_labels.csv")
    }
    everything = "\n".join(lines) + capsys.readouterr().out + caplog.text
    assert SENTINEL not in everything and "SENTINELQUESTION" not in everything
    assert not any(i[:12] in everything for i in shown_ids)
    assert str(label_dir) not in everything


def test_a_failure_prints_the_step_and_exception_type_only(
    label_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def explode(url: str, ids: Sequence[str]) -> int:
        raise RuntimeError(f"{SENTINEL} leaked into an exception message")

    monkeypatch.setattr(summary, "count_threads", explode)
    assert commands.run_label_summary(_ok_docker) == 1
    out = capsys.readouterr().out
    assert out.strip() == "label-summary: failed at summary (RuntimeError)"

    def broken_load(url: str) -> EligibleIndex:
        raise ValueError(f"{SENTINEL} in a driver message")

    monkeypatch.setattr(commands, "load_eligible", broken_load)
    assert commands.run_label_tags(Scripted(), _ok_docker) == 1
    assert capsys.readouterr().out.strip() == (
        "label-tags: failed at eligible messages (ValueError)"
    )


def test_a_corrupt_label_file_fails_with_a_type_name_and_never_overwrites_it(
    label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = label_dir / "tag_labels.csv"
    bad.write_text("wrong,header\nSENTINELTEXT,x\n", encoding="utf-8")
    assert commands.run_label_tags(Scripted("1g"), _ok_docker) == 1
    assert capsys.readouterr().out.strip() == "label-tags: failed at labeling (LabelFileError)"
    assert bad.read_text(encoding="utf-8") == "wrong,header\nSENTINELTEXT,x\n"
