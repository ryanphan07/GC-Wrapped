"""The automatic answer eval (D112). Nothing here prints.

Each scorable question (retrieval-eval's drop rules, over a complete corpus) is answered once.
"Context has gold" means a gold message was among the numbered messages the model was shown;
"citation hit" means it answered and cited at least one. Together they separate a retrieval miss
from a generation miss. Neither says the answer is correct.

Results go to <directory outside the repo>/answers/<model id>__<answer prompt version>.json, mode
0600, written atomically: question ID and hash, message IDs, outcome, seconds, token counts
(D116), embed version, corpus hash and retrieval method. The generated text is never stored.
"""

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO

from gcwrapped.answers.pipeline import (
    ABSTAINED,
    ANSWERED,
    INVALID,
    OUTCOMES,
    TIMED_OUT,
    Answer,
)
from gcwrapped.answers.prompt import MAX_CITATIONS
from gcwrapped.labeling.files import MESSAGE_ID, QUESTION_ID
from gcwrapped.labeling.schema import QuestionLabel
from gcwrapped.retrieval import evaluation as retrieval
from gcwrapped.tagging import evaluation as tag_evaluation
from gcwrapped.tagging.runner import Timings

RESULTS_DIR = "answers"
_MODEL_ID = re.compile(r"[A-Za-z0-9._-]+")
_VERSION = re.compile(r"[0-9a-f]{12}")


class AnswerResultsError(Exception):
    """An answers file is missing or malformed. Only the class name is ever printed."""


@dataclass(frozen=True)
class Row:
    question_id: str
    question_sha256: str
    source_message_id: str
    gold_message_ids: tuple[str, ...]
    numbered_message_ids: tuple[str, ...]  # what the model was shown, in number order
    cited_message_ids: tuple[str, ...]
    outcome: str
    seconds: float  # query embedding, SQL and generation
    prompt_tokens: int | None  # the server's counts; None if it reported none (D116)
    completion_tokens: int | None

    @property
    def referenced_ids(self) -> frozenset[str]:
        return frozenset(
            (
                self.source_message_id,
                *self.gold_message_ids,
                *self.numbered_message_ids,
                *self.cited_message_ids,
            )
        )

    @property
    def context_has_gold(self) -> bool:
        return bool(set(self.gold_message_ids) & set(self.numbered_message_ids))

    @property
    def citation_hit(self) -> bool:
        return self.outcome == ANSWERED and bool(
            set(self.gold_message_ids) & set(self.cited_message_ids)
        )


@dataclass(frozen=True)
class Results:
    embed_version: str
    corpus_hash: str
    method: str
    rows: tuple[Row, ...]


def run_questions(
    answer: Callable[[str], Answer],
    questions: Sequence[QuestionLabel],
    clock: Callable[[], float] = time.perf_counter,
) -> list[Row]:
    """Answer each question once and time it. A propagated failure voids the run."""
    rows: list[Row] = []
    for question in questions:
        began = clock()
        found = answer(question.question)
        seconds = clock() - began
        rows.append(
            Row(
                question_id=question.question_id,
                question_sha256=hashlib.sha256(question.question.encode("utf-8")).hexdigest(),
                source_message_id=question.source_message_id,
                gold_message_ids=question.gold_message_ids,
                numbered_message_ids=tuple(s.message.message_id for s in found.sources),
                cited_message_ids=tuple(s.message.message_id for s in found.cited),
                outcome=found.outcome,
                seconds=seconds,
                prompt_tokens=found.prompt_tokens,
                completion_tokens=found.completion_tokens,
            )
        )
    return rows


@dataclass(frozen=True)
class Rate:
    count: int
    estimate: retrieval.Estimate  # the share of scored questions, with its Wilson interval


@dataclass(frozen=True)
class EvalResult:
    questions: int
    dropped: int
    scored: int
    invalid: Rate
    timed_out: Rate
    answered: Rate
    abstained: Rate
    context_has_gold: Rate
    citation_hit: Rate
    p50: float
    p95: float
    prompt_tokens_p50: int | None  # over the rows with a count; None if no row has one
    prompt_tokens_p95: int | None
    prompt_tokens_max: int | None


def _rate(flags: Sequence[bool]) -> Rate:
    return Rate(sum(flags), retrieval.rate(flags))


def score(rows: Sequence[Row], questions: int) -> EvalResult:
    """Score a run's rows; `questions` is the size of the question file before drops."""
    timings = Timings([r.seconds for r in rows])
    p50, p95 = timings.percentile(50), timings.percentile(95)
    if p50 is None or p95 is None:
        raise retrieval.NoScorableQuestionsError
    tokens = Timings([r.prompt_tokens for r in rows if r.prompt_tokens is not None])
    p50_tokens, p95_tokens, max_tokens = (tokens.percentile(p) for p in (50, 95, 100))
    return EvalResult(
        questions=questions,
        dropped=questions - len(rows),
        scored=len(rows),
        invalid=_rate([r.outcome == INVALID for r in rows]),
        timed_out=_rate([r.outcome == TIMED_OUT for r in rows]),
        answered=_rate([r.outcome == ANSWERED for r in rows]),
        abstained=_rate([r.outcome == ABSTAINED for r in rows]),
        context_has_gold=_rate([r.context_has_gold for r in rows]),
        citation_hit=_rate([r.citation_hit for r in rows]),
        p50=p50,
        p95=p95,
        # nearest rank returns one of the values, so these are whole numbers
        prompt_tokens_p50=None if p50_tokens is None else int(p50_tokens),
        prompt_tokens_p95=None if p95_tokens is None else int(p95_tokens),
        prompt_tokens_max=None if max_tokens is None else int(max_tokens),
    )


# --- private files -----------------------------------------------------------------------------


def check_file_key(model_id: str, prompt_version: str) -> None:
    """The two parts of an answers or grades file name, which must not be able to leave its
    directory."""
    if _MODEL_ID.fullmatch(model_id) is None or _VERSION.fullmatch(prompt_version) is None:
        raise AnswerResultsError


def results_path(root: Path, model_id: str, prompt_version: str) -> Path:
    check_file_key(model_id, prompt_version)
    return root / RESULTS_DIR / f"{model_id}__{prompt_version}.json"


def write_results(root: Path, model_id: str, prompt_version: str, results: Results) -> None:
    folder = tag_evaluation.private_subdir(root, RESULTS_DIR)
    path = results_path(folder.parent, model_id, prompt_version)

    def write(handle: TextIO) -> None:
        handle.write(json.dumps(asdict(results), indent=1) + "\n")

    tag_evaluation.write_private(path, write)


def _ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError
    return tuple(str(m) for m in value)


def _count(value: object) -> int | None:
    if value is None or (type(value) is int and value >= 0):
        return value
    raise ValueError


def _row(data: dict[str, object]) -> Row:
    seconds = data["seconds"]
    if not isinstance(seconds, int | float) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError
    row = Row(
        question_id=str(data["question_id"]),
        question_sha256=str(data["question_sha256"]),
        source_message_id=str(data["source_message_id"]),
        gold_message_ids=_ids(data["gold_message_ids"]),
        numbered_message_ids=_ids(data["numbered_message_ids"]),
        cited_message_ids=_ids(data["cited_message_ids"]),
        outcome=str(data["outcome"]),
        seconds=float(seconds),
        prompt_tokens=_count(data["prompt_tokens"]),
        completion_tokens=_count(data["completion_tokens"]),
    )
    if (
        QUESTION_ID.fullmatch(row.question_id) is None
        or MESSAGE_ID.fullmatch(row.question_sha256) is None  # also a sha256
        or not row.gold_message_ids
        or row.outcome not in OUTCOMES
        # a request that timed out got no response, so no counts
        or (row.outcome == TIMED_OUT and (row.prompt_tokens, row.completion_tokens) != (None, None))
        or len(row.cited_message_ids) > MAX_CITATIONS
        or bool(row.cited_message_ids) != (row.outcome == ANSWERED)
        or not set(row.cited_message_ids) <= set(row.numbered_message_ids)
        or not all(MESSAGE_ID.fullmatch(m) for m in row.referenced_ids)
    ):
        raise ValueError
    return row


def read_results(root: Path, model_id: str, prompt_version: str) -> Results:
    path = results_path(root, model_id, prompt_version)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = tuple(_row(r) for r in data["rows"])
        found = Results(
            str(data["embed_version"]), str(data["corpus_hash"]), str(data["method"]), rows
        )
        if (
            _VERSION.fullmatch(found.embed_version) is None
            or _VERSION.fullmatch(found.corpus_hash) is None
            or found.method not in retrieval.METHODS
            or len({r.question_id for r in rows}) != len(rows)
        ):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise AnswerResultsError from None
    return found
