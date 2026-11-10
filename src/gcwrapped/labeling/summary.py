"""`make label-summary`: fixed aggregate lines about the labels, safe to paste (D86).

The only thing Ryan pastes from the real labeling run. Every line is a fixed label plus a number
(or the database name, or a topic or sentiment name from the fixed schema), and each line is
checked with fullmatch before anything is printed. No message text, question text or ID appears.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg

from gcwrapped.labeling.files import read_questions, read_skips, read_tags
from gcwrapped.labeling.sampling import read_frozen
from gcwrapped.labeling.schema import (
    QUESTION_TARGET,
    QUESTIONS_FILE,
    SENTIMENTS,
    SKIPS_FILE,
    TAG_LABELS_FILE,
    TAG_SAMPLE_FILE,
    TAG_SAMPLE_SIZE,
    TOPICS,
)
from gcwrapped.metrics import ALLOWED_DATABASES

_DATABASES = "|".join(sorted(ALLOWED_DATABASES))
LINE_SHAPES = (
    rf"target database: ({_DATABASES})",
    r"tags labeled: \d+ of \d+",
    *(rf"topic {t}: \d+" for t in TOPICS),
    *(rf"sentiment {s}: \d+" for s in SENTIMENTS),
    r"questions answered: \d+ of \d+",
    r"questions skipped: \d+",
    r"mean gold messages per question: \d+\.\d{2}",
    r"threads covered: \d+",
    r"label-summary: ok",
)


class ShapeError(Exception):
    """A rendered line did not match its allowed shape. Only the class name is ever printed."""


@dataclass(frozen=True)
class Summary:
    database: str
    tags_labeled: int
    tags_target: int
    topics: dict[str, int]
    sentiments: dict[str, int]
    questions_answered: int
    questions_target: int
    questions_skipped: int
    mean_gold: float
    threads_covered: int


def count_threads(url: str, message_ids: Sequence[str]) -> int:
    """How many distinct threads these messages belong to, counted inside the database."""
    if not message_ids:
        return 0
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "SELECT count(DISTINCT thread_id) FROM raw.messages WHERE message_key = ANY(%s)",
            (list(message_ids),),
        ).fetchone()
    return 0 if row is None else int(row[0])


def build_summary(label_dir: Path, database: str, url: str) -> Summary:
    """Read the label files and count. Targets come from the frozen sample files when they exist."""
    tags = read_tags(label_dir / TAG_LABELS_FILE)
    questions = read_questions(label_dir / QUESTIONS_FILE)
    skips = read_skips(label_dir / SKIPS_FILE)
    tag_sample = label_dir / TAG_SAMPLE_FILE
    tags_target = len(read_frozen(tag_sample)) if tag_sample.exists() else TAG_SAMPLE_SIZE
    topics = Counter(t.topic for t in tags)
    sentiments = Counter(t.sentiment for t in tags)
    gold_total = sum(len(q.gold_message_ids) for q in questions)
    covered = sorted({t.message_id for t in tags} | {q.source_message_id for q in questions})
    return Summary(
        database=database,
        tags_labeled=len(tags),
        tags_target=tags_target,
        topics={t: topics[t] for t in TOPICS},
        sentiments={s: sentiments[s] for s in SENTIMENTS},
        questions_answered=len(questions),
        questions_target=QUESTION_TARGET,
        questions_skipped=len(skips),
        mean_gold=gold_total / len(questions) if questions else 0.0,
        threads_covered=count_threads(url, covered),
    )


def render(summary: Summary) -> list[str]:
    lines = [
        f"target database: {summary.database}",
        f"tags labeled: {summary.tags_labeled} of {summary.tags_target}",
        *(f"topic {t}: {summary.topics[t]}" for t in TOPICS),
        *(f"sentiment {s}: {summary.sentiments[s]}" for s in SENTIMENTS),
        f"questions answered: {summary.questions_answered} of {summary.questions_target}",
        f"questions skipped: {summary.questions_skipped}",
        f"mean gold messages per question: {summary.mean_gold:.2f}",
        f"threads covered: {summary.threads_covered}",
        "label-summary: ok",
    ]
    check_shapes(lines)
    return lines


def check_shapes(lines: Sequence[str]) -> None:
    """Raise ShapeError unless there is one line per pattern, in order, each fully matching."""
    if len(lines) != len(LINE_SHAPES):
        raise ShapeError
    for line, shape in zip(lines, LINE_SHAPES, strict=True):
        if re.fullmatch(shape, line) is None:
            raise ShapeError
