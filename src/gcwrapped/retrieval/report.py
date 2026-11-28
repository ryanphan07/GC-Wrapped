"""Fixed-line output for `make embed`, `retrieval-eval`, `retrieval-compare`,
`retrieval-promote` and `retrieval-misses`.

Every line is a fixed label plus numbers or a name from a closed list, checked with fullmatch
before anything is printed, so no question, message text, sender, ID or path can reach the terminal
even through a bug. The failure line carries a step name and an exception class name only.
"""

import re
from collections.abc import Sequence

from gcwrapped.metrics import ALLOWED_DATABASES
from gcwrapped.retrieval.embed import EmbedResult
from gcwrapped.retrieval.evaluation import (
    METHODS,
    RECALL_AT,
    CompareResult,
    Estimate,
    EvalResult,
)
from gcwrapped.retrieval.misses import (
    HIT_AT,
    LENGTH_BUCKETS,
    NOT_FOUND,
    RANK_BUCKETS,
    Breakdown,
)
from gcwrapped.tagging.model import EMBED_REGISTRY
from gcwrapped.tagging.report import check_lines

_DATABASES = "|".join(sorted(ALLOWED_DATABASES))
_METHODS = "|".join(METHODS)
_MODELS = "|".join(re.escape(m) for m in EMBED_REGISTRY)
_NUM = r"\d+\.\d+"
_SIGNED = r"-?\d+\.\d+"
_INTERVAL = rf"\[{_SIGNED}, {_SIGNED}\]"
_NAMES = (*(f"recall@{k}" for k in RECALL_AT), "MRR@10")

EMBED_SHAPES = (
    rf"target database: ({_DATABASES})",
    rf"model id: ({_MODELS})",
    r"embed version: [0-9a-f]{12}",
    r"messages eligible: \d+",
    r"embedded this run: \d+",
    r"already current: \d+",
    r"failures: \d+",
    rf"seconds: {_NUM}",
    rf"messages per second: {_NUM}",
    r"embed: ok",
)
EVAL_SHAPES = (
    rf"target database: ({_DATABASES})",
    r"questions source: (synthetic|real)",
    rf"method: ({_METHODS})",
    r"embed version: [0-9a-f]{12}",
    r"messages not embedded: \d+",
    r"questions: \d+",
    r"questions dropped \(gold not embedded or ineligible\): \d+",
    r"questions scored: \d+",
    *(
        shape
        for name in _NAMES
        for shape in (rf"{name}: {_NUM}", rf"{name} 95% interval: {_INTERVAL}")
    ),
    rf"p50 retrieval seconds: {_NUM}",
    rf"p95 retrieval seconds: {_NUM}",
    r"retrieval-eval: ok",
)
COMPARE_SHAPES = (
    r"questions scored: \d+",
    rf"recall@5 A: {_NUM}",
    rf"recall@5 B: {_NUM}",
    rf"recall@5 difference \(B minus A\): {_SIGNED}",
    rf"recall@5 difference 95% interval: {_INTERVAL}",
    rf"p95 seconds B: {_NUM}",
    r"latency limit met: (yes|no)",
    rf"winner: ({_METHODS})",
    r"retrieval-compare: ok",
)
PROMOTE_SHAPES = (
    rf"target database: ({_DATABASES})",
    rf"promoted method: ({_METHODS})",
    rf"previous method: ({_METHODS}|none)",
    r"retrieval-promote: ok",
)
_RANK_NAMES = tuple(name for name, _ in RANK_BUCKETS)
_LENGTH_NAMES = tuple(name for name, _ in LENGTH_BUCKETS)
_MISS_LABELS = (
    "questions",
    "dropped, right message not searchable",
    "dropped, source message not eligible",
    "questions scored",
    *(f"right message at rank {name}" for name in _RANK_NAMES),
    f"right message {NOT_FOUND}",
    *(
        f"{outcome}@{HIT_AT}, right message of {name} words"
        for outcome in ("hit", "miss")
        for name in _LENGTH_NAMES
    ),
    f"misses@{HIT_AT} with the right message inside a top-{HIT_AT} window",
    "questions whose top 10 differs from the stored run",
)
_MISS_BLOCK = (
    rf"method: ({_METHODS})",
    *(rf"{re.escape(label)}: \d+" for label in _MISS_LABELS),
)
COMMANDS = (
    "embed",
    "retrieval-eval",
    "retrieval-compare",
    "retrieval-promote",
    "retrieval-misses",
)
STEPS = (
    "method|database|label directory|embed url|questions|predictions|docker|model|memory|"
    "embedding|retrieval|corpus|scoring|verdict|promote|render"
)
_FAILED_AT = rf"({'|'.join(COMMANDS)}): failed at ({STEPS}) \([A-Za-z_][A-Za-z0-9_]*\)"


def render_embed(database: str, result: EmbedResult) -> list[str]:
    lines = [
        f"target database: {database}",
        f"model id: {result.model_id}",
        f"embed version: {result.version}",
        f"messages eligible: {result.eligible}",
        f"embedded this run: {result.embedded}",
        f"already current: {result.already_current}",
        f"failures: {result.failures}",
        f"seconds: {result.seconds:.2f}",
        f"messages per second: {result.messages_per_second:.2f}",
        "embed: ok",
    ]
    return check_lines(lines, EMBED_SHAPES)


def _interval(estimate: Estimate) -> str:
    low, high = estimate.interval
    return f"[{low:.3f}, {high:.3f}]"


def unembedded_line(missing: int) -> str:
    """The D109 line, printed alone when a run is refused and among the others when it is not."""
    return check_lines([f"messages not embedded: {missing}"], (r"messages not embedded: \d+",))[0]


def render_eval(
    database: str, source: str, method: str, version: str, result: EvalResult
) -> list[str]:
    lines = [
        f"target database: {database}",
        f"questions source: {source}",
        f"method: {method}",
        f"embed version: {version}",
        unembedded_line(0),  # a run with any other count was refused before it searched (D109)
        f"questions: {result.questions}",
        f"questions dropped (gold not embedded or ineligible): {result.dropped}",
        f"questions scored: {result.scored}",
    ]
    estimates = (*(result.recall[k] for k in RECALL_AT), result.mrr)
    for name, estimate in zip(_NAMES, estimates, strict=True):
        lines += [f"{name}: {estimate.value:.3f}", f"{name} 95% interval: {_interval(estimate)}"]
    lines += [
        f"p50 retrieval seconds: {result.p50:.3f}",
        f"p95 retrieval seconds: {result.p95:.3f}",
        "retrieval-eval: ok",
    ]
    return check_lines(lines, EVAL_SHAPES)


def render_compare(a: str, b: str, result: CompareResult) -> list[str]:
    lines = [
        f"questions scored: {result.scored}",
        f"recall@5 A: {result.a_recall:.3f}",
        f"recall@5 B: {result.b_recall:.3f}",
        f"recall@5 difference (B minus A): {result.difference.value:.3f}",
        f"recall@5 difference 95% interval: {_interval(result.difference)}",
        f"p95 seconds B: {result.b_p95:.3f}",
        f"latency limit met: {'yes' if result.latency_ok else 'no'}",
        f"winner: {b if result.b_wins else a}",
        "retrieval-compare: ok",
    ]
    return check_lines(lines, COMPARE_SHAPES)


def render_promote(database: str, method: str, previous: str | None) -> list[str]:
    lines = [
        f"target database: {database}",
        f"promoted method: {method}",
        f"previous method: {previous or 'none'}",
        "retrieval-promote: ok",
    ]
    return check_lines(lines, PROMOTE_SHAPES)


def misses_shapes(methods: int) -> tuple[str, ...]:
    """The shapes of a retrieval-misses output with that many method blocks."""
    return (
        rf"target database: ({_DATABASES})",
        r"questions source: (synthetic|real)",
        r"embed version: [0-9a-f]{12}",
        *_MISS_BLOCK * methods,
        r"retrieval-misses: ok",
    )


def render_misses(
    database: str, source: str, version: str, breakdowns: Sequence[Breakdown]
) -> list[str]:
    lines = [
        f"target database: {database}",
        f"questions source: {source}",
        f"embed version: {version}",
    ]
    for found in breakdowns:
        values = (
            found.questions,
            found.gold_not_searchable,
            found.source_not_eligible,
            found.scored,
            *(found.ranks[name] for name in (*_RANK_NAMES, NOT_FOUND)),
            *(found.hit_lengths[name] for name in _LENGTH_NAMES),
            *(found.miss_lengths[name] for name in _LENGTH_NAMES),
            found.near_misses,
            found.changed,
        )
        lines.append(f"method: {found.method}")
        lines += [f"{label}: {value}" for label, value in zip(_MISS_LABELS, values, strict=True)]
    lines.append("retrieval-misses: ok")
    return check_lines(lines, misses_shapes(len(breakdowns)))


def failure_line(command: str, step: str, exc: BaseException) -> str:
    line = f"{command}: failed at {step} ({type(exc).__name__})"
    if re.fullmatch(_FAILED_AT, line) is None:  # only a strange exception name can land here
        line = f"{command}: failed at {step} (Exception)"
    return line
