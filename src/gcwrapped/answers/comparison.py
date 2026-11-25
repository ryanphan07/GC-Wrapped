"""The pre-registered answer prompt comparison and the default variant (D121). Nothing here prints.

A is the baseline prompt and B the variant, each from its stored answer-eval run of one model. The
two runs must cover the same corpus, retrieval method and questions, so each question is a pair.
B wins only if all three conditions hold, fixed before any real run: the whole 95% paired bootstrap
interval of (B - A) citation hit rate lies above 0, B's invalid plus timed out rate is at most 2
percentage points above A's, and B's p95 seconds per question is at most 1.25 times A's. The
interval is the retrieval comparison's: same resample count and seed.

A verdict goes to <directory outside the repo>/experiments/, mode 0600: numbers, variant names,
prompt versions, the model id, the embed version and the corpus hash. The default variant changes
only through `make answer-promote`.
"""

import json
import re
from collections.abc import Container
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import psycopg

from gcwrapped.answers.evaluation import Results, Row
from gcwrapped.answers.pipeline import INVALID, TIMED_OUT
from gcwrapped.answers.prompt import VARIANT_A, VARIANT_B, VARIANTS
from gcwrapped.retrieval import evaluation as retrieval
from gcwrapped.tagging import evaluation as tag_evaluation
from gcwrapped.tagging.runner import Timings

DEFAULT_TABLE = "enrich.answer_prompt_default"
DEFAULT_VARIANT = VARIANT_A  # the D121 baseline, until one is promoted
FAILURE_MARGIN_POINTS = 2  # B's invalid plus timed out rate may be at most this far above A's
LATENCY_RATIO = 1.25  # B's p95 seconds may be at most this multiple of A's
_FLOAT_NOISE_PLACES = 9
_HASH = re.compile(r"[0-9a-f]{12}")  # a prompt version or an embed version
_MODEL_ID = re.compile(r"[A-Za-z0-9._-]+")
_VERDICT_FILE = re.compile(r"answer-compare-\d{8}T\d{12}Z\.json")


class MethodMismatch(retrieval.RetrievalEvalError):
    """The two answer runs used different retrieval methods."""


class ModelMismatch(retrieval.RetrievalEvalError):
    """The latest verdict was made with a model other than the current one."""


class PromptVersionMismatch(retrieval.RetrievalEvalError):
    """The latest verdict was made with other prompt versions than the code's."""


@dataclass(frozen=True)
class CompareResult:
    scored: int
    a_hit_rate: float
    b_hit_rate: float
    difference: retrieval.Estimate  # B minus A, paired
    a_failures: int  # invalid or timed out
    b_failures: int
    a_p95: float
    b_p95: float

    @property
    def hit_ok(self) -> bool:
        return round(self.difference.interval[0], _FLOAT_NOISE_PLACES) > 0

    @property
    def failures_ok(self) -> bool:
        # In whole numbers: exactly 2 points above must pass, whatever the float division gives.
        return (self.b_failures - self.a_failures) * 100 <= FAILURE_MARGIN_POINTS * self.scored

    @property
    def latency_ok(self) -> bool:
        return round(self.b_p95 - LATENCY_RATIO * self.a_p95, _FLOAT_NOISE_PLACES) <= 0

    @property
    def winner(self) -> str:
        return VARIANT_B if self.hit_ok and self.failures_ok and self.latency_ok else VARIANT_A


def _question_set(results: Results) -> dict[str, tuple[str, str, tuple[str, ...]]]:
    return {
        r.question_id: (r.question_sha256, r.source_message_id, tuple(sorted(r.gold_message_ids)))
        for r in results.rows
    }


def _failures(rows: list[Row]) -> int:
    return sum(1 for r in rows if r.outcome in (INVALID, TIMED_OUT))


def _p95(rows: list[Row]) -> float:
    value = Timings([r.seconds for r in rows]).percentile(95)
    if value is None:
        raise retrieval.NoScorableQuestionsError
    return value


def compare(a: Results, b: Results, eligible: Container[str]) -> CompareResult:
    """The A run against the B run on the same questions. A question whose row, in either run,
    refers to a message that is no longer eligible is not scored (D87, as in retrieval-compare)."""
    if a.embed_version != b.embed_version:
        raise retrieval.EmbedVersionMismatch
    if a.corpus_hash != b.corpus_hash:
        raise retrieval.CorpusMismatch
    if a.method != b.method:
        raise MethodMismatch
    if _question_set(a) != _question_set(b):
        raise retrieval.QuestionSetMismatch
    by_id = {r.question_id: r for r in b.rows}
    pairs = [
        (row, by_id[row.question_id])
        for row in a.rows
        if all(m in eligible for m in row.referenced_ids | by_id[row.question_id].referenced_ids)
    ]
    if not pairs:
        raise retrieval.NoScorableQuestionsError
    rows_a = [x for x, _ in pairs]
    rows_b = [y for _, y in pairs]
    differences = [float(y.citation_hit) - float(x.citation_hit) for x, y in pairs]
    return CompareResult(
        scored=len(pairs),
        a_hit_rate=sum(r.citation_hit for r in rows_a) / len(pairs),
        b_hit_rate=sum(r.citation_hit for r in rows_b) / len(pairs),
        difference=retrieval.estimate(differences),  # resampling differences is the paired one
        a_failures=_failures(rows_a),
        b_failures=_failures(rows_b),
        a_p95=_p95(rows_a),
        b_p95=_p95(rows_b),
    )


# --- verdicts and the default variant ------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """What a stored verdict says it decided, and what it was made with."""

    winner: str
    model_id: str
    a_prompt_version: str
    b_prompt_version: str
    embed_version: str


def write_verdict(
    root: Path,
    model_id: str,
    a_version: str,
    b_version: str,
    results: Results,
    outcome: CompareResult,
) -> None:
    """Record a verdict in root/experiments/. `results` is either of the compared runs (compare has
    checked that embed version and corpus are the same in both)."""
    folder = tag_evaluation.private_subdir(root, tag_evaluation.EXPERIMENTS_DIR)
    now = datetime.now(UTC)
    path = folder / f"answer-compare-{now:%Y%m%dT%H%M%S%f}Z.json"
    body = {
        "a": VARIANT_A,
        "b": VARIANT_B,
        "winner": outcome.winner,
        "model_id": model_id,
        "a_prompt_version": a_version,
        "b_prompt_version": b_version,
        "embed_version": results.embed_version,
        "corpus_hash": results.corpus_hash,
        "questions_scored": outcome.scored,
        "a_citation_hit_rate": outcome.a_hit_rate,
        "b_citation_hit_rate": outcome.b_hit_rate,
        "difference": outcome.difference.value,
        "difference_interval": list(outcome.difference.interval),
        "a_failures": outcome.a_failures,
        "b_failures": outcome.b_failures,
        "a_p95_seconds": outcome.a_p95,
        "b_p95_seconds": outcome.b_p95,
    }

    def write(handle: TextIO) -> None:
        handle.write(json.dumps(body, indent=2) + "\n")

    tag_evaluation.write_private(path, write)


def latest_verdict(root: Path) -> Verdict:
    """The most recent answer-compare verdict."""
    folder = root / tag_evaluation.EXPERIMENTS_DIR
    found = folder.glob("answer-compare-*.json") if folder.is_dir() else []
    names = sorted(p.name for p in found if _VERDICT_FILE.fullmatch(p.name))
    if not names:
        raise retrieval.VerdictError
    try:
        body = json.loads((folder / names[-1]).read_text(encoding="utf-8"))
        a, b = body["a"], body["b"]
        verdict = Verdict(
            str(body["winner"]),
            str(body["model_id"]),
            str(body["a_prompt_version"]),
            str(body["b_prompt_version"]),
            str(body["embed_version"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        raise retrieval.VerdictError from None
    versions = (verdict.a_prompt_version, verdict.b_prompt_version, verdict.embed_version)
    if (
        (a, b) != VARIANTS
        or verdict.winner not in VARIANTS
        or _MODEL_ID.fullmatch(verdict.model_id) is None
        or not all(_HASH.fullmatch(v) for v in versions)
    ):
        raise retrieval.VerdictError
    return verdict


def default_variant(conn: psycopg.Connection) -> str:
    """The variant answers use: enrich.answer_prompt_default, or "a" while the table or its row
    does not exist."""
    if conn.execute("SELECT to_regclass(%s)", (DEFAULT_TABLE,)).fetchone() == (None,):
        return DEFAULT_VARIANT
    row = conn.execute(f"SELECT variant FROM {DEFAULT_TABLE}").fetchone()
    return DEFAULT_VARIANT if row is None else str(row[0])


def promoted_variant(url: str) -> str:
    with psycopg.connect(url) as conn:
        return default_variant(conn)


def promote(url: str, variant: str) -> str | None:
    """Set the one row of enrich.answer_prompt_default. Returns the previous variant, if there
    was one."""
    with psycopg.connect(url) as conn:
        row = conn.execute(f"SELECT variant FROM {DEFAULT_TABLE}").fetchone()
        conn.execute(
            f"INSERT INTO {DEFAULT_TABLE} (singleton, variant) VALUES (true, %s) "
            "ON CONFLICT (singleton) DO UPDATE SET variant = excluded.variant, "
            "promoted_at = now()",
            (variant,),
        )
    return None if row is None else str(row[0])
