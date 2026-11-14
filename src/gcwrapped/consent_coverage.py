"""`make consent-coverage`: how much of the chat the loaded consent ledger covers (D88).

Two outputs with different privacy levels:
- stdout: fixed aggregate lines (database name and counts only), each checked with fullmatch before
  anything is printed. Safe to paste.
- `consent_priorities.csv` in GCW_LABEL_DIR: senders ranked by text-message count, i.e. whose reply
  would unlock the most messages. It holds real names, so it is written only to the private label
  directory (outside the repo, checked like the labeling tool's), atomically with mode 0600, and
  its contents are never printed.

It reads the loaded ledger (raw.sender_consent) and the eligibility view, not the CSV, so it shows
what the pipeline will actually use. "Text message" means non-blank content, the same rule as the
labeling source. A sender is a (thread, display name) pair, because the ledger is per thread.
"""

import csv
import os
import re
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg

from gcwrapped.config import LabelSettings
from gcwrapped.db import database_name, database_url
from gcwrapped.docker import start_database
from gcwrapped.labeling.commands import LABEL_DIR_MESSAGE, LabelDirError, resolve_label_dir
from gcwrapped.ledger import warehouse_members
from gcwrapped.metrics import ALLOWED_DATABASES, UnexpectedDatabaseError

PRIORITIES_FILE = "consent_priorities.csv"
PRIORITIES_HEADER = (
    "rank",
    "thread_id",
    "thread_title",
    "sender_name",
    "text_messages",
    "cumulative_share",
    "consent",
)
MISSING = "missing"  # a sender in the warehouse with no ledger row
SHARE_TARGETS = (50, 80, 90)

STEP_DATABASE = "database"
STEP_LABEL_DIR = "label directory"
STEP_DOCKER = "docker"
STEP_QUERY = "warehouse"
STEP_WRITE = "write"
STEP_RENDER = "render"

_DATABASES = "|".join(sorted(ALLOWED_DATABASES))
LINE_SHAPES = (
    rf"target database: ({_DATABASES})",
    r"text messages: \d+",
    r"ledger rows: \d+",
    r"consent yes: \d+",
    r"consent local: \d+",
    r"consent no: \d+",
    r"consent blank: \d+",
    r"senders in the warehouse missing from the ledger: \d+",
    r"local-eligible text messages: \d+",
    r"external-eligible text messages: \d+",
    r"threads with a local-eligible message: \d+ of \d+",
    *(rf"senders needed for {p}% of text messages: \d+" for p in SHARE_TARGETS),
    r"consent-coverage: ok",
)
_FAILED_AT = (
    r"consent-coverage: failed at "
    r"(database|label directory|docker|warehouse|write|render) "
    r"\([A-Za-z_][A-Za-z0-9_]*\)"
)

# One row per (thread, sender) that wrote text. consent is NULL when the ledger has no row.
SENDERS_SQL = """
SELECT m.thread_id,
       coalesce(max(m.thread_title), ''),
       m.sender_name,
       count(*),
       count(*) FILTER (WHERE e.local_ok),
       count(*) FILTER (WHERE e.external_ok),
       max(c.consent)
FROM raw.messages AS m
LEFT JOIN analytics.fct_message_eligibility AS e USING (message_key)
LEFT JOIN raw.sender_consent AS c
    ON c.thread_id = m.thread_id AND c.sender_name = m.sender_name
WHERE coalesce(m.content ~ '\\S', false)
GROUP BY m.thread_id, m.sender_name
"""


class ShapeError(Exception):
    """A rendered line did not match its allowed shape. Only the class name is ever printed."""


@dataclass(frozen=True)
class SenderRow:
    thread_id: str
    thread_title: str
    sender_name: str
    text_messages: int
    local_messages: int  # of the text messages, those with local_ok
    external_messages: int  # of the text messages, those with external_ok
    consent: str  # "yes", "local", "no", "" or MISSING


@dataclass(frozen=True)
class Coverage:
    database: str
    text_messages: int
    ledger_rows: int
    yes: int
    local: int
    no: int
    blank: int
    missing_senders: int
    local_messages: int
    external_messages: int
    threads_local: int
    threads_total: int
    needed: dict[int, int]  # share target (percent) -> senders needed
    ranked: list[SenderRow]


def rank_senders(rows: Sequence[SenderRow]) -> list[SenderRow]:
    """Most text messages first; ties by thread then name, so the order is deterministic."""
    return sorted(rows, key=lambda r: (-r.text_messages, r.thread_id, r.sender_name))


def senders_needed(ranked: Sequence[SenderRow], percent: int) -> int:
    """How many of the top-ranked senders cover at least `percent`% of all text messages."""
    total = sum(r.text_messages for r in ranked)
    running = 0
    for count, row in enumerate(ranked, start=1):
        running += row.text_messages
        if running * 100 >= total * percent:
            return count
    return 0  # no text at all


def priority_rows(ranked: Sequence[SenderRow]) -> list[list[str]]:
    total = sum(r.text_messages for r in ranked)
    running = 0
    out: list[list[str]] = []
    for rank, row in enumerate(ranked, start=1):
        running += row.text_messages
        share = running / total if total else 0.0
        out.append(
            [
                str(rank),
                row.thread_id,
                row.thread_title,
                row.sender_name,
                str(row.text_messages),
                f"{share:.4f}",
                row.consent,
            ]
        )
    return out


def write_priorities(path: Path, ranked: Sequence[SenderRow]) -> None:
    """Replace `path` atomically with mode 0600: temp file in the same directory, then rename."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".priorities-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(PRIORITIES_HEADER)
            writer.writerows(priority_rows(ranked))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def build_coverage(url: str, database: str) -> Coverage:
    """Aggregate and rank inside Python from per-sender counts; names never leave this module."""
    with psycopg.connect(url) as conn:
        found = conn.execute(SENDERS_SQL).fetchall()
        ledger_values = [r[0] for r in conn.execute("SELECT consent FROM raw.sender_consent")]
        ledger_keys = {
            (str(t), str(s))
            for t, s in conn.execute("SELECT thread_id, sender_name FROM raw.sender_consent")
        }
        threads_row = conn.execute("SELECT count(DISTINCT thread_id) FROM raw.messages").fetchone()
        local_threads_row = conn.execute(
            "SELECT count(DISTINCT m.thread_id) FROM raw.messages AS m "
            "JOIN analytics.fct_message_eligibility AS e USING (message_key) "
            "WHERE e.local_ok AND coalesce(m.content ~ '\\S', false)"
        ).fetchone()
    if threads_row is None or local_threads_row is None:
        raise ShapeError
    rows = [
        SenderRow(
            str(thread),
            str(title),
            str(sender),
            int(texts),
            int(local),
            int(external),
            MISSING if consent is None else str(consent),
        )
        for thread, title, sender, texts, local, external, consent in found
    ]
    ranked = rank_senders(rows)
    members = warehouse_members(url)
    return Coverage(
        database=database,
        text_messages=sum(r.text_messages for r in ranked),
        ledger_rows=len(ledger_values),
        yes=ledger_values.count("yes"),
        local=ledger_values.count("local"),
        no=ledger_values.count("no"),
        blank=ledger_values.count(""),
        missing_senders=sum(1 for key in members if key not in ledger_keys),
        local_messages=sum(r.local_messages for r in ranked),
        external_messages=sum(r.external_messages for r in ranked),
        threads_local=int(local_threads_row[0]),
        threads_total=int(threads_row[0]),
        needed={p: senders_needed(ranked, p) for p in SHARE_TARGETS},
        ranked=ranked,
    )


def render(c: Coverage) -> list[str]:
    lines = [
        f"target database: {c.database}",
        f"text messages: {c.text_messages}",
        f"ledger rows: {c.ledger_rows}",
        f"consent yes: {c.yes}",
        f"consent local: {c.local}",
        f"consent no: {c.no}",
        f"consent blank: {c.blank}",
        f"senders in the warehouse missing from the ledger: {c.missing_senders}",
        f"local-eligible text messages: {c.local_messages}",
        f"external-eligible text messages: {c.external_messages}",
        f"threads with a local-eligible message: {c.threads_local} of {c.threads_total}",
        *(f"senders needed for {p}% of text messages: {c.needed[p]}" for p in SHARE_TARGETS),
        "consent-coverage: ok",
    ]
    if len(lines) != len(LINE_SHAPES):
        raise ShapeError
    for line, shape in zip(lines, LINE_SHAPES, strict=True):
        if re.fullmatch(shape, line) is None:
            raise ShapeError
    return lines


def _fail_at(step: str, exc: BaseException) -> int:
    line = f"consent-coverage: failed at {step} ({type(exc).__name__})"
    if re.fullmatch(_FAILED_AT, line) is None:  # only a strange exception name can land here
        line = f"consent-coverage: failed at {step} (Exception)"
    print(line)
    return 1


def run(start_db: Callable[[], None] = start_database) -> int:
    """Guards first (database name, label directory), then the database is started and read."""
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
        step = STEP_QUERY
        coverage = build_coverage(url, database)
        step = STEP_WRITE
        write_priorities(label_dir / PRIORITIES_FILE, coverage.ranked)
        step = STEP_RENDER
        lines = render(coverage)
    except LabelDirError:
        print(LABEL_DIR_MESSAGE)
        return 1
    except Exception as exc:  # type name only: driver and OS messages can echo values
        return _fail_at(step, exc)
    print(*lines, sep="\n")
    return 0
