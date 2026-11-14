"""Fixed-line output for `make tag` and `make tag-bench` (D89).

Every line is built from numbers and schema names and checked with fullmatch before anything is
printed, so no message text, name or path can reach the terminal even through a bug. The failure
line carries a step name and an exception class name only.
"""

import re

from gcwrapped.labeling.schema import SENTIMENTS, TOPICS
from gcwrapped.metrics import ALLOWED_DATABASES
from gcwrapped.tagging.runner import RunResult

_DATABASES = "|".join(sorted(ALLOWED_DATABASES))
_NUM = r"\d+(\.\d+)?"
_MAYBE = rf"({_NUM}|n/a)"
_TOPICS = "|".join(TOPICS)
_SENTIMENTS = "|".join(SENTIMENTS)
_MODEL = r"[A-Za-z0-9._-]+"

TAG_SHAPES = (
    rf"target database: ({_DATABASES})",
    rf"model id: {_MODEL}",
    r"prompt version: [0-9a-f]{12}",
    r"local-eligible text messages: \d+",
    r"already tagged: \d+",
    r"tagged this run: \d+",
    r"failures: \d+",
    r"pruned: \d+",
    rf"seconds: {_NUM}",
    rf"messages per second: {_NUM}",
    rf"p50 seconds per message: {_MAYBE}",
    rf"p95 seconds per message: {_MAYBE}",
    *(rf"topic ({t}): \d+" for t in TOPICS),
    *(rf"sentiment ({s}): \d+" for s in SENTIMENTS),
    r"tag: ok",
)
BENCH_SHAPES = (
    r"target database: gcw_dev",
    rf"model id: {_MODEL}",
    r"prompt version: [0-9a-f]{12}",
    r"messages benchmarked: \d+",
    r"failures: \d+",
    rf"seconds: {_NUM}",
    rf"messages per second: {_NUM}",
    rf"p50 seconds per message: {_MAYBE}",
    rf"p95 seconds per message: {_MAYBE}",
    rf"synthetic topic accuracy: {_MAYBE}",
    rf"synthetic sentiment accuracy: {_MAYBE}",
    r"tag-bench: ok",
)
_FAILED_AT = (
    r"(tag|tag-bench|llm-model): failed at "
    r"(database|docker|llm url|model|eligible messages|tagging|labels|render) "
    r"\([A-Za-z_][A-Za-z0-9_]*\)"
)


class ShapeError(Exception):
    """A rendered line did not match its allowed shape. Only the class name is ever printed."""


def _maybe(value: float | None, places: int) -> str:
    return "n/a" if value is None else f"{value:.{places}f}"


def check_lines(lines: list[str], shapes: tuple[str, ...]) -> list[str]:
    if len(lines) != len(shapes):
        raise ShapeError
    for line, shape in zip(lines, shapes, strict=True):
        if re.fullmatch(shape, line) is None:
            raise ShapeError
    return lines


def timing_lines(result: RunResult) -> list[str]:
    return [
        f"seconds: {result.seconds:.2f}",
        f"messages per second: {result.messages_per_second:.2f}",
        f"p50 seconds per message: {_maybe(result.timings.percentile(50), 3)}",
        f"p95 seconds per message: {_maybe(result.timings.percentile(95), 3)}",
    ]


def render_tag(database: str, result: RunResult) -> list[str]:
    lines = [
        f"target database: {database}",
        f"model id: {result.model_id}",
        f"prompt version: {result.prompt_version}",
        f"local-eligible text messages: {result.eligible}",
        f"already tagged: {result.already_tagged}",
        f"tagged this run: {result.tagged}",
        f"failures: {result.failures}",
        f"pruned: {result.pruned}",
        *timing_lines(result),
        *(f"topic {t}: {result.topics.get(t, 0)}" for t in TOPICS),
        *(f"sentiment {s}: {result.sentiments.get(s, 0)}" for s in SENTIMENTS),
        "tag: ok",
    ]
    return check_lines(lines, TAG_SHAPES)


def render_bench(
    database: str,
    result: RunResult,
    benchmarked: int,
    topic_accuracy: float | None,
    sentiment_accuracy: float | None,
) -> list[str]:
    lines = [
        f"target database: {database}",
        f"model id: {result.model_id}",
        f"prompt version: {result.prompt_version}",
        f"messages benchmarked: {benchmarked}",
        f"failures: {result.failures}",
        *timing_lines(result),
        f"synthetic topic accuracy: {_maybe(topic_accuracy, 3)}",
        f"synthetic sentiment accuracy: {_maybe(sentiment_accuracy, 3)}",
        "tag-bench: ok",
    ]
    return check_lines(lines, BENCH_SHAPES)


def failure_line(command: str, step: str, exc: BaseException) -> str:
    line = f"{command}: failed at {step} ({type(exc).__name__})"
    if re.fullmatch(_FAILED_AT, line) is None:  # only a strange exception name can land here
        line = f"{command}: failed at {step} (Exception)"
    return line
