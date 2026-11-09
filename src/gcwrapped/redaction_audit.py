"""`make redaction-audit`: redact every ingested message in memory and report aggregates only.

Ryan runs it on the real database and reads the numbers, so it follows the `make metrics` rules
(decisions.md, 2026-11-05): every output line is a fixed label plus a number (or the database name),
each line is checked with fullmatch before anything is printed, and a failure prints one fixed line
with the step and the exception's TYPE name only. It writes nothing anywhere: no file, no table, no
log line. Redacted text is counted and thrown away.

The member directory is every sender and reactor in raw.messages, which is exactly what
analytics.dim_members is built from (D77), so the audit needs no dbt run. Messages are read per
thread in batches; pseudonym numbers restart with each batch, which does not affect any count.
"""

import re
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import psycopg

from gcwrapped.db import database_name, database_url
from gcwrapped.docker import start_database
from gcwrapped.metrics import ALLOWED_DATABASES, UnexpectedDatabaseError
from gcwrapped.redaction import (
    PLACEHOLDER_TYPES,
    MemberDirectory,
    Redactor,
    load_model,
    residual_member_tokens,
)

BATCH_SIZE = 2000

STEP_DOCKER = "docker"
STEP_DATABASE = "database"
STEP_MODEL = "model"
STEP_DIRECTORY = "directory"
STEP_SCAN = "scan"
STEP_RENDER = "render"

_DIRECTORY_SQL = """
SELECT thread_id, sender_name FROM raw.messages
UNION
SELECT m.thread_id, r.reaction ->> 'actor'
FROM raw.messages AS m
CROSS JOIN LATERAL jsonb_array_elements(m.reactions) AS r (reaction)
WHERE r.reaction ->> 'actor' IS NOT NULL
"""

_DATABASES = "|".join(sorted(ALLOWED_DATABASES))
_TYPES = "|".join(PLACEHOLDER_TYPES)
LINE_SHAPES = (
    rf"target database: ({_DATABASES})",
    r"messages scanned: \d+",
    r"messages with text: \d+",
    r"messages changed: \d+",
    *(rf"placeholders {t}: \d+" for t in PLACEHOLDER_TYPES),
    r"member-name tokens remaining: \d+",
    r"redaction seconds: \d+\.\d{2}",
)
OK_LINE = "redaction-audit: ok"
RESIDUAL_LINE = "redaction-audit: failed (residual member tokens)"
_FAILED_SHAPE = rf"redaction-audit: failed at ({STEP_DOCKER}|{STEP_DATABASE}|{STEP_MODEL}|"
_FAILED_SHAPE += rf"{STEP_DIRECTORY}|{STEP_SCAN}|{STEP_RENDER}) \([A-Za-z_][A-Za-z0-9_]*\)"


class ShapeError(Exception):
    """A rendered line did not match its allowed shape. Only the class name is ever printed."""


@dataclass(frozen=True)
class AuditReport:
    database: str
    scanned: int
    with_text: int
    changed: int
    placeholders: dict[str, int]
    residual: int
    seconds: float


@dataclass(frozen=True)
class Steps:
    """One callable per step, so tests can replace the external ones."""

    docker: Callable[[], None]
    audit: Callable[[Callable[[str], None]], AuditReport]


def _batches(
    conn: psycopg.Connection[tuple[object, ...]],
) -> Iterator[tuple[str, list[tuple[str, str | None]]]]:
    """(thread_id, [(sender_name, content), ...]) in sent order, at most BATCH_SIZE per item."""
    threads = [r[0] for r in conn.execute("SELECT DISTINCT thread_id FROM raw.messages ORDER BY 1")]
    for thread_id in threads:
        with conn.cursor(name="redaction_audit") as cur:
            cur.execute(
                "SELECT sender_name, content FROM raw.messages WHERE thread_id = %s "
                "ORDER BY sent_at, message_key",
                (thread_id,),
            )
            while rows := cur.fetchmany(BATCH_SIZE):
                yield str(thread_id), [(str(s), None if c is None else str(c)) for s, c in rows]


def audit_database(url: str, set_step: Callable[[str], None]) -> AuditReport:
    """Scan every message in the database `url` names. Reports the step it is on via set_step."""
    set_step(STEP_DATABASE)
    name = database_name(url)
    if name not in ALLOWED_DATABASES:
        raise UnexpectedDatabaseError
    set_step(STEP_MODEL)
    nlp = load_model()
    with psycopg.connect(url) as conn:
        set_step(STEP_DIRECTORY)
        directory = MemberDirectory(
            (str(t), str(n)) for t, n in conn.execute(_DIRECTORY_SQL) if n is not None
        )
        redactor = Redactor(directory, nlp)
        set_step(STEP_SCAN)
        scanned = with_text = changed = residual = 0
        placeholders: Counter[str] = Counter()
        seconds = 0.0
        for thread_id, batch in _batches(conn):
            started = time.perf_counter()
            result = redactor.redact_thread(thread_id, batch)
            seconds += time.perf_counter() - started
            for (_, original), message in zip(batch, result, strict=True):
                scanned += 1
                if not original:
                    continue
                with_text += 1
                changed += message.text != original
                placeholders.update(message.counts)
                residual += residual_member_tokens(message.text or "", directory)
    counts = {t: placeholders[t] for t in PLACEHOLDER_TYPES}
    return AuditReport(name, scanned, with_text, changed, counts, residual, seconds)


def render(report: AuditReport) -> list[str]:
    """The report lines, in order, without the final status line."""
    return [
        f"target database: {report.database}",
        f"messages scanned: {report.scanned}",
        f"messages with text: {report.with_text}",
        f"messages changed: {report.changed}",
        *(f"placeholders {t}: {report.placeholders[t]}" for t in PLACEHOLDER_TYPES),
        f"member-name tokens remaining: {report.residual}",
        f"redaction seconds: {report.seconds:.2f}",
    ]


def check_shapes(lines: list[str]) -> None:
    """Raise ShapeError unless there is one line per pattern, in order, each fully matching."""
    if len(lines) != len(LINE_SHAPES):
        raise ShapeError
    for line, shape in zip(lines, LINE_SHAPES, strict=True):
        if re.fullmatch(shape, line) is None:
            raise ShapeError


def failure_line(step: str, exc: Exception) -> str:
    line = f"redaction-audit: failed at {step} ({type(exc).__name__})"
    if re.fullmatch(_FAILED_SHAPE, line) is None:
        line = f"redaction-audit: failed at {step} (Exception)"
    return line


DEFAULT_STEPS = Steps(
    docker=start_database,
    audit=lambda set_step: audit_database(database_url(), set_step),
)


def run(steps: Steps = DEFAULT_STEPS) -> int:
    """Run the audit, shape-check every line, then print. Exit 1 on a failure or residual token."""
    step = STEP_DOCKER

    def set_step(name: str) -> None:
        nonlocal step
        step = name

    try:
        steps.docker()
        report = steps.audit(set_step)
        set_step(STEP_RENDER)
        lines = render(report)
        check_shapes(lines)
    except Exception as exc:  # type name only: messages from drivers and models can echo data
        print(failure_line(step, exc))
        return 1
    for line in lines:
        print(line)
    if report.residual > 0:
        print(RESIDUAL_LINE)
        return 1
    print(OK_LINE)
    return 0
