"""`make consent-prune [APPLY=1]` (D101): remove everything derived from a message that is no longer
eligible.

A message is eligible when its sender is local_ok and it is user text (D88, D95). When a sender
revokes consent (or a thread is purged, or a placeholder pattern starts to match), what was derived
from their messages must leave every store, not just be hidden:
  - enrich.message_tags rows (the same delete `make tag` runs first),
  - rows of tag_labels.csv in GCW_LABEL_DIR (hand labels),
  - gold message references in questions.csv; a question left with no gold is dropped, and so is a
    question whose source message is ineligible (a question must contain its source in its gold,
    and its wording was written while reading that message),
  - rows of predictions/*.csv (stored tag-eval predictions).
Ids-only files (the frozen samples, skips.csv) and experiment verdicts (numbers only) are untouched.

Dry run by default: the database delete runs in a transaction that is rolled back and no file is
written. APPLY commits it and rewrites each changed file atomically (a temporary file, then a
rename, mode 0600). A rerun finds nothing left. Output is fixed lines of numbers, shape-checked
before printing; a failure prints a step name and an exception type only.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import psycopg

from gcwrapped.config import LabelSettings
from gcwrapped.db import database_name, database_url
from gcwrapped.docker import start_database
from gcwrapped.labeling.commands import resolve_label_dir
from gcwrapped.labeling.files import read_questions, read_tags, write_questions, write_tags
from gcwrapped.labeling.schema import QUESTIONS_FILE, TAG_LABELS_FILE, QuestionLabel
from gcwrapped.metrics import ALLOWED_DATABASES, UnexpectedDatabaseError
from gcwrapped.migrate import migrate
from gcwrapped.tagging import evaluation
from gcwrapped.tagging.runner import PRUNE_SQL

COMMAND = "consent-prune"
STEP_DATABASE = "database"
STEP_LABEL_DIR = "label directory"
STEP_DOCKER = "docker"
STEP_PRUNE = "prune"
STEP_RENDER = "render"

_DB = "|".join(sorted(ALLOWED_DATABASES))
SHAPES = (
    rf"target database: ({_DB})",
    r"ineligible messages: \d+",
    r"tags to prune: \d+",
    r"label rows to prune: \d+",
    r"question gold references to prune: \d+",
    r"questions left with no gold: \d+",
    r"prediction rows to prune: \d+",
    r"mode: (dry run|applied)",
    r"consent-prune: ok",
)
FAILED_AT_SHAPE = (
    rf"{COMMAND}: failed at (database|label directory|docker|prune|render) "
    r"\([A-Za-z_][A-Za-z0-9_]*\)"
)
ELIGIBLE_IDS_SQL = """
SELECT e.message_key
FROM analytics.fct_message_eligibility AS e
JOIN analytics.fct_message_text AS x USING (message_key)
WHERE e.local_ok AND x.has_user_text
"""
_PREDICTIONS_FILE = re.compile(r"(?P<model>[A-Za-z0-9._-]+)__(?P<version>[0-9a-f]{12})\.csv")


class PruneError(Exception):
    """Base of consent-prune failures. Only the class name is ever printed."""


class ShapeError(PruneError):
    """A rendered line did not match its allowed shape."""


class UnexpectedPredictionsFileError(PruneError):
    """A .csv in predictions/ is not named <model id>__<prompt version>.csv."""


@dataclass(frozen=True)
class FilePlan:
    """What the private files hold that is no longer eligible, and what each file becomes."""

    referenced_ineligible: frozenset[str]
    label_rows: int
    gold_references: int
    questions_without_gold: int
    prediction_rows: int
    writes: tuple[Callable[[], None], ...]  # one per changed file, each atomic


@dataclass(frozen=True)
class PruneResult:
    ineligible_messages: int
    tags: int
    label_rows: int
    gold_references: int
    questions_without_gold: int
    prediction_rows: int
    applied: bool


def eligible_ids(conn: psycopg.Connection) -> frozenset[str]:
    return frozenset(str(key) for (key,) in conn.execute(ELIGIBLE_IDS_SQL))


def _prune_question(label: QuestionLabel, eligible: frozenset[str]) -> QuestionLabel | None:
    """The question with its ineligible gold removed, or None if it must be dropped."""
    gold = tuple(g for g in label.gold_message_ids if g in eligible)
    if not gold or label.source_message_id not in eligible:
        return None
    if gold == label.gold_message_ids:
        return label
    return QuestionLabel(
        label.question_id, label.question, gold, label.source_message_id, label.labeled_at
    )


def plan_files(label_dir: Path, eligible: frozenset[str]) -> FilePlan:
    """Read every private file (validating it), count what is ineligible, and prepare the writes."""
    referenced: set[str] = set()
    writes: list[Callable[[], None]] = []

    tags_path = label_dir / TAG_LABELS_FILE
    tags = read_tags(tags_path)
    kept_tags = [t for t in tags if t.message_id in eligible]
    referenced |= {t.message_id for t in tags if t.message_id not in eligible}
    if len(kept_tags) != len(tags):
        writes.append(partial(write_tags, tags_path, kept_tags))

    questions_path = label_dir / QUESTIONS_FILE
    questions = read_questions(questions_path)
    kept_questions: list[QuestionLabel] = []
    gold_references = 0
    without_gold = 0
    for question in questions:
        referenced |= {g for g in question.gold_message_ids if g not in eligible}
        referenced |= {question.source_message_id} - eligible
        gold_references += sum(1 for g in question.gold_message_ids if g not in eligible)
        pruned = _prune_question(question, eligible)
        if pruned is None:
            without_gold += 1
        else:
            kept_questions.append(pruned)
    if gold_references or without_gold:
        writes.append(partial(write_questions, questions_path, kept_questions))

    prediction_rows = 0
    folder = label_dir / evaluation.PREDICTIONS_DIR
    for path in sorted(folder.glob("*.csv")) if folder.is_dir() else []:
        match = _PREDICTIONS_FILE.fullmatch(path.name)
        if match is None:
            raise UnexpectedPredictionsFileError
        model_id, version = match["model"], match["version"]
        rows = evaluation.read_predictions(label_dir, model_id, version)
        kept = [r for r in rows.values() if r.message_id in eligible]
        referenced |= {m for m in rows if m not in eligible}
        if len(kept) != len(rows):
            prediction_rows += len(rows) - len(kept)
            writes.append(partial(evaluation.write_predictions, label_dir, model_id, version, kept))
    return FilePlan(
        referenced_ineligible=frozenset(referenced),
        label_rows=len(tags) - len(kept_tags),
        gold_references=gold_references,
        questions_without_gold=without_gold,
        prediction_rows=prediction_rows,
        writes=tuple(writes),
    )


def prune(url: str, label_dir: Path, *, apply: bool) -> PruneResult:
    """Count (and with `apply`, remove) every derived record of an ineligible message.

    The database delete is the one `make tag` runs; without `apply` it is rolled back, so the dry
    run count cannot drift from what APPLY removes. Files are planned (and validated) before
    anything is written, and written only after the database delete succeeded.
    """
    migrate(url)  # an unmigrated database has no enrich schema (purge-unlisted does the same)
    with psycopg.connect(url) as conn:
        eligible = eligible_ids(conn)
        tagged = {
            str(k) for (k,) in conn.execute("SELECT DISTINCT message_key FROM enrich.message_tags")
        }
        plan = plan_files(label_dir, eligible)
        tags = conn.execute(PRUNE_SQL).rowcount
        if apply:
            conn.commit()
            for write in plan.writes:
                write()
        else:
            conn.rollback()
    return PruneResult(
        ineligible_messages=len(plan.referenced_ineligible | (tagged - eligible)),
        tags=tags,
        label_rows=plan.label_rows,
        gold_references=plan.gold_references,
        questions_without_gold=plan.questions_without_gold,
        prediction_rows=plan.prediction_rows,
        applied=apply,
    )


def render(database: str, result: PruneResult) -> list[str]:
    lines = [
        f"target database: {database}",
        f"ineligible messages: {result.ineligible_messages}",
        f"tags to prune: {result.tags}",
        f"label rows to prune: {result.label_rows}",
        f"question gold references to prune: {result.gold_references}",
        f"questions left with no gold: {result.questions_without_gold}",
        f"prediction rows to prune: {result.prediction_rows}",
        f"mode: {'applied' if result.applied else 'dry run'}",
        f"{COMMAND}: ok",
    ]
    if len(lines) != len(SHAPES):
        raise ShapeError
    for line, shape in zip(lines, SHAPES, strict=True):
        if re.fullmatch(shape, line) is None:
            raise ShapeError
    return lines


def failure_line(step: str, exc: BaseException) -> str:
    line = f"{COMMAND}: failed at {step} ({type(exc).__name__})"
    if re.fullmatch(FAILED_AT_SHAPE, line) is None:  # only a weird exception name can land here
        line = f"{COMMAND}: failed at {step} (Exception)"
    return line


def run(apply: bool = False, start_db: Callable[[], None] = start_database) -> int:
    """`make consent-prune [APPLY=1]`: guards, then prune, then fixed lines."""
    step = STEP_DATABASE
    try:
        url = database_url()
        database = database_name(url)
        if database not in ALLOWED_DATABASES:
            raise UnexpectedDatabaseError
        step = STEP_LABEL_DIR
        label_dir = resolve_label_dir(LabelSettings().gcw_label_dir)
        step = STEP_DOCKER
        start_db()
        step = STEP_PRUNE
        result = prune(url, label_dir, apply=apply)
        step = STEP_RENDER
        lines = render(database, result)
    except Exception as exc:  # type name only: driver and OS messages can echo values
        print(failure_line(step, exc))
        return 1
    print(*lines, sep="\n")
    return 0
