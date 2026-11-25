"""Fixed-line output for `make embed`, `retrieval-eval` and `retrieval-compare`.

Every line is a fixed label plus numbers or a name from a closed list, checked with fullmatch
before anything is printed, so no question, message text, sender, ID or path can reach the terminal
even through a bug. The failure line carries a step name and an exception class name only.
"""

import re

from gcwrapped.metrics import ALLOWED_DATABASES
from gcwrapped.retrieval.embed import EmbedResult
from gcwrapped.retrieval.evaluation import (
    METHODS,
    RECALL_AT,
    CompareResult,
    Estimate,
    EvalResult,
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
COMMANDS = ("embed", "retrieval-eval", "retrieval-compare")
STEPS = (
    "method|database|label directory|embed url|questions|predictions|docker|embedding|"
    "retrieval|scoring|render"
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


def render_eval(
    database: str, source: str, method: str, version: str, result: EvalResult
) -> list[str]:
    lines = [
        f"target database: {database}",
        f"questions source: {source}",
        f"method: {method}",
        f"embed version: {version}",
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


def failure_line(command: str, step: str, exc: BaseException) -> str:
    line = f"{command}: failed at {step} ({type(exc).__name__})"
    if re.fullmatch(_FAILED_AT, line) is None:  # only a strange exception name can land here
        line = f"{command}: failed at {step} (Exception)"
    return line
