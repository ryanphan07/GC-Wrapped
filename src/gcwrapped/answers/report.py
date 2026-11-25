"""Fixed-line output for `make answer-eval`, `answer-compare`, `answer-promote`, `grade-summary`
and `grade-answers`' closing counts, and for `make ask`'s numbers line and summary (D112, D113,
D115, D116, D120, D121).

Every line is a fixed label plus numbers or a name from a closed list, checked with fullmatch
before anything is printed. The deliberate exceptions in this package are what `commands` prints to
the terminal for `make ask` (the answer and the messages it cites or, after an abstention, was
shown) and for `make grade-answers` (the question, the answer and the cited messages).
"""

import re
from collections.abc import Mapping, Sequence

from gcwrapped.answers.comparison import CompareResult
from gcwrapped.answers.evaluation import EvalResult, Rate
from gcwrapped.answers.grades import Summary
from gcwrapped.answers.pipeline import ABSTAINED, ANSWERED, INVALID, TIMED_OUT
from gcwrapped.answers.prompt import VARIANTS
from gcwrapped.metrics import ALLOWED_DATABASES
from gcwrapped.retrieval import report as retrieval_report
from gcwrapped.retrieval.evaluation import METHODS, Estimate
from gcwrapped.tagging.model import REGISTRY
from gcwrapped.tagging.report import check_lines
from gcwrapped.tagging.runner import Timings
from gcwrapped.tagging.scoring import nearest_rank

_DATABASES = "|".join(sorted(ALLOWED_DATABASES))
_METHODS = "|".join(METHODS)
_MODELS = "|".join(re.escape(m) for m in REGISTRY)
_VARIANTS = "|".join(VARIANTS)
_NUM = r"\d+\.\d+"
_SIGNED = r"-?\d+\.\d+"
_MAYBE = rf"({_NUM}|n/a)"
_INTERVAL = rf"\[{_NUM}, {_NUM}\]"
_TOKENS = r"(\d+|n/a)"
_RATES = (
    "invalid outputs",
    "timed out",
    "answered",
    "abstained",
    "context has gold",
    "citation hit",
)

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
    rf"p50 prompt tokens: {_TOKENS}",
    rf"p95 prompt tokens: {_TOKENS}",
    rf"max prompt tokens: {_TOKENS}",
    r"answer-eval: ok",
)
ANSWER_SHAPE = rf"sources shown: \d+, prompt tokens: {_TOKENS}, seconds: {_NUM}"
ASK_SHAPES = (
    r"questions asked: \d+",
    r"answered: \d+",
    r"abstained: \d+",
    r"invalid outputs: \d+",
    r"timed out: \d+",
    rf"p50 seconds per question: {_MAYBE}",
    rf"p95 seconds per question: {_MAYBE}",
    rf"p50 prompt tokens: {_TOKENS}",
    rf"p95 prompt tokens: {_TOKENS}",
    r"ask: ok",
)
_YES_NO = r"(yes|no)"
COMPARE_SHAPES = (
    r"questions scored: \d+",
    rf"citation hit rate A: {_NUM}",
    rf"citation hit rate B: {_NUM}",
    rf"citation hit rate difference \(B minus A\): {_SIGNED}",
    rf"citation hit rate difference 95% interval: \[{_SIGNED}, {_SIGNED}\]",
    rf"invalid plus timed out rate A: {_NUM}",
    rf"invalid plus timed out rate B: {_NUM}",
    rf"p95 seconds per question A: {_NUM}",
    rf"p95 seconds per question B: {_NUM}",
    rf"citation hit condition met: {_YES_NO}",
    rf"failure rate condition met: {_YES_NO}",
    rf"latency condition met: {_YES_NO}",
    rf"winner: ({_VARIANTS})",
    r"answer-compare: ok",
)
PROMOTE_SHAPES = (
    rf"target database: ({_DATABASES})",
    rf"promoted variant: ({_VARIANTS})",
    rf"previous variant: ({_VARIANTS}|none)",
    r"answer-promote: ok",
)
_MAYBE_INTERVAL = rf"(\[{_NUM}, {_NUM}\]|n/a)"
GRADE_SHAPES = (
    r"graded this run: \d+",
    r"graded in the file: \d+",
    r"questions not yet graded: \d+",
    r"grade-answers: ok",
)
_GRADE_COUNTS = (
    "graded questions",
    "answered",
    "abstained",
    "invalid outputs",
    "timed out",
    "correct",
    "partial",
    "wrong",
    "supported",
)
_GRADE_RATES = ("answer accuracy", "precision", "supported rate")
SUMMARY_SHAPES = (
    rf"model id: ({_MODELS})",
    r"answer prompt version: [0-9a-f]{12}",
    rf"retrieval method: ({_METHODS})",
    *(rf"{name}: \d+" for name in _GRADE_COUNTS),
    *(
        shape
        for name in _GRADE_RATES
        for shape in (rf"{name}: {_MAYBE}", rf"{name} 95% interval: {_MAYBE_INTERVAL}")
    ),
    r"grade-summary: ok",
)
COMMANDS = (
    "answer-eval",
    "ask",
    "answer-compare",
    "answer-promote",
    "grade-answers",
    "grade-summary",
)
STEPS = (
    "count|variant|database|label directory|embed url|llm url|questions|docker|model|retrieval|"
    "corpus|grades|answering|scoring|predictions|verdict|promote|render"
)
_FAILED_AT = rf"({'|'.join(COMMANDS)}): failed at ({STEPS}) \([A-Za-z_][A-Za-z0-9_]*\)"


def _rate_lines(name: str, rate: Rate) -> list[str]:
    low, high = rate.estimate.interval
    return [
        f"{name}: {rate.count}",
        f"{name} rate: {rate.estimate.value:.3f}",
        f"{name} rate 95% interval: [{low:.3f}, {high:.3f}]",
    ]


def _tokens(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


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
        result.timed_out,
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
        f"p50 prompt tokens: {_tokens(result.prompt_tokens_p50)}",
        f"p95 prompt tokens: {_tokens(result.prompt_tokens_p95)}",
        f"max prompt tokens: {_tokens(result.prompt_tokens_max)}",
        "answer-eval: ok",
    ]
    return check_lines(lines, EVAL_SHAPES)


def answer_line(sources: int, prompt_tokens: int | None, seconds: float) -> str:
    """The numbers line after each answer of `make ask` (D115): safe to paste anywhere."""
    line = (
        f"sources shown: {sources}, prompt tokens: {_tokens(prompt_tokens)}, seconds: {seconds:.3f}"
    )
    return check_lines([line], (ANSWER_SHAPE,))[0]


def render_ask(
    counts: Mapping[str, int], timings: Timings, prompt_tokens: Sequence[int]
) -> list[str]:
    """The summary. `counts` is per outcome; `prompt_tokens` holds the reported counts only."""

    def seconds(percent: int) -> str:
        value = timings.percentile(percent)
        return "n/a" if value is None else f"{value:.3f}"

    lines = [
        f"questions asked: {sum(counts.values())}",
        f"answered: {counts[ANSWERED]}",
        f"abstained: {counts[ABSTAINED]}",
        f"invalid outputs: {counts[INVALID]}",
        f"timed out: {counts[TIMED_OUT]}",
        f"p50 seconds per question: {seconds(50)}",
        f"p95 seconds per question: {seconds(95)}",
        f"p50 prompt tokens: {_tokens(nearest_rank(prompt_tokens, 50))}",
        f"p95 prompt tokens: {_tokens(nearest_rank(prompt_tokens, 95))}",
        "ask: ok",
    ]
    return check_lines(lines, ASK_SHAPES)


def _yes_no(met: bool) -> str:
    return "yes" if met else "no"


def render_compare(result: CompareResult) -> list[str]:
    low, high = result.difference.interval
    lines = [
        f"questions scored: {result.scored}",
        f"citation hit rate A: {result.a_hit_rate:.3f}",
        f"citation hit rate B: {result.b_hit_rate:.3f}",
        f"citation hit rate difference (B minus A): {result.difference.value:.3f}",
        f"citation hit rate difference 95% interval: [{low:.3f}, {high:.3f}]",
        f"invalid plus timed out rate A: {result.a_failures / result.scored:.3f}",
        f"invalid plus timed out rate B: {result.b_failures / result.scored:.3f}",
        f"p95 seconds per question A: {result.a_p95:.3f}",
        f"p95 seconds per question B: {result.b_p95:.3f}",
        f"citation hit condition met: {_yes_no(result.hit_ok)}",
        f"failure rate condition met: {_yes_no(result.failures_ok)}",
        f"latency condition met: {_yes_no(result.latency_ok)}",
        f"winner: {result.winner}",
        "answer-compare: ok",
    ]
    return check_lines(lines, COMPARE_SHAPES)


def render_promote(database: str, variant: str, previous: str | None) -> list[str]:
    lines = [
        f"target database: {database}",
        f"promoted variant: {variant}",
        f"previous variant: {previous or 'none'}",
        "answer-promote: ok",
    ]
    return check_lines(lines, PROMOTE_SHAPES)


def render_grading(graded_now: int, graded: int, remaining: int) -> list[str]:
    lines = [
        f"graded this run: {graded_now}",
        f"graded in the file: {graded}",
        f"questions not yet graded: {remaining}",
        "grade-answers: ok",
    ]
    return check_lines(lines, GRADE_SHAPES)


def _estimate_lines(name: str, estimate: Estimate | None) -> list[str]:
    if estimate is None:
        return [f"{name}: n/a", f"{name} 95% interval: n/a"]
    low, high = estimate.interval
    return [f"{name}: {estimate.value:.3f}", f"{name} 95% interval: [{low:.3f}, {high:.3f}]"]


def render_summary(model_id: str, prompt_version: str, method: str, summary: Summary) -> list[str]:
    counts = (
        summary.graded,
        summary.answered,
        summary.abstained,
        summary.invalid,
        summary.timed_out,
        summary.correct,
        summary.partial,
        summary.wrong,
        summary.supported,
    )
    rates = (summary.accuracy, summary.precision, summary.supported_rate)
    lines = [
        f"model id: {model_id}",
        f"answer prompt version: {prompt_version}",
        f"retrieval method: {method}",
        *(f"{name}: {count}" for name, count in zip(_GRADE_COUNTS, counts, strict=True)),
        *(
            line
            for name, rate in zip(_GRADE_RATES, rates, strict=True)
            for line in _estimate_lines(name, rate)
        ),
        "grade-summary: ok",
    ]
    return check_lines(lines, SUMMARY_SHAPES)


def failure_line(command: str, step: str, exc: BaseException) -> str:
    line = f"{command}: failed at {step} ({type(exc).__name__})"
    if re.fullmatch(_FAILED_AT, line) is None:  # only a strange exception name can land here
        line = f"{command}: failed at {step} (Exception)"
    return line
