"""The tagging eval (D92, D93): run a model over hand labels, store predictions, score, compare.

Nothing here prints. `report` renders fixed lines from the dataclasses below.

Files live under a directory outside the repo (Ryan's GCW_LABEL_DIR, or a temporary directory for
synthetic runs), mode 0600 and written atomically:
  predictions/<model_id>__<prompt_version>.csv   message_id, topic, sentiment, seconds (no text)
  experiments/compare-<UTC timestamp>.json       one tag-compare verdict: ids, counts, numbers
A failed message has empty topic and sentiment and counts as wrong when scored (D92).

Labels whose message is not local_ok at scoring time are dropped (D87): `split_labels` keeps only
labeled messages present in the eligible index, so revoked consent removes a message from scoring
even though its old label row and prediction row may still exist.
"""

import csv
import json
import math
import os
import re
import tempfile
import time
from collections.abc import Callable, Container, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import psycopg

from gcwrapped.labeling.schema import SENTIMENTS, TOPICS
from gcwrapped.labeling.source import EligibleIndex
from gcwrapped.ledger import REPO_ROOT
from gcwrapped.tagging import scoring
from gcwrapped.tagging.provider import TagFailure, TagProvider
from gcwrapped.tagging.runner import tag_message

PREDICTIONS_DIR = "predictions"
EXPERIMENTS_DIR = "experiments"
PREDICTION_HEADER = ("message_id", "topic", "sentiment", "seconds")
_MESSAGE_ID = re.compile(r"[0-9a-f]{64}")
_MODEL_ID = re.compile(r"[A-Za-z0-9._-]+")
_VERDICT_FILE = re.compile(r"compare-\d{8}T\d{12}Z\.json")
DEFAULT_TABLE = "enrich.tag_default"

Labels = Mapping[str, tuple[str, str]]  # message_id -> (topic, sentiment)


class EvalError(Exception):
    """Base of eval failures. Only the class name is ever printed."""


class NoScorableLabelsError(EvalError):
    """No labeled message is both local-eligible now and predicted."""


class OutsideRepoError(EvalError):
    """A directory for predictions or verdicts is inside the repository."""


class PredictionsError(EvalError):
    """A predictions file is missing or malformed."""


class VerdictError(EvalError):
    """No tag-compare verdict exists, or the latest one is malformed."""


class NotWinnerError(EvalError):
    """The latest verdict does not name the requested model as the winner."""


@dataclass(frozen=True)
class PredictionRow:
    message_id: str
    topic: str | None  # None with sentiment None: the model failed on this message
    sentiment: str | None
    seconds: float


# --- private files -----------------------------------------------------------------------------


def private_subdir(root: Path, name: str) -> Path:
    """`root/name`, created 0700, refusing a location that resolves inside the repo."""
    target = Path(os.path.realpath(root / name))
    if target == REPO_ROOT or REPO_ROOT in target.parents:
        raise OutsideRepoError
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    return target


def write_private(path: Path, write: Callable[[TextIO], None]) -> None:
    """Atomically replace `path`; mkstemp creates the file 0600."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".eval-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            write(handle)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- predictions -------------------------------------------------------------------------------


def run_model(
    provider: TagProvider, index: EligibleIndex, message_ids: Sequence[str]
) -> list[PredictionRow]:
    """Tag each message with the production prompt and client; time each one. Stores nothing.

    A TagFailure becomes a row with no labels. ProviderUnavailable propagates: the run is void.
    """
    rows: list[PredictionRow] = []
    for message_id in message_ids:
        began = time.perf_counter()
        try:
            tag = tag_message(provider, index, index.get(message_id))
            topic: str | None = tag.topic
            sentiment: str | None = tag.sentiment
        except TagFailure:
            topic = sentiment = None
        rows.append(PredictionRow(message_id, topic, sentiment, time.perf_counter() - began))
    return rows


def predictions_path(directory: Path, model_id: str, prompt_version: str) -> Path:
    if _MODEL_ID.fullmatch(model_id) is None:
        raise PredictionsError
    return directory / PREDICTIONS_DIR / f"{model_id}__{prompt_version}.csv"


def write_predictions(
    root: Path, model_id: str, prompt_version: str, rows: Sequence[PredictionRow]
) -> None:
    folder = private_subdir(root, PREDICTIONS_DIR)
    path = predictions_path(folder.parent, model_id, prompt_version)

    def write(handle: TextIO) -> None:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(PREDICTION_HEADER)
        for r in rows:
            writer.writerow([r.message_id, r.topic or "", r.sentiment or "", repr(r.seconds)])

    write_private(path, write)


def read_predictions(root: Path, model_id: str, prompt_version: str) -> dict[str, PredictionRow]:
    path = predictions_path(root, model_id, prompt_version)
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            if tuple(next(reader, ())) != PREDICTION_HEADER:
                raise PredictionsError
            found: dict[str, PredictionRow] = {}
            for cells in reader:
                if len(cells) != len(PREDICTION_HEADER):
                    raise PredictionsError
                message_id, topic, sentiment, seconds = cells
                value = float(seconds)
                failed = topic == "" and sentiment == ""
                valid = failed or (topic in TOPICS and sentiment in SENTIMENTS)
                if (
                    _MESSAGE_ID.fullmatch(message_id) is None
                    or not valid
                    or message_id in found
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise PredictionsError
                found[message_id] = PredictionRow(
                    message_id, topic or None, sentiment or None, value
                )
    except (OSError, ValueError, csv.Error):
        raise PredictionsError from None
    return found


# --- scoring -----------------------------------------------------------------------------------


def split_labels(labels: Labels, eligible: Container[str]) -> tuple[list[str], int]:
    """(labeled message IDs still local-eligible, in file order; how many were dropped)."""
    kept = [m for m in labels if m in eligible]
    return kept, len(labels) - len(kept)


@dataclass(frozen=True)
class Metric:
    accuracy: float
    accuracy_interval: tuple[float, float]
    macro_f1: float
    macro_f1_interval: tuple[float, float]
    class_f1: Mapping[str, float]
    baseline: float  # accuracy of always predicting the most common gold label
    confusion: Mapping[str, Mapping[str, int]]  # gold -> predicted (or "failed") -> count


@dataclass(frozen=True)
class EvalResult:
    labels: int
    dropped: int
    scored: int
    failures: int
    topic: Metric
    sentiment: Metric
    seconds: Sequence[float]


def _metric(gold: Sequence[str], pred: scoring.Predictions) -> Metric:
    n = len(gold)
    return Metric(
        accuracy=scoring.accuracy(gold, pred),
        accuracy_interval=scoring.bootstrap_interval(lambda i: scoring.accuracy(gold, pred, i), n),
        macro_f1=scoring.macro_f1(gold, pred),
        macro_f1_interval=scoring.bootstrap_interval(lambda i: scoring.macro_f1(gold, pred, i), n),
        class_f1=scoring.class_f1(gold, pred),
        baseline=scoring.majority_baseline(gold),
        confusion=scoring.confusion(gold, pred),
    )


def evaluate(
    labels: Labels, rows: Mapping[str, PredictionRow], eligible: Container[str]
) -> EvalResult:
    """Score one model's predictions against the labels that are still local-eligible."""
    kept, dropped = split_labels(labels, eligible)
    ids = [m for m in kept if m in rows]
    if not ids:
        raise NoScorableLabelsError
    gold_topic = [labels[m][0] for m in ids]
    gold_sentiment = [labels[m][1] for m in ids]
    chosen = [rows[m] for m in ids]
    return EvalResult(
        labels=len(labels),
        dropped=dropped,
        scored=len(ids),
        failures=sum(1 for r in chosen if r.topic is None),
        topic=_metric(gold_topic, [r.topic for r in chosen]),
        sentiment=_metric(gold_sentiment, [r.sentiment for r in chosen]),
        seconds=[r.seconds for r in chosen],
    )


# --- comparison (D93) --------------------------------------------------------------------------


@dataclass(frozen=True)
class Difference:
    a: float
    b: float
    difference: float
    interval: tuple[float, float]


@dataclass(frozen=True)
class CompareResult:
    messages: int
    topic: Difference
    sentiment: Difference
    a_mps: float
    b_mps: float
    verdict: scoring.Verdict

    @property
    def speedup(self) -> float | None:
        return self.b_mps / self.a_mps if self.a_mps > 0 else None


def _difference(gold: Sequence[str], a: scoring.Predictions, b: scoring.Predictions) -> Difference:
    acc_a, acc_b = scoring.accuracy(gold, a), scoring.accuracy(gold, b)
    interval = scoring.bootstrap_interval(
        lambda i: scoring.accuracy(gold, b, i) - scoring.accuracy(gold, a, i), len(gold)
    )
    return Difference(acc_a, acc_b, acc_b - acc_a, interval)


def compare(
    labels: Labels,
    rows_a: Mapping[str, PredictionRow],
    rows_b: Mapping[str, PredictionRow],
    eligible: Container[str],
) -> CompareResult:
    """Both models on the same messages: labeled, still local-eligible and predicted by both."""
    kept, _ = split_labels(labels, eligible)
    ids = [m for m in kept if m in rows_a and m in rows_b]
    if not ids:
        raise NoScorableLabelsError
    a = [rows_a[m] for m in ids]
    b = [rows_b[m] for m in ids]
    topic = _difference([labels[m][0] for m in ids], [r.topic for r in a], [r.topic for r in b])
    sentiment = _difference(
        [labels[m][1] for m in ids], [r.sentiment for r in a], [r.sentiment for r in b]
    )
    a_mps = scoring.messages_per_second([r.seconds for r in a])
    b_mps = scoring.messages_per_second([r.seconds for r in b])
    verdict = scoring.decide(a_mps, b_mps, topic.interval[0], sentiment.interval[0])
    return CompareResult(len(ids), topic, sentiment, a_mps, b_mps, verdict)


def write_verdict(
    root: Path, a_id: str, b_id: str, prompt_version: str, result: CompareResult
) -> None:
    """Record the verdict in root/experiments/. Numbers and model ids only; mode 0600."""
    folder = private_subdir(root, EXPERIMENTS_DIR)
    now = datetime.now(UTC)
    path = folder / f"compare-{now:%Y%m%dT%H%M%S%f}Z.json"
    body = {
        "a": a_id,
        "b": b_id,
        "winner": b_id if result.verdict.b_wins else a_id,
        "prompt_version": prompt_version,
        "messages": result.messages,
        "created_at": now.isoformat(),
        "a_messages_per_second": result.a_mps,
        "b_messages_per_second": result.b_mps,
        "topic_difference_interval": list(result.topic.interval),
        "sentiment_difference_interval": list(result.sentiment.interval),
    }

    def write(handle: TextIO) -> None:
        handle.write(json.dumps(body, indent=2) + "\n")

    write_private(path, write)


def latest_winner(root: Path) -> str:
    """The winner named by the most recent tag-compare verdict in root/experiments/."""
    folder = root / EXPERIMENTS_DIR
    names = sorted(p.name for p in folder.glob("compare-*.json")) if folder.is_dir() else []
    names = [n for n in names if _VERDICT_FILE.fullmatch(n)]
    if not names:
        raise VerdictError
    try:
        body = json.loads((folder / names[-1]).read_text(encoding="utf-8"))
        winner, a, b = body["winner"], body["a"], body["b"]
    except (OSError, ValueError, KeyError, TypeError):
        raise VerdictError from None
    ids = (winner, a, b)
    if not all(isinstance(i, str) and _MODEL_ID.fullmatch(i) for i in ids) or winner not in (a, b):
        raise VerdictError
    return str(winner)


# --- the promoted model (D92) ------------------------------------------------------------------


def promoted_model(url: str, fallback: str) -> str:
    """enrich.tag_default's model, or `fallback` when the table or its row does not exist yet."""
    with psycopg.connect(url) as conn:
        if conn.execute("SELECT to_regclass(%s)", (DEFAULT_TABLE,)).fetchone() == (None,):
            return fallback
        row = conn.execute(f"SELECT model_id FROM {DEFAULT_TABLE}").fetchone()
    return fallback if row is None else str(row[0])


def promote(url: str, model_id: str) -> str | None:
    """Set the one row of enrich.tag_default. Returns the previous model id, if there was one."""
    with psycopg.connect(url) as conn:
        row = conn.execute(f"SELECT model_id FROM {DEFAULT_TABLE}").fetchone()
        conn.execute(
            f"INSERT INTO {DEFAULT_TABLE} (singleton, model_id) VALUES (true, %s) "
            "ON CONFLICT (singleton) DO UPDATE SET model_id = excluded.model_id, "
            "promoted_at = now()",
            (model_id,),
        )
    return None if row is None else str(row[0])
