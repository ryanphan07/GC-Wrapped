"""Hand grading without a database (D120): the keys, what is saved and when, the private file, the
scores and the fixed output.
"""

import csv
import re
import stat
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from gcwrapped.answers import commands, evaluation, grades, report
from gcwrapped.answers.excerpts import Source, SourceMessage
from gcwrapped.answers.pipeline import ABSTAINED, ANSWERED, INVALID, TIMED_OUT, Answer
from gcwrapped.cli import build_parser
from gcwrapped.labeling.schema import QuestionLabel
from gcwrapped.tagging import model
from gcwrapped.tagging.evaluation import OutsideRepoError
from gcwrapped.tagging.report import ShapeError, check_lines

REPO = Path(__file__).resolve().parents[1]
SENTINEL = "SENTINELTEXT"
VERSION = "0123456789ab"
MODEL_ID = model.MODEL_ID


def _id(n: int) -> str:
    return f"{n:064x}"


def _question(n: int, text: str = "") -> QuestionLabel:
    return QuestionLabel(
        f"q{n:03d}", text or f"{SENTINEL}Q{n}", (_id(n),), _id(n), "2026-11-10T00:00:00+00:00"
    )


def _source(number: int, message: int, text: str = "") -> Source:
    return Source(
        number,
        SourceMessage(
            _id(message),
            "t",
            "t:s1",
            number,
            f"{SENTINEL}SENDER{message}",
            datetime(2026, 3, 4, 17, 5),
            text or f"{SENTINEL}MESSAGE{message}",
        ),
    )


def _answer(question: str) -> Answer:
    """By question text: the end of it names the outcome; anything else is answered."""
    sources = (_source(1, 1), _source(2, 2, f"{SENTINEL}two\x1b[2J\nlines"))
    if question.endswith("abstain"):
        return Answer(ABSTAINED, "", sources, ())
    if question.endswith("invalid"):
        return Answer(INVALID, "", sources, ())
    if question.endswith("timeout"):
        return Answer(TIMED_OUT, "", sources, ())
    return Answer(ANSWERED, f"{SENTINEL}REPLY [2][1]\x1b]0;x\x07", sources, (2, 1))


class KeyTerminal:
    """Keys come from a script; an exception in it is raised where that key would be read."""

    def __init__(self, *keys: str | BaseException) -> None:
        self._keys: Iterator[str | BaseException] = iter(keys)
        self.printed: list[str] = []
        self.keys_read = 0
        self.discards = 0

    def key(self) -> str:
        self.keys_read += 1
        found = next(self._keys, "")  # end of input
        if isinstance(found, BaseException):
            raise found
        return found

    def line(self, prompt: str) -> str | None:
        raise AssertionError("grading reads single keys")

    def out(self, text: str) -> None:
        self.printed.append(text)

    def discard_input(self) -> None:
        self.discards += 1


def _grade(terminal: KeyTerminal, questions: Sequence[QuestionLabel]) -> list[grades.GradeRow]:
    saved: list[grades.GradeRow] = []
    clock = iter(range(0, 1000, 2)).__next__
    commands.grade_loop(terminal, _answer, questions, saved.append, clock=clock)
    return saved


# --- the keys and what is saved ----------------------------------------------------------------


def test_each_grade_and_support_key_is_recorded_with_the_cited_ids() -> None:
    terminal = KeyTerminal("c", "y", "P", "N", "w", "y")
    saved = _grade(terminal, [_question(1), _question(2), _question(3)])
    assert [(r.question_id, r.grade, r.supported) for r in saved] == [
        ("q001", "correct", "yes"),
        ("q002", "partial", "no"),
        ("q003", "wrong", "yes"),
    ]
    assert saved[0] == grades.GradeRow("q001", ANSWERED, "correct", "yes", (_id(2), _id(1)), 2.0)
    assert terminal.discards == 3  # type-ahead is dropped before each answer is graded


def test_an_invalid_key_prompts_again() -> None:
    terminal = KeyTerminal("x", "y", "\n", "c", "c", "3", "n")
    saved = _grade(terminal, [_question(1)])
    assert [(r.grade, r.supported) for r in saved] == [("correct", "no")]
    assert terminal.printed.count(commands.GRADE_PROMPT) == 4
    assert terminal.printed.count(commands.SUPPORT_PROMPT) == 3


@pytest.mark.parametrize(
    ("name", "outcome"), [("abstain", ABSTAINED), ("invalid", INVALID), ("timeout", TIMED_OUT)]
)
def test_any_other_outcome_is_recorded_without_a_key(name: str, outcome: str) -> None:
    terminal = KeyTerminal()
    saved = _grade(terminal, [_question(1, name), _question(2, f"second {name}")])
    assert [(r.outcome, r.grade, r.supported, r.cited_message_ids) for r in saved] == [
        (outcome, "", "", ()),
        (outcome, "", "", ()),
    ]
    assert terminal.keys_read == 0 and commands.GRADE_PROMPT not in terminal.printed


def test_the_terminal_shows_the_question_answer_and_cited_messages_made_safe() -> None:
    terminal = KeyTerminal("c", "y")
    _grade(terminal, [_question(1, "when\x1b[2J\nthen")])
    shown = "\n".join(terminal.printed)
    assert "\x1b" not in shown and "\x07" not in shown
    assert "[1 of 1] question: when�[2J then" in terminal.printed
    assert f"{SENTINEL}REPLY [2][1]�]0;x�" in terminal.printed
    start = terminal.printed.index(f"{SENTINEL}REPLY [2][1]�]0;x�")
    cited = terminal.printed[start + 2 : start + 4]
    assert [line.split("]")[0] for line in cited] == ["[1", "[2"]
    assert f"{SENTINEL}two�[2J lines" in cited[1]


def test_an_abstention_shows_only_not_in_the_chat() -> None:
    terminal = KeyTerminal()
    _grade(terminal, [_question(1, "abstain")])
    assert commands.NOT_IN_CHAT in terminal.printed
    assert not any(f"{SENTINEL}MESSAGE" in line for line in terminal.printed)


@pytest.mark.parametrize("keys", [("c", "y", "q"), ("c", "y", "w", "q"), ("c", "y", "")])
def test_q_or_end_of_input_stops_without_saving_the_open_question(keys: tuple[str, ...]) -> None:
    terminal = KeyTerminal(*keys)
    saved = _grade(terminal, [_question(1), _question(2), _question(3)])
    assert [r.question_id for r in saved] == ["q001"]
    assert not any("[3 of 3]" in line for line in terminal.printed)


def test_ctrl_c_keeps_the_finished_grades() -> None:
    terminal = KeyTerminal("c", "y", KeyboardInterrupt())
    saved = _grade(terminal, [_question(1), _question(2)])
    assert [r.question_id for r in saved] == ["q001"]


def test_ctrl_c_during_an_answer_keeps_the_finished_grades() -> None:
    saved: list[grades.GradeRow] = []

    def slow(question: str) -> Answer:
        if question.endswith("slow"):
            raise KeyboardInterrupt
        return _answer(question)

    commands.grade_loop(
        KeyTerminal("c", "y"), slow, [_question(1), _question(2, "slow")], saved.append
    )
    assert [r.question_id for r in saved] == ["q001"]


# --- the order and the file --------------------------------------------------------------------


def test_the_order_is_seeded_and_stable_when_questions_change() -> None:
    questions = [_question(n) for n in range(1, 31)]
    order = [q.question_id for q in grades.grading_order(questions)]
    assert order == [q.question_id for q in grades.grading_order(reversed(questions))]
    assert sorted(order) == [q.question_id for q in questions] and order != sorted(order)
    fewer = [q.question_id for q in grades.grading_order(questions[5:])]
    assert fewer == [i for i in order if i in set(fewer)]


def _row(
    n: int, outcome: str = ANSWERED, grade: str = "correct", supported: str = "yes"
) -> grades.GradeRow:
    if outcome != ANSWERED:
        return grades.GradeRow(f"q{n:03d}", outcome, "", "", (), 1.5)
    return grades.GradeRow(f"q{n:03d}", outcome, grade, supported, (_id(n), _id(n + 1)), 1.5)


def test_the_file_is_private_and_holds_ids_and_numbers_only(tmp_path: Path) -> None:
    saved: list[grades.GradeRow] = []

    def save(row: grades.GradeRow) -> None:
        saved.append(row)
        grades.write_grades(tmp_path, MODEL_ID, VERSION, saved)

    commands.grade_loop(
        KeyTerminal("p", "n"), _answer, [_question(1), _question(2, f"{SENTINEL} abstain")], save
    )
    path = tmp_path / "grades" / f"{MODEL_ID}__{VERSION}.csv"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = path.read_text()
    assert SENTINEL not in text and "REPLY" not in text
    with path.open(newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == [
        "question_id",
        "outcome",
        "grade",
        "supported",
        "cited_message_ids",
        "seconds",
    ]
    assert rows[1][:4] == ["q001", "answered", "partial", "no"]
    assert rows[1][4] == f"{_id(2)};{_id(1)}"
    assert rows[2][:5] == ["q002", "abstained", "", "", ""]
    assert [replace(r, seconds=0) for r in grades.read_grades(tmp_path, MODEL_ID, VERSION)] == [
        replace(r, seconds=0) for r in saved
    ]


def test_a_missing_file_has_no_grades(tmp_path: Path) -> None:
    assert grades.read_grades(tmp_path, MODEL_ID, VERSION) == []


@pytest.mark.parametrize(
    "row",
    [
        replace(_row(1), question_id=SENTINEL),
        replace(_row(1), outcome="maybe"),
        replace(_row(1), grade=SENTINEL),
        replace(_row(1), grade=""),
        replace(_row(1), supported=""),
        replace(_row(1), cited_message_ids=()),
        replace(_row(1), cited_message_ids=(SENTINEL,)),
        replace(_row(1), cited_message_ids=tuple(_id(n) for n in range(6))),
        replace(_row(1, ABSTAINED), grade="correct"),
        replace(_row(1, ABSTAINED), cited_message_ids=(_id(1),)),
        replace(_row(1), seconds=-1),
        replace(_row(1), seconds=float("nan")),
    ],
)
def test_a_bad_row_is_never_written_or_read(tmp_path: Path, row: grades.GradeRow) -> None:
    with pytest.raises(grades.GradesError):
        grades.write_grades(tmp_path, MODEL_ID, VERSION, [row])
    assert not (tmp_path / "grades").exists()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "wrong,header\n",
        ",".join(grades.HEADER) + "\nq001,answered\n",
        ",".join(grades.HEADER) + "\nq001,abstained,,,,x\n",
        ",".join(grades.HEADER) + "\nq001,abstained,,,,1\nq001,abstained,,,,1\n",
    ],
)
def test_a_malformed_file_is_refused(tmp_path: Path, text: str) -> None:
    (tmp_path / "grades").mkdir()
    (tmp_path / "grades" / f"{MODEL_ID}__{VERSION}.csv").write_text(text)
    with pytest.raises(grades.GradesError):
        grades.read_grades(tmp_path, MODEL_ID, VERSION)


@pytest.mark.parametrize(
    ("model_id", "version"), [("../x", VERSION), (MODEL_ID, "../../etc"), ("a/b", VERSION)]
)
def test_the_file_name_is_validated_like_an_answers_file(
    tmp_path: Path, model_id: str, version: str
) -> None:
    with pytest.raises(evaluation.AnswerResultsError):
        grades.grades_path(tmp_path, model_id, version)


def test_grades_are_never_written_inside_the_repo() -> None:
    with pytest.raises(OutsideRepoError):
        grades.write_grades(REPO / "tmp-label-dir", MODEL_ID, VERSION, [_row(1)])
    assert not (REPO / "tmp-label-dir").exists()


# --- scores and output -------------------------------------------------------------------------


def test_the_summary_counts_and_the_three_rates_with_wilson_intervals() -> None:
    rows = [
        _row(1),
        _row(2),
        _row(3, grade="partial", supported="no"),
        _row(4, grade="wrong", supported="yes"),
        _row(5, ABSTAINED),
        _row(6, ABSTAINED),
        _row(7, ABSTAINED),
        _row(8, INVALID),
        _row(9, TIMED_OUT),
        _row(10, ABSTAINED),
    ]
    lines = report.render_summary(MODEL_ID, VERSION, "hybrid", grades.summarize(rows))
    check_lines(lines, report.SUMMARY_SHAPES)
    assert lines == [
        f"model id: {MODEL_ID}",
        f"answer prompt version: {VERSION}",
        "retrieval method: hybrid",
        "graded questions: 10",
        "answered: 4",
        "abstained: 4",
        "invalid outputs: 1",
        "timed out: 1",
        "correct: 2",
        "partial: 1",
        "wrong: 1",
        "supported: 3",
        "answer accuracy: 0.200",
        "answer accuracy 95% interval: [0.057, 0.510]",
        "precision: 0.500",
        "precision 95% interval: [0.150, 0.850]",
        "supported rate: 0.750",
        "supported rate 95% interval: [0.301, 0.954]",
        "grade-summary: ok",
    ]


def test_with_nothing_answered_precision_and_support_are_not_available() -> None:
    summary = grades.summarize([_row(1, ABSTAINED)])
    lines = report.render_summary(MODEL_ID, VERSION, "vector", summary)
    assert lines[-7:] == [
        "answer accuracy: 0.000",
        "answer accuracy 95% interval: [0.000, 0.793]",
        "precision: n/a",
        "precision 95% interval: n/a",
        "supported rate: n/a",
        "supported rate 95% interval: n/a",
        "grade-summary: ok",
    ]


def test_no_grades_is_an_error() -> None:
    with pytest.raises(grades.NoGradesError):
        grades.summarize([])


def test_the_summary_leaks_nothing_and_only_fixed_shapes_pass() -> None:
    saved = _grade(KeyTerminal("c", "y"), [_question(1), _question(2, "abstain")])
    lines = report.render_summary(MODEL_ID, VERSION, "vector", grades.summarize(saved))
    text = "\n".join(lines)
    assert SENTINEL not in text and not re.search(r"[0-9a-f]{64}|q\d{3}", text)
    with pytest.raises(ShapeError):
        report.render_summary(SENTINEL, VERSION, "vector", grades.summarize(saved))
    with pytest.raises(ShapeError):
        report.render_summary(MODEL_ID, VERSION, SENTINEL, grades.summarize(saved))
    check_lines(report.render_grading(2, 7, 3), report.GRADE_SHAPES)
    assert report.failure_line("grade-answers", "grades", grades.GradesError()) == (
        "grade-answers: failed at grades (GradesError)"
    )
    assert report.failure_line("grade-summary", "scoring", grades.NoGradesError()) == (
        "grade-summary: failed at scoring (NoGradesError)"
    )


def test_the_cli_defaults_to_fifty_answers() -> None:
    parser = build_parser()
    assert parser.parse_args(["grade-answers"]).n == 50
    assert parser.parse_args(["grade-answers", "--n", "5", "--synthetic"]).n == 5
    assert parser.parse_args(["grade-summary", "--synthetic"]).synthetic


@pytest.mark.parametrize(("make_args", "argv"), [([], []), (["N=5"], ["--n", "5"])])
def test_make_passes_n_only_when_set(make_args: list[str], argv: list[str]) -> None:
    import os
    import subprocess

    env = {k: v for k, v in os.environ.items() if k not in ("N", "MAKEFLAGS", "MAKELEVEL")}
    shown = subprocess.run(
        ["make", "-n", "grade-answers", *make_args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert shown == ["uv", "run", "gcw", "grade-answers", *argv]
