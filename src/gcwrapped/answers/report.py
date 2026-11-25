"""Fixed-line output for `make answer-eval` and `make ask`'s summary (D112, D113).

Every line is a fixed label plus numbers or a name from a closed list, checked with fullmatch
before anything is printed. The one deliberate exception in this package is the answer and its
cited messages, which `commands` prints to the terminal for `make ask` only.
"""

import re

from gcwrapped.answers.evaluation import EvalResult, Rate
from gcwrapped.metrics import ALLOWED_DATABASES
from gcwrapped.retrieval import report as retrieval_report
from gcwrapped.retrieval.evaluation import METHODS
from gcwrapped.tagging.model import REGISTRY
from gcwrapped.tagging.report import check_lines
from gcwrapped.tagging.runner import Timings

_DATABASES = "|".join(sorted(ALLOWED_DATABASES))
_METHODS = "|".join(METHODS)
_MODELS = "|".join(re.escape(m) for m in REGISTRY)
_NUM = r"\d+\.\d+"
_MAYBE = rf"({_NUM}|n/a)"
_INTERVAL = rf"\[{_NUM}, {_NUM}\]"
_RATES = ("invalid outputs", "answered", "abstained", "context has gold", "citation hit")

EVAL_SHAPES = (
    rf"target database: ({_DATABASES})",
    r"questions source: (synthetic|real)",
    rf"model id: ({_MODELS})",
    r"answer prompt version: [0-9a-f]{12}",
    rf"retrieval method: ({_METHODS})",
    r"embed version: [0-9a-f]{12}",
    r"messages not embedded: \d+",
    r"questions: \d+",
    r"questions dropped \(gold not embedded or ineligible\): \d+",
    r"questions scored: \d+",
    *(
        shape
        for name in _RATES
        for shape in (
            rf"{name}: \d+",
            rf"{name} rate: {_NUM}",
            rf"{name} rate 95% interval: {_INTERVAL}",
        )
    ),
    rf"p50 seconds per question: {_NUM}",
    rf"p95 seconds per question: {_NUM}",
    r"answer-eval: ok",
)
ASK_SHAPES = (
    r"questions asked: \d+",
    r"answered: \d+",
    r"abstained: \d+",
    r"invalid outputs: \d+",
    rf"p50 seconds per question: {_MAYBE}",
    rf"p95 seconds per question: {_MAYBE}",
    r"ask: ok",
)
COMMANDS = ("answer-eval", "ask")
STEPS = (
    "database|label directory|embed url|llm url|questions|docker|model|retrieval|corpus|"
    "answering|scoring|predictions|render"
)
_FAILED_AT = rf"({'|'.join(COMMANDS)}): failed at ({STEPS}) \([A-Za-z_][A-Za-z0-9_]*\)"


def _rate_lines(name: str, rate: Rate) -> list[str]:
    low, high = rate.estimate.interval
    return [
        f"{name}: {rate.count}",
        f"{name} rate: {rate.estimate.value:.3f}",
        f"{name} rate 95% interval: [{low:.3f}, {high:.3f}]",
    ]


def render_eval(
    database: str,
    source: str,
    model_id: str,
    prompt_version: str,
    method: str,
    embed_version: str,
    result: EvalResult,
) -> list[str]:
    rates = (
        result.invalid,
        result.answered,
        result.abstained,
        result.context_has_gold,
        result.citation_hit,
    )
    lines = [
        f"target database: {database}",
        f"questions source: {source}",
        f"model id: {model_id}",
        f"answer prompt version: {prompt_version}",
        f"retrieval method: {method}",
        f"embed version: {embed_version}",
        retrieval_report.unembedded_line(0),  # any other count was refused earlier (D109)
        f"questions: {result.questions}",
        f"questions dropped (gold not embedded or ineligible): {result.dropped}",
        f"questions scored: {result.scored}",
        *(
            line
            for name, rate in zip(_RATES, rates, strict=True)
            for line in _rate_lines(name, rate)
        ),
        f"p50 seconds per question: {result.p50:.3f}",
        f"p95 seconds per question: {result.p95:.3f}",
        "answer-eval: ok",
    ]
    return check_lines(lines, EVAL_SHAPES)


def render_ask(answered: int, abstained: int, invalid: int, timings: Timings) -> list[str]:
    def seconds(percent: int) -> str:
        value = timings.percentile(percent)
        return "n/a" if value is None else f"{value:.3f}"

    lines = [
        f"questions asked: {answered + abstained + invalid}",
        f"answered: {answered}",
        f"abstained: {abstained}",
        f"invalid outputs: {invalid}",
        f"p50 seconds per question: {seconds(50)}",
        f"p95 seconds per question: {seconds(95)}",
        "ask: ok",
    ]
    return check_lines(lines, ASK_SHAPES)


def failure_line(command: str, step: str, exc: BaseException) -> str:
    line = f"{command}: failed at {step} ({type(exc).__name__})"
    if re.fullmatch(_FAILED_AT, line) is None:  # only a strange exception name can land here
        line = f"{command}: failed at {step} (Exception)"
    return line
