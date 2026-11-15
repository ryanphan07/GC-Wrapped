"""Fixed-line output for `make tag`, `tag-eval`, `tag-compare` and `tag-promote` (D89, D92).

Every line is built from numbers and schema names and checked with fullmatch before anything is
printed, so no message text, name or path can reach the terminal even through a bug. The failure
line carries a step name and an exception class name only.
"""

import re
from collections.abc import Mapping

from gcwrapped.labeling.schema import SENTIMENTS, TOPICS
from gcwrapped.metrics import ALLOWED_DATABASES
from gcwrapped.tagging import evaluation, scoring
from gcwrapped.tagging.runner import RunResult, Timings

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
_SIGNED = r"-?\d+\.\d+"
_INTERVAL = rf"\[{_SIGNED}, {_SIGNED}\]"
_SOURCE = "synthetic|real"
_PASS = "pass|fail"
_METRICS = ("topic", "sentiment")
EVAL_SHAPES = (
    rf"target database: ({_DATABASES})",
    rf"labels source: ({_SOURCE})",
    rf"model id: {_MODEL}",
    r"prompt version: [0-9a-f]{12}",
    r"labels: \d+",
    r"labels dropped \(not local-eligible\): \d+",
    r"messages scored: \d+",
    r"failures: \d+",
    *(
        shape
        for m in _METRICS
        for shape in (
            rf"{m} accuracy: {_NUM}",
            rf"{m} accuracy 95% interval: {_INTERVAL}",
            rf"{m} macro-F1: {_NUM}",
            rf"{m} macro-F1 95% interval: {_INTERVAL}",
        )
    ),
    rf"seconds: {_NUM}",
    rf"messages per second: {_NUM}",
    rf"p50 seconds per message: {_MAYBE}",
    rf"p95 seconds per message: {_MAYBE}",
    *(rf"topic F1 \({t}\): {_MAYBE}" for t in TOPICS),
    *(rf"sentiment F1 \({s}\): {_MAYBE}" for s in SENTIMENTS),
    r"tag-eval: ok",
)
COMPARE_SHAPES = (
    rf"target database: ({_DATABASES})",
    rf"labels source: ({_SOURCE})",
    r"prompt version: [0-9a-f]{12}",
    rf"model A id: {_MODEL}",
    rf"model B id: {_MODEL}",
    r"messages compared: \d+",
    *(
        shape
        for m in _METRICS
        for shape in (
            rf"{m} accuracy A: {_NUM}",
            rf"{m} accuracy B: {_NUM}",
            rf"{m} accuracy difference \(B minus A\): {_SIGNED}",
            rf"{m} accuracy difference 95% interval: {_INTERVAL}",
        )
    ),
    rf"messages per second A: {_NUM}",
    rf"messages per second B: {_NUM}",
    rf"speedup \(B over A\): {_MAYBE}",
    rf"check speedup at least {re.escape(str(scoring.MIN_SPEEDUP))}: ({_PASS})",
    rf"check topic lower bound at least {re.escape(str(scoring.MARGIN))}: ({_PASS})",
    rf"check sentiment lower bound at least {re.escape(str(scoring.MARGIN))}: ({_PASS})",
    rf"winner: {_MODEL}",
    r"tag-compare: ok",
)
PROMOTE_SHAPES = (
    rf"target database: ({_DATABASES})",
    rf"promoted model id: {_MODEL}",
    rf"previous model id: ({_MODEL}|none)",
    r"tag-promote: ok",
)
COMMANDS = ("tag", "tag-eval", "tag-compare", "tag-promote", "llm-model")
STEPS = (
    "database|docker|llm url|model|eligible messages|tagging|labels|label directory|"
    "predictions|scoring|verdict|promote|render"
)
_FAILED_AT = (
    rf"({'|'.join(COMMANDS)}): failed at ({STEPS}) "
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


def _f(value: float, places: int = 3) -> str:
    return f"{value:.{places}f}"


def _interval(bounds: tuple[float, float]) -> str:
    return f"[{_f(bounds[0])}, {_f(bounds[1])}]"


def _f1_lines(name: str, classes: tuple[str, ...], scores: Mapping[str, float]) -> list[str]:
    return [f"{name} F1 ({c}): {_maybe(scores.get(c), 3)}" for c in classes]


def render_eval(
    database: str,
    source: str,
    model_id: str,
    prompt_version: str,
    result: evaluation.EvalResult,
) -> list[str]:
    timings = Timings(list(result.seconds))
    lines = [
        f"target database: {database}",
        f"labels source: {source}",
        f"model id: {model_id}",
        f"prompt version: {prompt_version}",
        f"labels: {result.labels}",
        f"labels dropped (not local-eligible): {result.dropped}",
        f"messages scored: {result.scored}",
        f"failures: {result.failures}",
    ]
    for name, metric in (("topic", result.topic), ("sentiment", result.sentiment)):
        lines += [
            f"{name} accuracy: {_f(metric.accuracy)}",
            f"{name} accuracy 95% interval: {_interval(metric.accuracy_interval)}",
            f"{name} macro-F1: {_f(metric.macro_f1)}",
            f"{name} macro-F1 95% interval: {_interval(metric.macro_f1_interval)}",
        ]
    lines += [
        f"seconds: {_f(sum(result.seconds), 2)}",
        f"messages per second: {_f(scoring.messages_per_second(result.seconds), 2)}",
        f"p50 seconds per message: {_maybe(timings.percentile(50), 3)}",
        f"p95 seconds per message: {_maybe(timings.percentile(95), 3)}",
        *_f1_lines("topic", TOPICS, result.topic.class_f1),
        *_f1_lines("sentiment", SENTIMENTS, result.sentiment.class_f1),
        "tag-eval: ok",
    ]
    return check_lines(lines, EVAL_SHAPES)


def render_compare(
    database: str,
    source: str,
    prompt_version: str,
    a_id: str,
    b_id: str,
    result: evaluation.CompareResult,
) -> list[str]:
    lines = [
        f"target database: {database}",
        f"labels source: {source}",
        f"prompt version: {prompt_version}",
        f"model A id: {a_id}",
        f"model B id: {b_id}",
        f"messages compared: {result.messages}",
    ]
    for name, diff in (("topic", result.topic), ("sentiment", result.sentiment)):
        lines += [
            f"{name} accuracy A: {_f(diff.a)}",
            f"{name} accuracy B: {_f(diff.b)}",
            f"{name} accuracy difference (B minus A): {_f(diff.difference)}",
            f"{name} accuracy difference 95% interval: {_interval(diff.interval)}",
        ]
    verdict = result.verdict

    def mark(ok: bool) -> str:
        return "pass" if ok else "fail"

    lines += [
        f"messages per second A: {_f(result.a_mps, 2)}",
        f"messages per second B: {_f(result.b_mps, 2)}",
        f"speedup (B over A): {_maybe(result.speedup, 2)}",
        f"check speedup at least {scoring.MIN_SPEEDUP}: {mark(verdict.speed_ok)}",
        f"check topic lower bound at least {scoring.MARGIN}: {mark(verdict.topic_ok)}",
        f"check sentiment lower bound at least {scoring.MARGIN}: {mark(verdict.sentiment_ok)}",
        f"winner: {b_id if verdict.b_wins else a_id}",
        "tag-compare: ok",
    ]
    return check_lines(lines, COMPARE_SHAPES)


def render_promote(database: str, model_id: str, previous: str | None) -> list[str]:
    lines = [
        f"target database: {database}",
        f"promoted model id: {model_id}",
        f"previous model id: {previous or 'none'}",
        "tag-promote: ok",
    ]
    return check_lines(lines, PROMOTE_SHAPES)


def failure_line(command: str, step: str, exc: BaseException) -> str:
    line = f"{command}: failed at {step} ({type(exc).__name__})"
    if re.fullmatch(_FAILED_AT, line) is None:  # only a strange exception name can land here
        line = f"{command}: failed at {step} (Exception)"
    return line
