"""tag-eval, tag-compare, tag-promote and the promoted model (D92, D93) against gcw_test.

Only docker and the model are replaced: a stub llama.cpp server answers, and the compose starters
are `None` or recorders. Eligibility, scoring, files, the verdict, the promotion row and the
fixed output run for real. Sender names and message text are sentinels that must never be printed.
"""

import logging
import re
import stat
import time
from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest

from gcwrapped.config import get_database_settings
from gcwrapped.labeling.files import write_tags
from gcwrapped.labeling.schema import TagLabel
from gcwrapped.labeling.source import load_eligible
from gcwrapped.ledger import REPO_ROOT
from gcwrapped.tagging import commands, evaluation, model, report
from gcwrapped.tagging.prompt import prompt_version_for
from tagging_stub import StubLlm
from test_tagging_integration import (
    ELIGIBLE,
    ROWS,
    SENTINEL,
    Fake,
    _key,
)
from test_tagging_integration import warehouse as tagging_warehouse  # noqa: F401  (a fixture)

# Both Qwen3.5 arms send the same thinking-off setting, so they share one prompt version (D98).
PROMPT_VERSION = prompt_version_for(model.REGISTRY[model.MODEL_ID].chat_template_kwargs)

pytestmark = pytest.mark.integration

A_ID = model.MODEL_ID
B_ID = model.SMALL_MODEL_ID
ID = {n: _key(ROWS[n][0], n) for n in range(len(ROWS))}
LOCAL_OK = [0, 2, 4, 8, 9]  # a1 a2 a3 e1 e2; the others are declined, undecided, unlisted or blank
NOT_OK = [1, 3, 7]  # labeled, but their senders are not local_ok
GOLD = {0: "food", 2: "food", 4: "food", 8: "plans", 9: "plans", 1: "work", 3: "work", 7: "news"}
SENT = {0: "neutral", 2: "neutral", 4: "neutral", 8: "positive", 9: "positive"}
HEX64 = re.compile(r"[0-9a-f]{64}")


def _labels() -> dict[str, tuple[str, str]]:
    return {ID[n]: (topic, SENT.get(n, "negative")) for n, topic in GOLD.items()}


def _write_labels(directory: Path) -> Path:
    path = directory / "tag_labels.csv"
    write_tags(
        path,
        [TagLabel(i, t, s, "2026-11-10T00:00:00+00:00") for i, (t, s) in _labels().items()],
    )
    return path


@pytest.fixture
def warehouse(request: pytest.FixtureRequest) -> str:
    return str(request.getfixturevalue("tagging_warehouse"))


@pytest.fixture
def label_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "labels"
    directory.mkdir()
    _write_labels(directory)
    monkeypatch.setenv("GCW_LABEL_DIR", str(directory))
    return directory


def _recorder(started: list[str]) -> Callable[[], None]:
    def start() -> None:
        started.append("db")

    return start


def _assert_clean(*texts: str, extra: tuple[str, ...] = ()) -> None:
    for text in texts:
        assert SENTINEL not in text and "SENDER" not in text
        assert HEX64.search(text) is None  # no message ID
        for fragment in extra:
            assert fragment not in text


def _promoted_rows(url: str) -> list[tuple[str]]:
    with psycopg.connect(url) as conn:
        return conn.execute("SELECT model_id FROM enrich.tag_default").fetchall()


def _predictions(
    model_id: str, seconds: float, ids: list[int] = LOCAL_OK
) -> list[evaluation.PredictionRow]:
    return [evaluation.PredictionRow(ID[n], "food", "neutral", seconds) for n in ids]


# --- scoring against the warehouse -------------------------------------------------------------


def test_fresh_predictions_are_scored_and_labels_of_ineligible_messages_are_dropped(
    warehouse: str,
) -> None:
    index = load_eligible(warehouse)
    ids, dropped = evaluation.split_labels(_labels(), index)
    assert (len(ids), dropped) == (ELIGIBLE, len(NOT_OK))
    fake = Fake()

    rows = evaluation.run_model(fake, index, ids)
    result = evaluation.evaluate(_labels(), {r.message_id: r for r in rows}, index)

    assert len(fake.prompts) == ELIGIBLE  # declined, undecided and unlisted were never sent
    assert (result.labels, result.dropped, result.scored, result.failures) == (8, 3, 5, 0)
    assert result.topic.accuracy == pytest.approx(3 / 5)  # food everywhere: 3 of 5 gold are food
    assert result.sentiment.accuracy == pytest.approx(3 / 5)  # neutral everywhere


def test_a_failure_counts_as_wrong_and_is_timed(warehouse: str) -> None:
    index = load_eligible(warehouse)
    ids, _ = evaluation.split_labels(_labels(), index)
    rows = evaluation.run_model(Fake(fail_on=f"{SENTINEL} a2"), index, ids)
    result = evaluation.evaluate(_labels(), {r.message_id: r for r in rows}, index)
    assert result.failures == 1 and result.scored == 5
    assert result.topic.accuracy == pytest.approx(2 / 5)  # a2 was gold food, the failure is wrong
    assert all(r.seconds >= 0 for r in rows) and sum(1 for r in rows if r.topic is None) == 1


def test_labels_of_messages_that_stop_being_local_ok_are_dropped_at_scoring_time(
    warehouse: str,
) -> None:
    index = load_eligible(warehouse)
    ids, _ = evaluation.split_labels(_labels(), index)
    rows = {r.message_id: r for r in evaluation.run_model(Fake(), index, ids)}
    with psycopg.connect(warehouse, autocommit=True) as conn:
        conn.execute("UPDATE raw.sender_consent SET consent = 'no' WHERE sender_name = 'SENDERANA'")

    after = load_eligible(warehouse)
    result = evaluation.evaluate(_labels(), rows, after)
    assert (result.dropped, result.scored) == (6, 2)  # a1 a2 a3 joined the three already dropped
    both = evaluation.compare(_labels(), rows, rows, after)
    assert both.messages == 2


def test_a_label_on_a_message_that_is_a_placeholder_is_dropped_at_scoring_time(
    warehouse: str,
) -> None:
    placeholder = ID[10]  # "SENDERANA liked a message", from a consented sender
    labels = _labels() | {placeholder: ("food", "neutral")}
    index = load_eligible(warehouse)
    assert placeholder not in index
    ids, dropped = evaluation.split_labels(labels, index)
    assert (len(ids), dropped) == (ELIGIBLE, len(NOT_OK) + 1)
    rows = {r.message_id: r for r in evaluation.run_model(Fake(), index, ids)}
    result = evaluation.evaluate(labels, rows, index)
    assert (result.labels, result.dropped, result.scored) == (9, 4, 5)


# --- the commands, end to end through the real client and the stub server ----------------------


def test_tag_eval_prints_fixed_lines_and_stores_private_predictions_without_text(
    warehouse: str,
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with StubLlm() as stub:
        monkeypatch.setenv("GCW_LLM_URL", stub.url)
        assert commands.run_tag_eval(A_ID, start_db=None, start_llm=None) == 0
        out = capsys.readouterr()
        assert len(stub.requests) == ELIGIBLE and {r["model"] for r in stub.requests} == {A_ID}
    lines = out.out.splitlines()
    report.check_eval_lines(lines)
    assert lines[:2] == ["target database: gcw_test", "labels source: real"]
    assert f"prompt version: {PROMPT_VERSION}" in lines
    assert "labels: 8" in lines and "labels dropped (not local-eligible): 3" in lines
    assert "messages scored: 5" in lines and lines[-1] == "tag-eval: ok"

    path = label_dir / "predictions" / f"{A_ID}__{PROMPT_VERSION}.csv"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(evaluation.read_predictions(label_dir, A_ID, PROMPT_VERSION)) == ELIGIBLE
    assert SENTINEL not in path.read_text() and "SENDER" not in path.read_text()
    _assert_clean(out.out, out.err, caplog.text, extra=(str(label_dir), "tag_labels"))
    assert _promoted_rows(warehouse) == []  # an eval never promotes


def test_tag_eval_refuses_a_database_outside_the_allowed_list_before_starting_anything(
    warehouse: str,
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    started: list[str] = []
    monkeypatch.setenv("GCW_DB_NAME", "gcw_other")
    monkeypatch.setenv("DATABASE_URL", "")
    get_database_settings.cache_clear()
    code = commands.run_tag_eval(
        A_ID, start_db=lambda: started.append("db"), start_llm=lambda m: started.append(m)
    )
    assert code == 1
    assert capsys.readouterr().out == "tag-eval: failed at database (UnexpectedDatabaseError)\n"
    assert started == [] and not (label_dir / "predictions").exists()


def test_a_synthetic_run_refuses_any_database_but_gcw_dev(
    warehouse: str, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[str] = []
    start = _recorder(started)
    assert commands.run_tag_eval(A_ID, synthetic=True, start_db=start) == 1
    assert commands.run_tag_compare(A_ID, B_ID, synthetic=True, start_db=start) == 1
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "tag-eval: failed at database (UnexpectedDatabaseError)",
        "tag-compare: failed at database (UnexpectedDatabaseError)",
    ]
    assert started == []


def test_the_label_directory_must_be_outside_the_repo_and_nothing_is_started_or_written(
    warehouse: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    link = tmp_path / "link"
    link.symlink_to(REPO_ROOT / "tests")
    started: list[str] = []
    for bad in (str(REPO_ROOT / "tests"), str(REPO_ROOT), str(link), "", str(tmp_path / "missing")):
        monkeypatch.setenv("GCW_LABEL_DIR", bad)
        start = _recorder(started)
        assert commands.run_tag_eval(A_ID, start_db=start) == 1
        assert commands.run_tag_compare(A_ID, B_ID, start_db=start) == 1
        assert commands.run_tag_promote(A_ID, start_db=start) == 1
        out = capsys.readouterr().out.splitlines()
        assert out == [
            "tag-eval: failed at label directory (LabelDirError)",
            "tag-compare: failed at label directory (LabelDirError)",
            "tag-promote: failed at label directory (LabelDirError)",
        ]
    assert started == []
    assert not (REPO_ROOT / "tests" / "predictions").exists()
    assert not (REPO_ROOT / "tests" / "experiments").exists()


def test_a_synthetic_run_uses_a_temporary_directory_and_never_touches_labels_or_the_default(
    warehouse: str,
    label_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    synthetic = _write_labels(tmp_path)  # stands in for the committed synthetic set
    (label_dir / "tag_labels.csv").unlink()
    monkeypatch.setattr(commands, "SYNTHETIC_DATABASE", "gcw_test")
    monkeypatch.setattr(commands, "SYNTHETIC_LABELS", synthetic)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    with StubLlm() as stub:
        monkeypatch.setenv("GCW_LLM_URL", stub.url)
        for model_id in (A_ID, B_ID):
            assert (
                commands.run_tag_eval(model_id, synthetic=True, start_db=None, start_llm=None) == 0
            )
        eval_out = capsys.readouterr().out
        assert commands.run_tag_compare(A_ID, B_ID, synthetic=True, start_db=None) == 0
        compare_out = capsys.readouterr().out

    assert "labels source: synthetic" in eval_out and "labels source: synthetic" in compare_out
    (root,) = tmp_path.glob("gcw-synthetic-eval-*")
    assert (
        len(list((root / "predictions").iterdir())) == 2
        and len(list((root / "experiments").iterdir())) == 1
    )
    assert list(label_dir.iterdir()) == []  # GCW_LABEL_DIR untouched
    assert _promoted_rows(warehouse) == []  # nothing promoted
    assert commands.run_tag_promote(B_ID, start_db=None) == 1  # a synthetic verdict is no verdict
    assert capsys.readouterr().out == "tag-promote: failed at verdict (VerdictError)\n"


def _write_predictions(label_dir: Path, a_seconds: float, b_seconds: float) -> None:
    evaluation.write_predictions(label_dir, A_ID, PROMPT_VERSION, _predictions(A_ID, a_seconds))
    evaluation.write_predictions(label_dir, B_ID, PROMPT_VERSION, _predictions(B_ID, b_seconds))


def test_tag_compare_prints_the_verdict_and_records_it_privately(
    warehouse: str,
    label_dir: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _write_predictions(label_dir, a_seconds=1.0, b_seconds=0.5)

    assert commands.run_tag_compare(A_ID, B_ID, start_db=None) == 0

    out = capsys.readouterr()
    lines = out.out.splitlines()
    report.check_lines(lines, report.COMPARE_SHAPES)
    assert "messages compared: 5" in lines and f"winner: {B_ID}" in lines
    assert "speedup (B over A): 2.00" in lines
    (verdict,) = (label_dir / "experiments").iterdir()
    assert stat.S_IMODE(verdict.stat().st_mode) == 0o600
    assert evaluation.latest_winner(label_dir) == B_ID
    _assert_clean(out.out, out.err, caplog.text, verdict.read_text(), extra=(str(label_dir),))


def test_tag_compare_keeps_a_when_b_is_not_fast_enough(
    warehouse: str, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_predictions(label_dir, a_seconds=1.0, b_seconds=0.9)
    assert commands.run_tag_compare(A_ID, B_ID, start_db=None) == 0
    lines = capsys.readouterr().out.splitlines()
    assert f"winner: {A_ID}" in lines and "check speedup at least 1.5: fail" in lines
    assert evaluation.latest_winner(label_dir) == A_ID


def test_tag_compare_without_stored_predictions_fails_at_predictions(
    warehouse: str, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert commands.run_tag_compare(A_ID, B_ID, start_db=None) == 1
    assert capsys.readouterr().out == "tag-compare: failed at predictions (PredictionsError)\n"
    assert not (label_dir / "experiments").exists()


def test_tag_compare_refuses_arms_with_different_prompt_versions(
    warehouse: str, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ref = model.REFERENCE_MODEL_ID
    ref_version = prompt_version_for(model.REGISTRY[ref].chat_template_kwargs)
    assert ref_version != PROMPT_VERSION
    evaluation.write_predictions(label_dir, A_ID, PROMPT_VERSION, _predictions(A_ID, 1.0))
    evaluation.write_predictions(label_dir, ref, ref_version, _predictions(ref, 1.0))

    assert commands.run_tag_compare(A_ID, ref, start_db=None) == 1

    assert capsys.readouterr().out == "tag-compare: failed at predictions (PromptVersionMismatch)\n"
    assert not (label_dir / "experiments").exists()


# --- promotion ---------------------------------------------------------------------------------


def _verdict(label_dir: Path, b_wins: bool) -> None:
    _write_predictions(label_dir, 1.0, 0.5 if b_wins else 1.0)
    assert commands.run_tag_compare(A_ID, B_ID, start_db=None) == 0


def test_tag_promote_refuses_without_a_verdict_or_when_the_winner_is_another_model(
    warehouse: str, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert commands.run_tag_promote(B_ID, start_db=None) == 1
    assert capsys.readouterr().out == "tag-promote: failed at verdict (VerdictError)\n"

    _verdict(label_dir, b_wins=False)
    capsys.readouterr()
    assert commands.run_tag_promote(B_ID, start_db=None) == 1
    assert capsys.readouterr().out == "tag-promote: failed at verdict (NotWinnerError)\n"
    assert _promoted_rows(warehouse) == []


def test_tag_promote_sets_the_one_row_after_a_winning_verdict_and_replaces_it_later(
    warehouse: str, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _verdict(label_dir, b_wins=True)
    capsys.readouterr()

    assert commands.run_tag_promote(B_ID, start_db=None) == 0

    lines = capsys.readouterr().out.splitlines()
    report.check_lines(lines, report.PROMOTE_SHAPES)
    assert lines[1:3] == [f"promoted model id: {B_ID}", "previous model id: none"]
    assert _promoted_rows(warehouse) == [(B_ID,)]

    time.sleep(0.002)  # the next verdict file must sort after the first
    _verdict(label_dir, b_wins=False)  # a later experiment says A
    capsys.readouterr()
    assert commands.run_tag_promote(B_ID, start_db=None) == 1  # B can no longer be promoted again
    assert commands.run_tag_promote(A_ID, start_db=None) == 0
    assert f"previous model id: {B_ID}" in capsys.readouterr().out.splitlines()
    assert _promoted_rows(warehouse) == [(A_ID,)]  # still exactly one row


def test_tag_promote_applies_migration_005_on_a_database_that_lacks_it(
    warehouse: str, label_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with psycopg.connect(warehouse, autocommit=True) as conn:
        conn.execute("DROP TABLE enrich.tag_default")
        conn.execute("DELETE FROM schema_migrations WHERE version = '005_enrich_tag_default'")
    _verdict(label_dir, b_wins=True)
    capsys.readouterr()
    assert commands.run_tag_promote(B_ID, start_db=None) == 0
    assert _promoted_rows(warehouse) == [(B_ID,)]


def test_tag_promote_refuses_a_database_outside_the_allowed_list(
    warehouse: str,
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GCW_DB_NAME", "gcw_other")
    monkeypatch.setenv("DATABASE_URL", "")
    get_database_settings.cache_clear()
    assert commands.run_tag_promote(B_ID, start_db=lambda: pytest.fail("started")) == 1
    assert capsys.readouterr().out == "tag-promote: failed at database (UnexpectedDatabaseError)\n"


def test_the_table_holds_at_most_one_row(warehouse: str) -> None:
    evaluation.promote(warehouse, A_ID)
    with psycopg.connect(warehouse) as conn, pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO enrich.tag_default (singleton, model_id) VALUES (true, 'x')")


def test_the_default_model_is_used_until_a_promotion_and_the_table_may_not_exist(
    warehouse: str, clean_db: str
) -> None:
    assert evaluation.promoted_model(warehouse, "fallback") == "fallback"
    evaluation.promote(warehouse, B_ID)
    assert evaluation.promoted_model(warehouse, "fallback") == B_ID
    with psycopg.connect(clean_db, autocommit=True) as conn:
        conn.execute("DROP SCHEMA enrich CASCADE")
    assert evaluation.promoted_model(clean_db, "fallback") == "fallback"


def test_make_tag_uses_the_promoted_model_unless_one_is_given(
    warehouse: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[str] = []

    def tag(model_id: str | None = None) -> None:
        assert (
            commands.run_tag(
                model_id=model_id,
                start_db=lambda: started.append("db"),
                start_llm=lambda m: started.append(m),
            )
            == 0
        )
        capsys.readouterr()

    with StubLlm() as stub:
        monkeypatch.setenv("GCW_LLM_URL", stub.url)
        tag()  # nothing promoted: the D90 default
        evaluation.promote(warehouse, B_ID)
        tag()  # the promoted model tags everything again under its own id
        tag(A_ID)  # an explicit MODEL= wins; A is already tagged, so it sends nothing
        models_sent = [r["model"] for r in stub.requests]
    assert started == ["db", A_ID, "db", B_ID, "db", A_ID]
    assert models_sent == [A_ID] * ELIGIBLE + [B_ID] * ELIGIBLE
    with psycopg.connect(warehouse) as conn:
        counts = conn.execute(
            "SELECT model_id, count(*) FROM enrich.message_tags GROUP BY 1 ORDER BY 1"
        ).fetchall()
    assert counts == sorted([(A_ID, ELIGIBLE), (B_ID, ELIGIBLE)])


def test_make_tag_refuses_an_unregistered_model_before_starting_anything(
    warehouse: str, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[str] = []
    assert commands.run_tag(model_id="nope", start_db=lambda: started.append("db")) == 1
    assert capsys.readouterr().out == "tag: failed at model (UnknownModelError)\n"
    assert started == []
