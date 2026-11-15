"""Scoring, bootstrap intervals and the D93 win rule: pure functions, no I/O, no message text.

A prediction is the model's label for one message, or None when the model failed on it. A failure
never equals a gold label, so it counts as wrong for accuracy; for per-class F1 it is a missed gold
item (a false negative for the gold class) and adds no false positive anywhere.

Intervals are 95% percentile bootstrap intervals (D92): resample the labeled messages with
replacement RESAMPLES times with a fixed seed, compute the statistic on each resample, and take the
2.5th and 97.5th percentiles of the sorted values. A paired comparison passes the SAME resampled
indices to both models (the statistic is a difference), which removes most of the sampling noise.
"""

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

RESAMPLES = 2000
SEED = 20261114
ALPHA = 0.05

# D93, fixed before any run: B must be at least this many times faster than A, and the lower bound
# of the paired interval of (B minus A) accuracy must be at least this, for topic and for sentiment.
MIN_SPEEDUP = 1.5
MARGIN = -0.05
_FLOAT_NOISE_PLACES = 9  # a bound that is -0.05 up to float noise counts as -0.05

Predictions = Sequence[str | None]


def accuracy(gold: Sequence[str], pred: Predictions, indices: Sequence[int] | None = None) -> float:
    """Share of the selected items whose prediction equals the gold label. Needs >= 1 item."""
    chosen = range(len(gold)) if indices is None else indices
    return sum(1 for i in chosen if pred[i] == gold[i]) / len(chosen)


def class_f1(
    gold: Sequence[str], pred: Predictions, indices: Sequence[int] | None = None
) -> dict[str, float]:
    """F1 per class that appears in the selected gold labels or predictions.

    F1 = 2TP / (2TP + FP + FN), so a class with no true positives scores 0.0, never undefined.
    """
    chosen = range(len(gold)) if indices is None else indices
    tp: dict[str, int] = {}
    fp: dict[str, int] = {}
    fn: dict[str, int] = {}
    for i in chosen:
        g, p = gold[i], pred[i]
        tp.setdefault(g, 0)
        fp.setdefault(g, 0)
        fn.setdefault(g, 0)
        if p == g:
            tp[g] += 1
            continue
        fn[g] += 1
        if p is not None:
            tp.setdefault(p, 0)
            fn.setdefault(p, 0)
            fp[p] = fp.get(p, 0) + 1
    return {c: 2 * tp[c] / (2 * tp[c] + fp.get(c, 0) + fn[c]) for c in tp}


def macro_f1(gold: Sequence[str], pred: Predictions, indices: Sequence[int] | None = None) -> float:
    """Unweighted mean of class_f1 over the classes that appear in gold or predictions."""
    scores = class_f1(gold, pred, indices)
    return sum(scores.values()) / len(scores)


def bootstrap_interval(
    statistic: Callable[[Sequence[int]], float],
    n: int,
    *,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> tuple[float, float]:
    """The 95% percentile interval of `statistic` over resamples of range(n) (needs n >= 1).

    The statistic receives the resampled indices. For a paired difference, build it from both
    models' metrics so each resample feeds the same indices to both. Deterministic for a seed.
    """
    rng = random.Random(seed)
    population = range(n)
    values = sorted(statistic(rng.choices(population, k=n)) for _ in range(resamples))
    lower = int(resamples * ALPHA / 2)  # 50 of 2000: the 2.5th percentile
    return values[lower], values[resamples - lower - 1]


def messages_per_second(seconds: Sequence[float]) -> float:
    """Attempted messages divided by the summed per-message seconds; 0.0 when nothing was timed."""
    total = sum(seconds)
    return len(seconds) / total if total > 0 else 0.0


@dataclass(frozen=True)
class Verdict:
    """The D93 rule applied to one comparison. `b_wins` is true only if all three checks pass."""

    speed_ok: bool
    topic_ok: bool
    sentiment_ok: bool

    @property
    def b_wins(self) -> bool:
        return self.speed_ok and self.topic_ok and self.sentiment_ok


def decide(a_mps: float, b_mps: float, topic_lower: float, sentiment_lower: float) -> Verdict:
    """D93: B wins only if b_mps >= 1.5 * a_mps and both lower bounds are >= -0.05."""
    return Verdict(
        speed_ok=a_mps > 0 and b_mps > 0 and b_mps >= MIN_SPEEDUP * a_mps,
        topic_ok=round(topic_lower, _FLOAT_NOISE_PLACES) >= MARGIN,
        sentiment_ok=round(sentiment_lower, _FLOAT_NOISE_PLACES) >= MARGIN,
    )
