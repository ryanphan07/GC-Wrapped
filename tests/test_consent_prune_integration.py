"""consent-prune (D101) against gcw_test: tags, label rows, question gold and stored predictions.

The warehouse is the tagging tests' one (a stand-in eligibility view over the raw tables). Labels,
questions and predictions are synthetic and live in a temporary directory, never GCW_LABEL_DIR.
Sender names and message text are sentinels that must never be printed.
"""

import re
import stat
from pathlib import Path

import psycopg
import pytest

from gcwrapped import consent_prune
from gcwrapped.config import get_database_settings
from gcwrapped.labeling.files import read_questions, read_tags, write_questions, write_tags
from gcwrapped.labeling.schema import QuestionLabel, TagLabel
from gcwrapped.tagging import evaluation, model
from gcwrapped.tagging.prompt import prompt_version_for
from gcwrapped.tagging.runner import run_tagging
from test_tagging_integration import SENTINEL, Fake, _key
from test_tagging_integration import warehouse as tagging_warehouse  # noqa: F401  (a fixture)

pytestmark = pytest.mark.integration

A1, A2, A3, E1, E2 = (
    _key("111", 0),
    _key("111", 2),
    _key("111", 4),
    _key("333", 8),
    _key("333", 9),
)
BO = _key("111", 1)  # a declined sender's message: never eligible, labeled by mistake
STAMP = "2026-11-10T00:00:00+00:00"
VERSION = prompt_version_for(model.REGISTRY[model.MODEL_ID].chat_template_kwargs)
MODEL_ID = model.MODEL_ID


def _tags(*ids: str) -> list[TagLabel]:
    return [TagLabel(i, "food", "neutral", STAMP) for i in ids]


def _question(n: int, source: str, *gold: str) -> QuestionLabel:
    return QuestionLabel(f"q{n:03d}", f"{SENTINEL} question {n}?", gold, source, STAMP)


@pytest.fixture
def warehouse(request: pytest.FixtureRequest) -> str:
    url = str(request.getfixturevalue("tagging_warehouse"))
    run_tagging(url, Fake("fake"), prompt_version=VERSION)  # a tag row for each eligible message
    return url


@pytest.fixture
def label_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "labels"
    directory.mkdir()
    write_tags(directory / "tag_labels.csv", _tags(A1, A2, A3, E1, E2, BO))
    write_questions(
        directory / "questions.csv",
        [
            _question(1, A1, A1, E1),  # source ineligible after the revocation: dropped
            _question(2, E1, E1, A2),  # loses one gold reference, keeps the question
            _question(3, E2, E2, E1),  # untouched
            _question(4, A3, A3),  # left with no gold
        ],
    )
    rows = [evaluation.PredictionRow(i, "food", "neutral", 0.5) for i in (A1, A2, A3, E1, E2)]
    evaluation.write_predictions(directory, MODEL_ID, VERSION, rows)
    monkeypatch.setenv("GCW_LABEL_DIR", str(directory))
    return directory


def _revoke_ana(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("UPDATE raw.sender_consent SET consent = 'no' WHERE sender_name = 'SENDERANA'")


def _tag_count(url: str) -> int:
    with psycopg.connect(url) as conn:
        row = conn.execute("SELECT count(*) FROM enrich.message_tags").fetchone()
    assert row is not None
    return int(row[0])


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()
    }


def _run(capsys: pytest.CaptureFixture[str], apply: bool) -> list[str]:
    get_database_settings.cache_clear()
    assert consent_prune.run(apply=apply, start_db=lambda: None) == 0
    return capsys.readouterr().out.splitlines()


def test_with_nobody_revoked_the_only_ineligible_label_is_the_declined_senders(
    warehouse: str, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = _run(capsys, apply=False)
    assert lines == [
        "target database: gcw_test",
        "ineligible messages: 1",  # the declined sender's message, labeled by mistake
        "tags to prune: 0",
        "label rows to prune: 1",
        "question gold references to prune: 0",
        "questions left with no gold: 0",
        "prediction rows to prune: 0",
        "mode: dry run",
        "consent-prune: ok",
    ]


def test_a_dry_run_reports_what_a_revocation_leaves_behind_and_changes_nothing(
    warehouse: str, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _revoke_ana(warehouse)
    before_files, before_tags = _snapshot(label_dir), _tag_count(warehouse)

    lines = _run(capsys, apply=False)

    assert lines == [
        "target database: gcw_test",
        "ineligible messages: 4",  # a1 a2 a3 and the declined sender's message
        "tags to prune: 3",
        "label rows to prune: 4",
        "question gold references to prune: 3",  # a1 (q1), a2 (q2), a3 (q4)
        "questions left with no gold: 2",  # q1 (source gone) and q4
        "prediction rows to prune: 3",
        "mode: dry run",
        "consent-prune: ok",
    ]
    assert _snapshot(label_dir) == before_files and _tag_count(warehouse) == before_tags


def test_apply_removes_every_derived_record_and_a_rerun_finds_nothing(
    warehouse: str, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _revoke_ana(warehouse)
    dry = _run(capsys, apply=False)
    applied = _run(capsys, apply=True)
    assert applied[:-2] == dry[:-2] and applied[-2:] == ["mode: applied", "consent-prune: ok"]

    assert [t.message_id for t in read_tags(label_dir / "tag_labels.csv")] == [E1, E2]
    questions = read_questions(label_dir / "questions.csv")
    assert [(q.question_id, q.gold_message_ids) for q in questions] == [
        ("q002", (E1,)),
        ("q003", (E2, E1)),
    ]
    assert set(evaluation.read_predictions(label_dir, MODEL_ID, VERSION)) == {E1, E2}
    assert _tag_count(warehouse) == 2
    for path in (label_dir / "tag_labels.csv", label_dir / "questions.csv"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    prediction_file = label_dir / "predictions" / f"{MODEL_ID}__{VERSION}.csv"
    assert stat.S_IMODE(prediction_file.stat().st_mode) == 0o600
    assert not list(label_dir.rglob(".*tmp"))  # no temporary file left behind

    for apply in (False, True):  # a rerun reports zero everywhere, in either mode
        again = _run(capsys, apply=apply)
        assert again[1:7] == [
            "ineligible messages: 0",
            "tags to prune: 0",
            "label rows to prune: 0",
            "question gold references to prune: 0",
            "questions left with no gold: 0",
            "prediction rows to prune: 0",
        ]


def test_nothing_is_printed_but_the_fixed_lines(
    warehouse: str, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _revoke_ana(warehouse)
    lines = _run(capsys, apply=True)
    assert len(lines) == len(consent_prune.SHAPES)
    text = "\n".join(lines)
    assert SENTINEL not in text and "SENDER" not in text and str(label_dir) not in text
    assert re.search(r"[0-9a-f]{64}", text) is None  # no message ID


def test_a_database_outside_the_allowed_list_is_refused_before_anything_starts(
    warehouse: str,
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    started: list[str] = []
    monkeypatch.setenv("GCW_DB_NAME", "gcw_other")
    monkeypatch.setenv("DATABASE_URL", "")
    get_database_settings.cache_clear()
    before = _snapshot(label_dir)
    assert consent_prune.run(apply=True, start_db=lambda: started.append("db")) == 1
    assert (
        capsys.readouterr().out == "consent-prune: failed at database (UnexpectedDatabaseError)\n"
    )
    assert started == [] and _snapshot(label_dir) == before


def test_an_unset_or_in_repo_label_directory_is_refused(
    warehouse: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for value in ("", str(Path(__file__).resolve().parent)):
        monkeypatch.setenv("GCW_LABEL_DIR", value)
        assert consent_prune.run(apply=True, start_db=lambda: None) == 1
        assert capsys.readouterr().out.startswith("consent-prune: failed at label directory (")


def test_a_malformed_private_file_fails_closed_and_changes_nothing(
    warehouse: str, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _revoke_ana(warehouse)
    (label_dir / "predictions" / "notes.csv").write_text("anything\n", encoding="utf-8")
    before, tags = _snapshot(label_dir), _tag_count(warehouse)
    get_database_settings.cache_clear()
    assert consent_prune.run(apply=True, start_db=lambda: None) == 1
    assert capsys.readouterr().out == (
        "consent-prune: failed at prune (UnexpectedPredictionsFileError)\n"
    )
    assert _snapshot(label_dir) == before and _tag_count(warehouse) == tags  # labels not rewritten


def test_files_that_do_not_exist_are_not_an_error(
    warehouse: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("GCW_LABEL_DIR", str(empty))
    _revoke_ana(warehouse)
    lines = _run(capsys, apply=True)
    assert "tags to prune: 3" in lines and "label rows to prune: 0" in lines
    assert list(empty.iterdir()) == []  # nothing was created
