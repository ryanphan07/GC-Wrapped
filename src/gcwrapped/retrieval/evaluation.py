"""The retrieval eval and the pre-registered comparison (D104). Nothing here prints.

A question is a hit at k when any of its gold message IDs is in the first k results; MRR@10 is the
mean of 1 / rank of the first gold ID in the first 10 (0 if none). Intervals are the tagging eval's:
95% percentile bootstrap over questions, same resample count and seed; the comparison is paired.

A question is dropped when its source message is no longer eligible (as consent-prune does, D101)
or none of its gold messages has a searchable embedding; otherwise its gold is cut to the
searchable ones.

Predictions go to <directory outside the repo>/retrieval/<method>.json, mode 0600, written
atomically: IDs, a hash of each question and seconds. Never question or message text.
"""

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Container, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO

from gcwrapped.labeling.files import MESSAGE_ID, QUESTION_ID
from gcwrapped.labeling.schema import QuestionLabel
from gcwrapped.retrieval.search import Hit
from gcwrapped.tagging import evaluation as tag_evaluation
from gcwrapped.tagging import scoring
from gcwrapped.tagging.runner import Timings

VECTOR = "vector"
HYBRID = "hybrid"
METHODS = (VECTOR, HYBRID)
RECALL_AT = (1, 5, 10)
DEPTH = 10  # results kept per question
COMPARE_AT = 5  # D104: the win rule is on recall@5
LATENCY_LIMIT_SECONDS = 1.0  # D104: hybrid's p95 must be under this
PREDICTIONS_DIR = "retrieval"
SOURCES = ("synthetic", "real")
_FLOAT_NOISE_PLACES = 9  # a lower bound that is 0 up to float noise does not lie above 0
_VERSION = re.compile(r"[0-9a-f]{12}")


class RetrievalEvalError(Exception):
    """Base of retrieval eval failures. Only the class name is ever printed."""


class UnknownMethodError(RetrievalEvalError):
    """A method name is not vector or hybrid, or compare was not asked for A=vector B=hybrid."""


class NoScorableQuestionsError(RetrievalEvalError):
    """No question has an eligible source and an embedded gold message."""


class RetrievalPredictionsError(RetrievalEvalError):
    """A predictions file is missing or malformed."""


class EmbedVersionMismatch(RetrievalEvalError):
    """The two predictions files were made with different embed versions."""


class QuestionSetMismatch(RetrievalEvalError):
    """The two predictions files were not made on the same questions and gold."""


@dataclass(frozen=True)
class Row:
    question_id: str
    question_sha256: str
    source_message_id: str
    gold_message_ids: tuple[str, ...]
    retrieved_message_ids: tuple[str, ...]  # best first, at most DEPTH
    seconds: float  # query embedding plus SQL

    @property
    def referenced_ids(self) -> frozenset[str]:
        return frozenset(
            (self.source_message_id, *self.gold_message_ids, *self.retrieved_message_ids)
        )


@dataclass(frozen=True)
class Predictions:
    method: str
    embed_version: str
    source: str  # "synthetic" or "real" questions
    rows: tuple[Row, ...]


def split_questions(
    questions: Sequence[QuestionLabel], eligible: Container[str], searchable: Container[str]
) -> tuple[list[QuestionLabel], int]:
    """(the questions that can be scored, with their gold cut to searchable IDs; dropped count)."""
    kept: list[QuestionLabel] = []
    for question in questions:
        gold = tuple(g for g in question.gold_message_ids if g in searchable)
        if gold and question.source_message_id in eligible:
            kept.append(
                QuestionLabel(
                    question.question_id,
                    question.question,
                    gold,
                    question.source_message_id,
                    question.labeled_at,
                )
            )
    return kept, len(questions) - len(kept)


def run_method(
    search: Callable[[str, int], list[Hit]],
    questions: Sequence[QuestionLabel],
    clock: Callable[[], float] = time.perf_counter,
) -> list[Row]:
    """Search each question once and time it. A failure propagates: the run is void."""
    rows: list[Row] = []
    for question in questions:
        began = clock()
        hits = search(question.question, DEPTH)
        seconds = clock() - began
        rows.append(
            Row(
                question_id=question.question_id,
                question_sha256=hashlib.sha256(question.question.encode("utf-8")).hexdigest(),
                source_message_id=question.source_message_id,
                gold_message_ids=question.gold_message_ids,
                retrieved_message_ids=tuple(h.message_id for h in hits[:DEPTH]),
                seconds=seconds,
            )
        )
    return rows


# --- scoring -----------------------------------------------------------------------------------


def first_hit_rank(row: Row) -> int | None:
    """The rank (from 1) of the first gold message in the results, or None."""
    gold = set(row.gold_message_ids)
    return next((n for n, m in enumerate(row.retrieved_message_ids, start=1) if m in gold), None)


def hit_at(row: Row, k: int) -> float:
    rank = first_hit_rank(row)
    return 1.0 if rank is not None and rank <= k else 0.0


def reciprocal_rank(row: Row, depth: int = DEPTH) -> float:
    rank = first_hit_rank(row)
    return 1 / rank if rank is not None and rank <= depth else 0.0


@dataclass(frozen=True)
class Estimate:
    value: float
    interval: tuple[float, float]


def estimate(values: Sequence[float]) -> Estimate:
    """The mean of per-question values and its bootstrap interval (needs at least one)."""

    def mean(indices: Sequence[int]) -> float:
        return sum(values[i] for i in indices) / len(indices)

    return Estimate(mean(range(len(values))), scoring.bootstrap_interval(mean, len(values)))


@dataclass(frozen=True)
class EvalResult:
    questions: int
    dropped: int
    scored: int
    recall: dict[int, Estimate]
    mrr: Estimate
    p50: float
    p95: float


def percentile(rows: Sequence[Row], percent: int) -> float:
    value = Timings([r.seconds for r in rows]).percentile(percent)
    if value is None:
        raise NoScorableQuestionsError
    return value


def score(rows: Sequence[Row], questions: int) -> EvalResult:
    """Score one method's rows; `questions` is the size of the question file before drops."""
    if not rows:
        raise NoScorableQuestionsError
    return EvalResult(
        questions=questions,
        dropped=questions - len(rows),
        scored=len(rows),
        recall={k: estimate([hit_at(r, k) for r in rows]) for k in RECALL_AT},
        mrr=estimate([reciprocal_rank(r) for r in rows]),
        p50=percentile(rows, 50),
        p95=percentile(rows, 95),
    )


# --- the comparison (D104) ---------------------------------------------------------------------


@dataclass(frozen=True)
class CompareResult:
    scored: int
    a_recall: float
    b_recall: float
    difference: Estimate  # B minus A, paired
    b_p95: float

    @property
    def latency_ok(self) -> bool:
        return self.b_p95 < LATENCY_LIMIT_SECONDS

    @property
    def b_wins(self) -> bool:
        """D104, fixed before any real run: the whole paired interval above 0, and latency."""
        return round(self.difference.interval[0], _FLOAT_NOISE_PLACES) > 0 and self.latency_ok


def _question_set(predictions: Predictions) -> dict[str, tuple[str, str, tuple[str, ...]]]:
    return {
        r.question_id: (r.question_sha256, r.source_message_id, tuple(sorted(r.gold_message_ids)))
        for r in predictions.rows
    }


def compare(a: Predictions, b: Predictions, eligible: Container[str]) -> CompareResult:
    """A (vector) against B (hybrid) on the same questions. A question whose row, in either file,
    refers to a message that is no longer eligible is not scored (D87, D105)."""
    if a.embed_version != b.embed_version:
        raise EmbedVersionMismatch
    if a.source != b.source or _question_set(a) != _question_set(b):
        raise QuestionSetMismatch
    by_id = {r.question_id: r for r in b.rows}
    pairs = [
        (row, by_id[row.question_id])
        for row in a.rows
        if all(m in eligible for m in row.referenced_ids | by_id[row.question_id].referenced_ids)
    ]
    if not pairs:
        raise NoScorableQuestionsError
    hits_a = [hit_at(x, COMPARE_AT) for x, _ in pairs]
    hits_b = [hit_at(y, COMPARE_AT) for _, y in pairs]
    differences = [y - x for x, y in zip(hits_a, hits_b, strict=True)]
    return CompareResult(
        scored=len(pairs),
        a_recall=sum(hits_a) / len(pairs),
        b_recall=sum(hits_b) / len(pairs),
        difference=estimate(differences),  # resampling differences is the paired bootstrap
        b_p95=percentile([y for _, y in pairs], 95),
    )


# --- private files -----------------------------------------------------------------------------


def predictions_path(root: Path, method: str) -> Path:
    if method not in METHODS:
        raise UnknownMethodError
    return root / PREDICTIONS_DIR / f"{method}.json"


def write_predictions(root: Path, predictions: Predictions) -> None:
    folder = tag_evaluation.private_subdir(root, PREDICTIONS_DIR)
    path = predictions_path(folder.parent, predictions.method)

    def write(handle: TextIO) -> None:
        handle.write(json.dumps(asdict(predictions), indent=1) + "\n")

    tag_evaluation._write_private(path, write)


def _row(data: dict[str, object]) -> Row:
    gold, retrieved = data["gold_message_ids"], data["retrieved_message_ids"]
    seconds = data["seconds"]
    if not isinstance(gold, list) or not isinstance(retrieved, list):
        raise ValueError
    if not isinstance(seconds, int | float) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError
    row = Row(
        question_id=str(data["question_id"]),
        question_sha256=str(data["question_sha256"]),
        source_message_id=str(data["source_message_id"]),
        gold_message_ids=tuple(str(m) for m in gold),
        retrieved_message_ids=tuple(str(m) for m in retrieved),
        seconds=float(seconds),
    )
    if (
        QUESTION_ID.fullmatch(row.question_id) is None
        or MESSAGE_ID.fullmatch(row.question_sha256) is None  # also a sha256
        or not row.gold_message_ids
        or len(row.retrieved_message_ids) > DEPTH
        or not all(MESSAGE_ID.fullmatch(m) for m in row.referenced_ids)
    ):
        raise ValueError
    return row


def read_predictions(root: Path, method: str) -> Predictions:
    path = predictions_path(root, method)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = tuple(_row(r) for r in data["rows"])
        found = Predictions(
            str(data["method"]), str(data["embed_version"]), str(data["source"]), rows
        )
        if (
            found.method != method
            or found.source not in SOURCES
            or _VERSION.fullmatch(found.embed_version) is None
            or len({r.question_id for r in rows}) != len(rows)
        ):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise RetrievalPredictionsError from None
    return found
