"""`make purge-unlisted` (D91): delete what the warehouse holds for threads that left the allowlist.

The allowlist is the ingestion consent gate, so a thread that leaves it must leave the warehouse.
Everything keyed to such a thread goes: its raw.messages (reactions live in a jsonb column of that
row), enrich.message_tags (keyed to a message) and raw.sender_consent. raw.load_runs has no thread
or message key and stays. dbt marts drop the thread on their next build.

Guards come before anything runs: the database must be in ALLOWED_DATABASES and the allowlist must
be non-empty (an empty one would purge everything). Dry run by default; `--confirm` deletes, in one
transaction, so any failure leaves every table unchanged. Before touching anything the command
reads the catalog and refuses if raw or enrich holds a table keyed by message_key or thread_id that
it does not know how to purge, so a table added later fails closed instead of silently keeping data.

Privacy: output is fixed lines of numbers, checked with fullmatch before printing. Never a thread
ID, title, sender name or message text; a failure prints a step name and an exception type only.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

import psycopg

from gcwrapped.consent import require_allowlist
from gcwrapped.db import database_name, database_url
from gcwrapped.docker import start_database
from gcwrapped.metrics import ALLOWED_DATABASES, UnexpectedDatabaseError
from gcwrapped.migrate import migrate

COMMAND = "purge-unlisted"
SCHEMAS = ("raw", "enrich")
KEY_COLUMNS = ("message_key", "thread_id")
# Every table in SCHEMAS that has a KEY_COLUMNS column; purge() deletes from exactly these.
COVERED_TABLES = frozenset({"raw.messages", "raw.sender_consent", "enrich.message_tags"})

STEP_DATABASE = "database"
STEP_ALLOWLIST = "allowlist"
STEP_PURGE = "purge"  # includes the coverage check, which runs first inside purge()
STEP_RENDER = "render"

_DB = "|".join(sorted(ALLOWED_DATABASES))
SHAPES = (
    rf"target database: ({_DB})",
    r"threads in the warehouse: \d+",
    r"threads allowlisted: \d+",
    r"threads to purge: \d+",
    r"messages: \d+",
    r"reactions: \d+",
    r"tags: \d+",
    r"consent rows: \d+",
    r"mode: (dry run|deleted)",
    r"purge-unlisted: ok",
)
FAILED_AT_SHAPE = (
    r"purge-unlisted: failed at (database|allowlist|purge|render) "
    r"\([A-Za-z_][A-Za-z0-9_]*\)"
)


class PurgeError(Exception):
    """Base of purge failures. Only the class name is ever printed."""


class UncoveredTableError(PurgeError):
    """raw or enrich has a table keyed by message or thread that this module does not purge."""


class ShapeError(PurgeError):
    """A rendered line did not match its allowed shape."""


@dataclass(frozen=True)
class PurgeResult:
    threads_in_warehouse: int
    threads_allowlisted: int
    threads_to_purge: int
    messages: int
    reactions: int
    tags: int
    consent_rows: int
    deleted: bool


def keyed_tables(conn: psycopg.Connection) -> frozenset[str]:
    """Every base table in raw and enrich with a message_key or thread_id column."""
    found = conn.execute(
        "SELECT DISTINCT c.table_schema || '.' || c.table_name "
        "FROM information_schema.columns c "
        "JOIN information_schema.tables t "
        "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
        "WHERE t.table_type = 'BASE TABLE' AND c.table_schema = ANY(%s) "
        "AND c.column_name = ANY(%s)",
        (list(SCHEMAS), list(KEY_COLUMNS)),
    ).fetchall()
    return frozenset(name for (name,) in found)


def _require_coverage(conn: psycopg.Connection) -> None:
    # A missing covered table is refused too: the counts would be wrong. purge() migrates first.
    if keyed_tables(conn) != COVERED_TABLES:
        raise UncoveredTableError


def _threads(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT thread_id FROM raw.messages UNION SELECT thread_id FROM raw.sender_consent"
    ).fetchall()
    return {thread for (thread,) in rows}


def purge(url: str, allowlist: frozenset[str], *, confirm: bool) -> PurgeResult:
    """Delete every row of a thread outside `allowlist`, or (no `confirm`) count what would go.

    Both modes run the same three deletes in one transaction, children first. Without `confirm`
    the transaction is rolled back, so the dry-run counts cannot drift from what a real run
    removes. With `confirm` it commits; any exception rolls everything back (`with connect()`).
    Counts come from the deletes themselves.
    """
    keep = sorted(allowlist)
    # Bring the database up to date first, as consent-load does: an unmigrated one (no enrich
    # schema yet) would fail the coverage check below for a reason that is not a new table.
    migrate(url)
    with psycopg.connect(url) as conn:
        _require_coverage(conn)
        in_warehouse = _threads(conn)
        tags = conn.execute(
            "DELETE FROM enrich.message_tags t USING raw.messages m "
            "WHERE m.message_key = t.message_key AND m.thread_id <> ALL(%s)",
            (keep,),
        ).rowcount
        consent = conn.execute(
            "DELETE FROM raw.sender_consent WHERE thread_id <> ALL(%s)", (keep,)
        ).rowcount
        row = conn.execute(
            "WITH gone AS ("
            "DELETE FROM raw.messages WHERE thread_id <> ALL(%s) RETURNING reactions) "
            "SELECT count(*), coalesce(sum(CASE WHEN jsonb_typeof(reactions) = 'array' "
            "THEN jsonb_array_length(reactions) ELSE 0 END), 0) FROM gone",
            (keep,),
        ).fetchone()
        if row is None:
            raise ShapeError
        messages, reactions = int(row[0]), int(row[1])
        if not confirm:
            conn.rollback()
    return PurgeResult(
        threads_in_warehouse=len(in_warehouse),
        threads_allowlisted=len(allowlist),
        threads_to_purge=len(in_warehouse - allowlist),
        messages=messages,
        reactions=reactions,
        tags=tags,
        consent_rows=consent,
        deleted=confirm,
    )


def render(database: str, result: PurgeResult) -> list[str]:
    lines = [
        f"target database: {database}",
        f"threads in the warehouse: {result.threads_in_warehouse}",
        f"threads allowlisted: {result.threads_allowlisted}",
        f"threads to purge: {result.threads_to_purge}",
        f"messages: {result.messages}",
        f"reactions: {result.reactions}",
        f"tags: {result.tags}",
        f"consent rows: {result.consent_rows}",
        f"mode: {'deleted' if result.deleted else 'dry run'}",
        "purge-unlisted: ok",
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


def run(confirm: bool = False, start_db: Callable[[], None] = start_database) -> int:
    """`make purge-unlisted [CONFIRM=1]`: guards, then purge, then fixed lines."""
    step = STEP_DATABASE
    try:
        url = database_url()
        database = database_name(url)
        if database not in ALLOWED_DATABASES:
            raise UnexpectedDatabaseError
        step = STEP_ALLOWLIST
        allowlist = require_allowlist()  # unset or empty would purge everything: refused
        step = STEP_DATABASE
        start_db()
        step = STEP_PURGE
        result = purge(url, allowlist, confirm=confirm)
        step = STEP_RENDER
        lines = render(database, result)
    except Exception as exc:  # type name only: driver and OS messages can echo values
        print(failure_line(step, exc))
        return 1
    print(*lines, sep="\n")
    return 0
