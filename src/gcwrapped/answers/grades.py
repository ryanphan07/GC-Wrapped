"""Hand grades of answers (D120): the private file, the grading order and the scores. Nothing here
prints.

Grades go to <directory outside the repo>/grades/<model id>__<answer prompt version>.csv, mode
0600, rewritten atomically after every graded question: question ID, outcome, grade, whether the
cited messages support it, cited message IDs and seconds. Never question, answer or message text.

Answer accuracy is correct over graded questions: every eval question was written from a message,
so its answer is in the chat, and an abstention or a failed request counts as not correct.
Precision is correct over answered questions, and the supported rate is supported over answered.
Each has a Wilson score 95% interval (D117).
"""

import csv
import hashlib
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from gcwrapped.answers.evaluation import check_file_key
from gcwrapped.answers.pipeline import ABSTAINED, ANSWERED, INVALID, OUTCOMES, TIMED_OUT
from gcwrapped.answers.prompt import MAX_CITATIONS
from gcwrapped.labeling.files import MESSAGE_ID, QUESTION_ID
from gcwrapped.labeling.schema import GOLD_SEPARATOR, QuestionLabel
from gcwrapped.retrieval import evaluation as retrieval
from gcwrapped.tagging import evaluation as tag_evaluation

GRADES_DIR = "grades"
GRADE_SEED = 20261125  # fixes the order questions are graded in
DEFAULT_COUNT = 50  # answers graded per run, unless N says otherwise
CORRECT = "correct"
PARTIAL = "partial"
WRONG = "wrong"
GRADES = (CORRECT, PARTIAL, WRONG)
SUPPORTED = ("yes", "no")
HEADER = ("question_id", "outcome", "grade", "supported", "cited_message_ids", "seconds")


class GradesError(Exception):
    """A grades file is malformed. Only the class name is ever printed."""


class NoGradesError(Exception):
    """Nothing has been graded for the current model and answer prompt."""


@dataclass(frozen=True)
class GradeRow:
    question_id: str
    outcome: str
    grade: str  # one of GRADES for an answered question, else empty
    supported: str  # "yes" or "no" for an answered question, else empty
    cited_message_ids: tuple[str, ...]
    seconds: float


def grading_order(questions: Iterable[QuestionLabel]) -> list[QuestionLabel]:
    """A seeded shuffle that stays put as the set changes: sorted by a hash of the seed and the
    question ID, so adding or dropping a question never reorders the others."""

    def key(question: QuestionLabel) -> str:
        return hashlib.sha256(f"{GRADE_SEED}:{question.question_id}".encode()).hexdigest()

    return sorted(questions, key=key)


def _check(row: GradeRow) -> None:
    answered = row.outcome == ANSWERED
    cited = row.cited_message_ids
    if (
        QUESTION_ID.fullmatch(row.question_id) is None
        or row.outcome not in OUTCOMES
        or row.grade not in (GRADES if answered else ("",))
        or row.supported not in (SUPPORTED if answered else ("",))
        or bool(cited) != answered
        or len(cited) > MAX_CITATIONS
        or not all(MESSAGE_ID.fullmatch(m) for m in cited)
        or not math.isfinite(row.seconds)
        or row.seconds < 0
    ):
        raise GradesError


def _unique(rows: Sequence[GradeRow]) -> None:
    if len({r.question_id for r in rows}) != len(rows):
        raise GradesError


def grades_path(root: Path, model_id: str, prompt_version: str) -> Path:
    check_file_key(model_id, prompt_version)
    return root / GRADES_DIR / f"{model_id}__{prompt_version}.csv"


def write_grades(root: Path, model_id: str, prompt_version: str, rows: Sequence[GradeRow]) -> None:
    for row in rows:
        _check(row)
    _unique(rows)
    folder = tag_evaluation.private_subdir(root, GRADES_DIR)
    path = grades_path(folder.parent, model_id, prompt_version)

    def write(handle: TextIO) -> None:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        for row in rows:
            writer.writerow(
                [
                    row.question_id,
                    row.outcome,
                    row.grade,
                    row.supported,
                    GOLD_SEPARATOR.join(row.cited_message_ids),
                    f"{row.seconds:.3f}",
                ]
            )

    tag_evaluation.write_private(path, write)


def read_grades(root: Path, model_id: str, prompt_version: str) -> list[GradeRow]:
    """The rows in file order; a missing file is an empty list."""
    path = grades_path(root, model_id, prompt_version)
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            lines = list(csv.reader(handle))
        if not lines or tuple(lines[0]) != HEADER:
            raise GradesError
        rows = []
        for question_id, outcome, grade, supported, cited, seconds in lines[1:]:
            row = GradeRow(
                question_id,
                outcome,
                grade,
                supported,
                tuple(cited.split(GOLD_SEPARATOR)) if cited else (),
                float(seconds),
            )
            _check(row)
            rows.append(row)
        _unique(rows)
    except (OSError, ValueError, csv.Error):  # ValueError: a bad width, number or encoding
        raise GradesError from None
    return rows


@dataclass(frozen=True)
class Summary:
    graded: int
    answered: int
    abstained: int
    invalid: int
    timed_out: int
    correct: int
    partial: int
    wrong: int
    supported: int
    accuracy: retrieval.Estimate
    precision: retrieval.Estimate | None  # None when nothing was answered
    supported_rate: retrieval.Estimate | None


def _estimate(count: int, n: int) -> retrieval.Estimate | None:
    if n == 0:
        return None
    return retrieval.Estimate(count / n, retrieval.wilson_interval(count, n))


def summarize(rows: Sequence[GradeRow]) -> Summary:
    if not rows:
        raise NoGradesError
    outcomes = [r.outcome for r in rows]
    grades = [r.grade for r in rows]
    answered = outcomes.count(ANSWERED)
    correct = grades.count(CORRECT)
    supported = sum(r.supported == "yes" for r in rows)
    return Summary(
        graded=len(rows),
        answered=answered,
        abstained=outcomes.count(ABSTAINED),
        invalid=outcomes.count(INVALID),
        timed_out=outcomes.count(TIMED_OUT),
        correct=correct,
        partial=grades.count(PARTIAL),
        wrong=grades.count(WRONG),
        supported=supported,
        accuracy=retrieval.Estimate(
            correct / len(rows), retrieval.wilson_interval(correct, len(rows))
        ),
        precision=_estimate(correct, answered),
        supported_rate=_estimate(supported, answered),
    )
