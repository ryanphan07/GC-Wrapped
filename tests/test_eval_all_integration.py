"""`make eval-all` (D122) against gcw_test: every command runs for real, against the stub embed and
llm servers, on test_retrieval_integration's warehouse and questions.
"""

import json
import re
from pathlib import Path

import psycopg
import pytest

from gcwrapped import eval_all
from gcwrapped.answers import commands, comparison, report
from gcwrapped.cli import build_parser
from gcwrapped.retrieval import commands as retrieval_commands
from gcwrapped.retrieval import evaluation as retrieval
from gcwrapped.retrieval import report as retrieval_report
from gcwrapped.tagging import commands as tagging
from tagging_stub import StubLlm
from test_answers_integration import (
    NO_SERVICES,
    VERSION,
    VERSION_B,
    _reply_b_answers_everything,
    llm,
)
from test_retrieval_integration import (
    _assert_clean,
    _write_questions,
    label_dir,
    stub,
    warehouse,
)

__all__ = ["label_dir", "llm", "stub", "warehouse"]  # fixtures, used by name

pytestmark = pytest.mark.integration

REAL_STEPS = [
    "embed",
    "retrieval-eval vector",
    "retrieval-eval hybrid",
    "retrieval-compare",
    "retrieval-promote",
    "answer-eval a",
    "answer-eval b",
    "answer-compare",
    "answer-promote",
]
SHAPES = (
    r"== (" + "|".join(REAL_STEPS) + r") ==",
    *retrieval_report.EMBED_SHAPES,
    *retrieval_report.EVAL_SHAPES,
    *retrieval_report.COMPARE_SHAPES,
    *retrieval_report.PROMOTE_SHAPES,
    *report.EVAL_SHAPES,
    *report.COMPARE_SHAPES,
    *report.PROMOTE_SHAPES,
)


def _assert_fixed(lines: list[str]) -> None:
    for line in lines:
        assert any(re.fullmatch(shape, line) for shape in SHAPES), line


def _headers(lines: list[str]) -> list[str]:
    return [line[3:-3] for line in lines if line.startswith("== ")]


def _section(lines: list[str], step: str) -> list[str]:
    start = lines.index(eval_all.header(step)) + 1
    end = next((n for n in range(start, len(lines)) if lines[n].startswith("== ")), len(lines))
    return lines[start:end]


def _promoted(url: str) -> tuple[str, str]:
    with psycopg.connect(url, autocommit=True) as conn:
        return retrieval.default_method(conn), comparison.default_variant(conn)


def test_every_step_runs_in_order_and_the_winners_are_promoted(
    warehouse: str,
    stub: object,
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with StubLlm(reply=_reply_b_answers_everything) as server:
        monkeypatch.setenv("GCW_LLM_URL", server.url)
        assert eval_all.run(services=NO_SERVICES) == 0
    lines = capsys.readouterr().out.splitlines()
    _assert_fixed(lines)
    _assert_clean("\n".join(lines), extra=("midterm", "ramen", "friday", str(label_dir)))
    assert _headers(lines) == REAL_STEPS
    for step in REAL_STEPS:
        last = _section(lines, step)[-1]
        assert last == f"{step.split()[0]}: ok", step
    assert "method: hybrid" in _section(lines, "retrieval-eval hybrid")
    assert f"answer prompt version: {VERSION}" in _section(lines, "answer-eval a")
    assert f"answer prompt version: {VERSION_B}" in _section(lines, "answer-eval b")

    # what was promoted is what each verdict named
    verdicts = label_dir / "experiments"
    (retrieval_verdict,) = verdicts.glob("retrieval-compare-*.json")
    (answer_verdict,) = verdicts.glob("answer-compare-*.json")
    method = json.loads(retrieval_verdict.read_text())["winner"]
    variant = json.loads(answer_verdict.read_text())["winner"]
    assert _promoted(warehouse) == (method, variant)
    assert f"promoted method: {method}" in lines and f"promoted variant: {variant}" in lines
    # answer-eval ran with the method promoted just before it
    assert f"retrieval method: {method}" in _section(lines, "answer-eval a")
    stored = {p.name for p in label_dir.rglob("*") if p.is_file()}
    assert {"vector.json", "hybrid.json"} <= stored


def test_the_synthetic_form_skips_both_promote_steps(
    warehouse: str,
    stub: object,
    llm: StubLlm,
    label_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    committed = _write_questions(tmp_path)
    (label_dir / "questions.csv").unlink()
    monkeypatch.setattr(tagging, "SYNTHETIC_DATABASE", "gcw_test")
    monkeypatch.setattr(retrieval_commands, "SYNTHETIC_QUESTIONS", committed)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    assert eval_all.run(synthetic=True, services=NO_SERVICES) == 0
    lines = capsys.readouterr().out.splitlines()
    _assert_fixed(lines)
    assert _headers(lines) == [s for s in REAL_STEPS if not s.endswith("promote")]
    assert lines[-1] == "answer-compare: ok"
    assert _promoted(warehouse) == ("vector", "a")
    assert list(label_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("broken", "failure"),
    [
        ("questions", "retrieval-eval: failed at questions (NoScorableQuestionsError)"),
        ("llm", "answer-eval: failed at answering (ProviderUnavailable)"),
    ],
)
def test_the_first_failure_prints_one_line_and_stops_the_run(
    warehouse: str,
    stub: object,
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    broken: str,
    failure: str,
) -> None:
    if broken == "questions":
        (label_dir / "questions.csv").unlink()
    with StubLlm() as gone:
        monkeypatch.setenv("GCW_LLM_URL", gone.url)
    assert eval_all.run(services=NO_SERVICES) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == failure
    assert [line for line in lines if ": failed at " in line] == [failure]
    failed_step = "retrieval-eval vector" if broken == "questions" else "answer-eval a"
    assert _headers(lines) == REAL_STEPS[: REAL_STEPS.index(failed_step) + 1]
    assert _promoted(warehouse)[1] == "a"


def test_the_cli_takes_the_synthetic_flag() -> None:
    parser = build_parser()
    assert not parser.parse_args(["eval-all"]).synthetic
    assert parser.parse_args(["eval-all", "--synthetic"]).synthetic
    assert commands.Services().db is not None  # the real command starts its own services
