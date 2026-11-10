"""`make label-tags`, `make label-questions`, `make label-summary` (D86).

Guards, in order: the database must be one of ALLOWED_DATABASES (the guard every real-data target
uses), and GCW_LABEL_DIR must be set, exist and resolve outside the repo (so a label file, which
holds Ryan's own question text, can never be committed by accident). Only then is the database
started and read.

Output: the flows print real message text to the terminal by design. Everything else this module
prints is a fixed message, a fixed label with a number, or `<command>: failed at <step> (<exception
type>)`; never a path, a name or str(exc). Nothing is logged.
"""

import os
import re
from collections.abc import Callable
from pathlib import Path

from gcwrapped.config import LabelSettings
from gcwrapped.db import database_name, database_url
from gcwrapped.docker import start_database
from gcwrapped.labeling import summary
from gcwrapped.labeling.flows import Clock, FlowEnd, run_questions, run_tags, utc_now
from gcwrapped.labeling.sampling import frozen_or_draw
from gcwrapped.labeling.schema import (
    QUESTION_CANDIDATE_POOL,
    QUESTION_CANDIDATES_FILE,
    QUESTIONS_FILE,
    SAMPLE_SEED,
    SKIPS_FILE,
    TAG_LABELS_FILE,
    TAG_SAMPLE_FILE,
    TAG_SAMPLE_SIZE,
)
from gcwrapped.labeling.source import EligibleIndex, load_eligible
from gcwrapped.labeling.terminal import StdTerminal, Terminal
from gcwrapped.ledger import REPO_ROOT
from gcwrapped.metrics import ALLOWED_DATABASES, UnexpectedDatabaseError

LABEL_DIR_MESSAGE = "error: GCW_LABEL_DIR must name an existing directory outside the repository"
NO_ELIGIBLE_MESSAGE = (
    "error: no eligible messages (needs consent-load, a dbt build and consented senders)"
)

STEP_DATABASE = "database"
STEP_LABEL_DIR = "label directory"
STEP_DOCKER = "docker"
STEP_ELIGIBLE = "eligible messages"
STEP_SAMPLE = "sample"
STEP_FLOW = "labeling"
STEP_SUMMARY = "summary"
_FAILED_AT = (
    r"label-(tags|questions|summary): failed at "
    r"(database|label directory|docker|eligible messages|sample|labeling|summary) "
    r"\([A-Za-z_][A-Za-z0-9_]*\)"
)


class LabelDirError(Exception):
    """GCW_LABEL_DIR is unset, missing, not a directory, or inside the repo."""


def resolve_label_dir(raw: str) -> Path:
    """The label directory, or LabelDirError. Symlinks are resolved before the repo check."""
    if not raw.strip():
        raise LabelDirError
    path = Path(os.path.realpath(os.path.expanduser(raw.strip())))
    if not path.is_dir() or path == REPO_ROOT or REPO_ROOT in path.parents:
        raise LabelDirError
    return path


def _guards() -> tuple[str, str, Path]:
    """(database URL, database name, label directory). Connects to nothing."""
    url = database_url()
    name = database_name(url)
    if name not in ALLOWED_DATABASES:
        raise UnexpectedDatabaseError
    return url, name, resolve_label_dir(LabelSettings().gcw_label_dir)


def _fail_at(command: str, step: str, exc: BaseException) -> int:
    line = f"{command}: failed at {step} ({type(exc).__name__})"
    if re.fullmatch(_FAILED_AT, line) is None:  # only a strange exception name can land here
        line = f"{command}: failed at {step} (Exception)"
    print(line)
    return 1


def _interactive(
    command: str,
    flow: Callable[[Terminal, EligibleIndex, Path], FlowEnd],
    terminal: Terminal | None,
    start_db: Callable[[], None],
) -> int:
    step = STEP_DATABASE
    try:
        url, _, label_dir = _guards()
        step = STEP_DOCKER
        start_db()
        step = STEP_ELIGIBLE
        index = load_eligible(url)
        if len(index) == 0:
            print(NO_ELIGIBLE_MESSAGE)
            return 1
        step = STEP_FLOW
        flow(terminal or StdTerminal(), index, label_dir)
    except LabelDirError:
        print(LABEL_DIR_MESSAGE)
        return 1
    except Exception as exc:  # type name only: driver and OS messages can echo values
        return _fail_at(command, step, exc)
    return 0


def run_label_tags(
    terminal: Terminal | None = None,
    start_db: Callable[[], None] = start_database,
    clock: Clock = utc_now,
) -> int:
    """Label the frozen 200-message tag sample (drawn on the first run)."""

    def flow(term: Terminal, index: EligibleIndex, label_dir: Path) -> FlowEnd:
        sample = frozen_or_draw(
            label_dir / TAG_SAMPLE_FILE,
            index.ids_by_thread(),
            TAG_SAMPLE_SIZE,
            SAMPLE_SEED,
            "tags",
        )
        return run_tags(term, index, sample, label_dir / TAG_LABELS_FILE, clock)

    return _interactive("label-tags", flow, terminal, start_db)


def run_label_questions(
    terminal: Terminal | None = None,
    start_db: Callable[[], None] = start_database,
    clock: Clock = utc_now,
) -> int:
    """Write retrieval questions from the frozen candidates (drawn on the first run)."""

    def flow(term: Terminal, index: EligibleIndex, label_dir: Path) -> FlowEnd:
        candidates = frozen_or_draw(
            label_dir / QUESTION_CANDIDATES_FILE,
            index.ids_by_thread(),
            QUESTION_CANDIDATE_POOL,
            SAMPLE_SEED,
            "questions",
        )
        return run_questions(
            term, index, candidates, label_dir / QUESTIONS_FILE, label_dir / SKIPS_FILE, clock
        )

    return _interactive("label-questions", flow, terminal, start_db)


def run_label_summary(start_db: Callable[[], None] = start_database) -> int:
    """Print the fixed aggregate lines about the labels so far. Nothing else."""
    command = "label-summary"
    step = STEP_DATABASE
    try:
        url, database, label_dir = _guards()
        step = STEP_DOCKER
        start_db()
        step = STEP_SUMMARY
        lines = summary.render(summary.build_summary(label_dir, database, url))
    except LabelDirError:
        print(LABEL_DIR_MESSAGE)
        return 1
    except Exception as exc:
        return _fail_at(command, step, exc)
    print(*lines, sep="\n")
    return 0
