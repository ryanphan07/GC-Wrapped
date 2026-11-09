"""Per-sender consent ledger (D69): who may have their text processed outside this machine.

A UTF-8 CSV kept outside the repo (GCW_CONSENT_LEDGER), one row per (thread_id, sender display
name), consent "yes", "no" or blank (blank means no). `make consent-template` creates and extends
it; `make consent-load` validates the whole file and replaces raw.sender_consent in one
transaction. dbt's fct_message_eligibility is the one place that turns it into a per-message
`external_ok`. The thread allowlist (consent.py) still gates ingestion; this is the second gate.

Privacy: both commands print fixed labels, numbers, a line number per validation error and
exception TYPE names. Never a sender name, a ledger path or message text. Every line is checked
against a fullmatch pattern before anything is printed, as in metrics.py.
"""

import csv
import os
import re
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import psycopg

from gcwrapped.config import LedgerSettings
from gcwrapped.consent import require_allowlist
from gcwrapped.db import database_name, database_url
from gcwrapped.docker import start_database
from gcwrapped.metrics import ALLOWED_DATABASES
from gcwrapped.migrate import migrate

HEADER = ("consent", "thread_id", "sender_name", "messages")
CONSENT_VALUES = ("yes", "no", "")
REAL_DATABASE = "gcw_real"
MAX_ERROR_LINES = 20
REPO_ROOT = Path(__file__).resolve().parents[2]

KIND_BAD_HEADER = "bad_header"
KIND_BAD_VALUE = "bad_value"
KIND_DUPLICATE = "duplicate"
KIND_UNKNOWN_THREAD = "unknown_thread"
KIND_UNKNOWN_SENDER = "unknown_sender"

STEP_LEDGER_PATH = "ledger path"
STEP_DATABASE = "database"
STEP_ALLOWLIST = "allowlist"
STEP_WAREHOUSE = "warehouse"
STEP_LEDGER = "ledger"
STEP_MIGRATE = "migrate"
STEP_LOAD = "load"
STEP_WRITE = "write"
STEP_RENDER = "render"

_DB = r"target database: (gcw_dev|gcw_real|gcw_ci|gcw_test)"
TEMPLATE_SHAPES = (
    _DB,
    r"ledger rows: \d+",
    r"new rows added: \d+",
    r"rows with a decision kept: \d+",
    r"stale rows: \d+",
    r"consent-template: ok",
)
LOAD_SHAPES = (
    _DB,
    r"ledger rows: \d+",
    r"consented \(yes\): \d+",
    r"declined \(no\): \d+",
    r"undecided \(blank, treated as no\): \d+",
    r"consent-load: ok",
)
# Failure lines, for both commands. Step names and exception type names only.
ERROR_LINE_SHAPE = (
    r"ledger error: (bad_header|bad_value|duplicate|unknown_thread|unknown_sender) at line \d+"
)
ERROR_COUNT_SHAPE = r"ledger errors: \d+"
FAILED_SHAPE = r"consent-(template|load): failed"
FAILED_AT_SHAPE = (
    r"consent-(template|load): failed at "
    r"(ledger path|database|allowlist|warehouse|ledger|migrate|load|write|render) "
    r"\([A-Za-z_][A-Za-z0-9_]*\)"
)


class LedgerError(Exception):
    """Base of ledger precondition failures. Only the class name is ever printed."""


class LedgerPathError(LedgerError):
    """GCW_CONSENT_LEDGER is unset or empty, or a real-database run points inside the repo."""


class UnexpectedDatabaseError(LedgerError):
    """The target database is not one of the known names."""


class ShapeError(LedgerError):
    """A rendered line did not match its allowed shape, or the line count was wrong."""


@dataclass(frozen=True)
class LedgerRow:
    consent: str  # "yes", "no" or "" (undecided; treated as no)
    thread_id: str
    sender_name: str
    messages: str  # informational only; kept as written so stale rows round-trip unchanged

    @property
    def key(self) -> tuple[str, str]:
        return (self.thread_id, self.sender_name)


@dataclass(frozen=True)
class Problem:
    kind: str
    line: int

    def render(self) -> str:
        return f"ledger error: {self.kind} at line {self.line}"


@dataclass(frozen=True)
class LedgerCheck:
    rows: list[LedgerRow]  # rows without a problem
    problems: list[Problem]  # one per bad row at most, in file order


@dataclass(frozen=True)
class MergeResult:
    rows: list[LedgerRow]
    new: int
    decisions_kept: int
    stale: int


@dataclass(frozen=True)
class Counts:
    rows: int
    yes: int
    no: int
    blank: int


Members = Mapping[tuple[str, str], int]  # (thread_id, sender_name) -> message count


# --- Reading and validating ------------------------------------------------------------------


def check_ledger(
    path: Path,
    *,
    allowlist: frozenset[str] | None = None,
    members: Members | None = None,
) -> LedgerCheck:
    """Read the whole file and report every problem; a row gets at most one, in this order:
    bad_value, duplicate, unknown_thread (when `allowlist` is given), unknown_sender (when
    `members` is given). A wrong header or an undecodable file is a single bad_header at line 1.

    Raises OSError if the file cannot be opened; callers print the type name only.
    """
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            records = [(reader.line_num, record) for record in reader]
    except (UnicodeDecodeError, csv.Error):
        return LedgerCheck([], [Problem(KIND_BAD_HEADER, 1)])
    if not records or tuple(records[0][1]) != HEADER:
        return LedgerCheck([], [Problem(KIND_BAD_HEADER, 1)])

    rows: list[LedgerRow] = []
    problems: list[Problem] = []
    seen: set[tuple[str, str]] = set()
    for line, record in records[1:]:
        if not record:  # a blank line
            continue
        kind = _row_problem(record, seen, allowlist, members)
        if kind is not None:
            problems.append(Problem(kind, line))
            continue
        row = LedgerRow(*record)
        seen.add(row.key)
        rows.append(row)
    return LedgerCheck(rows, problems)


def _row_problem(
    record: list[str],
    seen: set[tuple[str, str]],
    allowlist: frozenset[str] | None,
    members: Members | None,
) -> str | None:
    if len(record) != len(HEADER):
        return KIND_BAD_VALUE
    consent, thread_id, sender_name, _messages = record
    if consent not in CONSENT_VALUES or not thread_id or not sender_name:
        return KIND_BAD_VALUE
    if (thread_id, sender_name) in seen:
        return KIND_DUPLICATE
    if allowlist is not None and thread_id not in allowlist:
        return KIND_UNKNOWN_THREAD
    if members is not None and (thread_id, sender_name) not in members:
        return KIND_UNKNOWN_SENDER
    return None


# --- Merging and writing ---------------------------------------------------------------------


def _message_count(row: LedgerRow) -> int:
    return int(row.messages) if row.messages.isascii() and row.messages.isdigit() else 0


def merge(existing: Iterable[LedgerRow], members: Members) -> MergeResult:
    """Existing rows keep their consent; senders new to the warehouse are added with blank consent.

    Rows no longer in the warehouse are kept and counted as stale; nothing is ever deleted. The
    informational message count of a row still in the warehouse is refreshed. Rows are sorted by
    thread_id, then message count descending (then name, so the order is deterministic).
    """
    kept = list(existing)
    known = {row.key for row in kept}
    merged: list[LedgerRow] = []
    stale = 0
    for row in kept:
        if row.key in members:
            merged.append(replace(row, messages=str(members[row.key])))
        else:
            stale += 1
            merged.append(row)
    added = [
        LedgerRow("", thread_id, sender_name, str(count))
        for (thread_id, sender_name), count in members.items()
        if (thread_id, sender_name) not in known
    ]
    merged.extend(added)
    merged.sort(key=lambda r: (r.thread_id, -_message_count(r), r.sender_name))
    return MergeResult(
        rows=merged,
        new=len(added),
        decisions_kept=sum(1 for row in kept if row.consent != ""),
        stale=stale,
    )


def write_ledger(path: Path, rows: Iterable[LedgerRow]) -> None:
    """Write atomically with mode 0600: temp file in the same directory, fsync, then rename.

    The rename means a crash leaves either the old ledger or the new one, never half of one. The
    temp file is created 0600 (mkstemp) so there is no window where the names are world-readable.
    """
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".ledger-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(HEADER)
            for row in rows:
                writer.writerow([row.consent, row.thread_id, row.sender_name, row.messages])
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# --- Warehouse -------------------------------------------------------------------------------


def warehouse_members(url: str) -> dict[tuple[str, str], int]:
    """(thread_id, sender display name) -> message count, from the dim_members mart.

    Members with 0 messages (people who only reacted) are left out: nothing they wrote could be
    sent anywhere, so they need no decision.
    """
    with psycopg.connect(url) as conn:
        found = conn.execute(
            "SELECT thread_id, member_name, message_count FROM analytics.dim_members "
            "WHERE message_count > 0"
        ).fetchall()
    return {(thread_id, name): int(count) for thread_id, name, count in found}


def replace_consent(url: str, rows: Iterable[LedgerRow]) -> None:
    """Replace raw.sender_consent in one transaction (delete, then insert): all or nothing.

    `with connect()` commits on a clean exit and rolls back on any exception, so a failed insert
    leaves the previous contents in place. Re-running with the same rows gives the same table.
    """
    with psycopg.connect(url) as conn:
        conn.execute("DELETE FROM raw.sender_consent")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO raw.sender_consent (thread_id, sender_name, consent) "
                "VALUES (%s, %s, %s)",
                [(row.thread_id, row.sender_name, row.consent) for row in rows],
            )


# --- Commands --------------------------------------------------------------------------------


def count_consent(rows: Iterable[LedgerRow]) -> Counts:
    listed = list(rows)
    return Counts(
        rows=len(listed),
        yes=sum(1 for r in listed if r.consent == "yes"),
        no=sum(1 for r in listed if r.consent == "no"),
        blank=sum(1 for r in listed if r.consent == ""),
    )


def resolve_ledger_path(raw: str, database: str) -> Path:
    """The configured ledger path. Raises LedgerPathError if it is empty, or if the target is the
    real database and the path resolves inside the repo (a real ledger must live outside it)."""
    if not raw.strip():
        raise LedgerPathError
    path = Path(os.path.realpath(os.path.expanduser(raw.strip())))
    if database == REAL_DATABASE and (path == REPO_ROOT or REPO_ROOT in path.parents):
        raise LedgerPathError
    return path


def _target_database() -> tuple[str, str]:
    """(connection URL, database name) of the target; nothing is started or connected yet."""
    url = database_url()
    name = database_name(url)
    if name not in ALLOWED_DATABASES:
        raise UnexpectedDatabaseError
    return url, name


def _ledger_path(database: str) -> Path:
    return resolve_ledger_path(LedgerSettings().gcw_consent_ledger, database)


def _check_shapes(lines: list[str], shapes: tuple[str, ...]) -> None:
    if len(lines) != len(shapes):
        raise ShapeError
    for line, shape in zip(lines, shapes, strict=True):
        if re.fullmatch(shape, line) is None:
            raise ShapeError


def _failure_lines(command: str, problems: list[Problem]) -> list[str]:
    shown = [p.render() for p in problems[:MAX_ERROR_LINES]]
    lines = [*shown, f"ledger errors: {len(problems)}", f"{command}: failed"]
    allowed = (ERROR_LINE_SHAPE, ERROR_COUNT_SHAPE, FAILED_SHAPE)
    for line in lines:
        if not any(re.fullmatch(shape, line) for shape in allowed):
            raise ShapeError
    return lines


def _fail_at(command: str, step: str, exc: BaseException) -> int:
    line = f"{command}: failed at {step} ({type(exc).__name__})"
    if re.fullmatch(FAILED_AT_SHAPE, line) is None:  # only a weird exception name can land here
        line = f"{command}: failed at {step} (Exception)"
    print(line)
    return 1


def run_template(start_db: Callable[[], None] = start_database) -> int:
    """`make consent-template`: create the ledger, or add this warehouse's missing senders to it."""
    command = "consent-template"
    step = STEP_DATABASE
    try:
        url, database = _target_database()
        step = STEP_LEDGER_PATH
        path = _ledger_path(database)
        step = STEP_DATABASE
        start_db()
        step = STEP_WAREHOUSE
        members = warehouse_members(url)
        step = STEP_LEDGER
        existing: list[LedgerRow] = []
        if path.exists():
            check = check_ledger(path)
            if check.problems:
                print(*_failure_lines(command, check.problems), sep="\n")
                return 1
            existing = check.rows
        step = STEP_WRITE
        merged = merge(existing, members)
        write_ledger(path, merged.rows)
        step = STEP_RENDER
        lines = [
            f"target database: {database}",
            f"ledger rows: {len(merged.rows)}",
            f"new rows added: {merged.new}",
            f"rows with a decision kept: {merged.decisions_kept}",
            f"stale rows: {merged.stale}",
            "consent-template: ok",
        ]
        _check_shapes(lines, TEMPLATE_SHAPES)
    except Exception as exc:  # type name only: driver and OS messages can echo values
        return _fail_at(command, step, exc)
    print(*lines, sep="\n")
    return 0


def run_load(start_db: Callable[[], None] = start_database) -> int:
    """`make consent-load`: validate the whole ledger, then replace raw.sender_consent."""
    command = "consent-load"
    step = STEP_DATABASE
    try:
        url, database = _target_database()
        step = STEP_LEDGER_PATH
        path = _ledger_path(database)
        step = STEP_DATABASE
        start_db()
        step = STEP_ALLOWLIST
        allowlist = require_allowlist()
        step = STEP_WAREHOUSE
        members = warehouse_members(url)
        step = STEP_LEDGER
        check = check_ledger(path, allowlist=allowlist, members=members)
        if check.problems:
            print(*_failure_lines(command, check.problems), sep="\n")
            return 1
        # Validation passed; only now touch the database. Applying pending migrations is what
        # `gcw migrate` does and creates the table on a database that predates it (D74).
        step = STEP_MIGRATE
        migrate(url)
        step = STEP_LOAD
        replace_consent(url, check.rows)
        step = STEP_RENDER
        counts = count_consent(check.rows)
        lines = [
            f"target database: {database}",
            f"ledger rows: {counts.rows}",
            f"consented (yes): {counts.yes}",
            f"declined (no): {counts.no}",
            f"undecided (blank, treated as no): {counts.blank}",
            "consent-load: ok",
        ]
        _check_shapes(lines, LOAD_SHAPES)
    except Exception as exc:
        return _fail_at(command, step, exc)
    print(*lines, sep="\n")
    return 0
