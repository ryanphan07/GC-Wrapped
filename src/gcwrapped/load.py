"""Load parsed messages into `raw.messages`, idempotently.

See decisions.md, "Idempotent bulk load".

One load = one `raw.load_runs` row + one transaction:
  1. COPY the parsed rows into a temp table (fast bulk path);
  2. INSERT ... SELECT into raw.messages ON CONFLICT (message_key) DO NOTHING;
  3. mark the run succeeded, in the same transaction as the messages.

The run row is inserted first and committed on its own, so if the load fails and rolls back,
the run can still be marked failed from a separate transaction.

Privacy: logs, run rows and exceptions raised here carry counts, ids and exception type names only.
Any exception during a load is replaced by a `LoadError` (see below): exception text can echo row
data into the Postgres server log, `raw.load_runs.error` and whatever gets pasted while debugging.
"""

import json
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import psycopg

from gcwrapped.models import ParsedMessage, Reaction

logger = logging.getLogger(__name__)

# Column order of the COPY tuples; must match _COPY and _CREATE_STAGING below.
COLUMNS = (
    "message_key",
    "platform",
    "thread_id",
    "thread_title",
    "sender_name",
    "sent_at",
    "content",
    "media_count",
    "share_url",
    "reactions",
    "source_file",
)

_CREATE_STAGING = """
CREATE TEMP TABLE staging_messages (
    message_key  text,
    platform     text,
    thread_id    text,
    thread_title text,
    sender_name  text,
    sent_at      timestamptz,
    content      text,
    media_count  integer,
    share_url    text,
    reactions    jsonb,
    source_file  text
) ON COMMIT DROP
"""

_COPY = """
COPY staging_messages (
    message_key, platform, thread_id, thread_title, sender_name, sent_at,
    content, media_count, share_url, reactions, source_file
) FROM STDIN
"""

_MERGE = """
INSERT INTO raw.messages (
    message_key, platform, thread_id, thread_title, sender_name, sent_at,
    content, media_count, share_url, reactions, source_file, load_id
)
SELECT
    message_key, platform, thread_id, thread_title, sender_name, sent_at,
    content, media_count, share_url, reactions, source_file, %s
FROM staging_messages
ON CONFLICT (message_key) DO NOTHING
"""


class LoadError(Exception):
    """A load failed. Carries the cause's class name, row index and field name; never any value.

    `row_index` is the 0-based index of the message being processed, or None when the failure
    happened outside the per-row loop (flushing the COPY, or the merge). `field` is None when it
    can't be determined without reading data.
    """

    def __init__(self, cause: str, row_index: int | None, field: str | None) -> None:
        self.cause = cause
        self.row_index = row_index
        self.field = field
        summary = cause
        if row_index is not None:
            summary += f" at row {row_index}"
        if field is not None:
            summary += f" in field {field}"
        self.summary = summary
        super().__init__(summary)


# psycopg server errors carry "COPY staging_messages, line 3, column content: "<value>"" in
# diag.context. We only ever take the column name out of it, and only if it is a known column.
_CONTEXT_COLUMN = re.compile(r"column (\w+)")


def _field_of(exc: BaseException) -> str | None:
    if not isinstance(exc, psycopg.Error) or exc.diag.context is None:
        return None
    match = _CONTEXT_COLUMN.search(exc.diag.context)
    return match.group(1) if match and match.group(1) in COLUMNS else None


def _scrub(exc: Exception, row_index: int | None) -> LoadError:
    return LoadError(type(exc).__name__, row_index, _field_of(exc))


@dataclass(frozen=True)
class LoadResult:
    load_id: int
    rows_parsed: int
    rows_inserted: int

    @property
    def duplicates_skipped(self) -> int:
        """Parsed rows that were already in raw.messages."""
        return self.rows_parsed - self.rows_inserted


def reactions_json(reactions: Sequence[Reaction]) -> str:
    """Serialise reactions for the jsonb column. Keeps emoji as real characters, not \\u escapes."""
    return json.dumps([r.model_dump() for r in reactions], ensure_ascii=False)


def message_to_row(msg: ParsedMessage) -> tuple[Any, ...]:
    """One COPY tuple, in `COLUMNS` order."""
    return (
        msg.message_key,
        msg.platform,
        msg.thread_id,
        msg.thread_title,
        msg.sender_name,
        msg.sent_at,
        msg.content,
        msg.media_count,
        msg.share_url,
        reactions_json(msg.reactions),
        msg.source_file,
    )


def _start_run(conn: psycopg.Connection[Any], roots_count: int) -> int:
    row = conn.execute(
        "INSERT INTO raw.load_runs (roots_count) VALUES (%s) RETURNING load_id", (roots_count,)
    ).fetchone()
    assert row is not None  # INSERT ... RETURNING always yields a row
    return int(row[0])


def _copy_and_merge(
    conn: psycopg.Connection[Any], load_id: int, messages: Iterable[ParsedMessage]
) -> tuple[int, int]:
    """Stage, merge. Returns (rows_parsed, rows_inserted). Runs inside the caller's transaction.

    Raises LoadError only. The per-row try sits inside the COPY block on purpose: when an exception
    leaves `cur.copy`, psycopg sends its text to the server as the reason the COPY was cancelled,
    and the server writes that to its log. Raising a LoadError there keeps the log value-free.
    """
    parsed = 0
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_STAGING)
            with cur.copy(_COPY) as copy:
                try:
                    for msg in messages:
                        copy.write_row(message_to_row(msg))
                        parsed += 1
                except Exception as exc:
                    raise _scrub(exc, parsed) from None
            cur.execute(_MERGE, (load_id,))
            inserted = cur.rowcount
    except LoadError:
        raise
    except Exception as exc:  # COPY flush or merge: server errors can quote the failing row
        raise _scrub(exc, None) from None
    return parsed, inserted


def _mark_failed(database_url: str, load_id: int, exc: BaseException) -> None:
    """Record the failure from a fresh connection: the load transaction has rolled back."""
    summary = exc.summary if isinstance(exc, LoadError) else type(exc).__name__
    try:
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(
                "UPDATE raw.load_runs SET status = 'failed', finished_at = now(), error = %s "
                "WHERE load_id = %s",
                (summary, load_id),  # never exception text: it can echo data
            )
    except Exception:
        logger.error("could not mark load failed load_id=%s", load_id)


def load_messages(
    database_url: str, messages: Iterable[ParsedMessage], *, roots_count: int
) -> LoadResult:
    """Load messages into raw.messages. Safe to repeat: existing message_keys are skipped.

    `messages` is consumed lazily, so a parse error mid-stream rolls the whole load back and
    marks the run failed. Failures are raised as `LoadError`; the original text is dropped.
    """
    with psycopg.connect(database_url, autocommit=True) as conn:
        load_id = _start_run(conn, roots_count)
        try:
            with conn.transaction():
                parsed, inserted = _copy_and_merge(conn, load_id, messages)
                conn.execute(
                    "UPDATE raw.load_runs SET status = 'succeeded', finished_at = now(), "
                    "rows_parsed = %s, rows_inserted = %s WHERE load_id = %s",
                    (parsed, inserted, load_id),
                )
        except BaseException as exc:  # includes KeyboardInterrupt, so no run is left 'running'
            _mark_failed(database_url, load_id, exc)
            raise
    result = LoadResult(load_id=load_id, rows_parsed=parsed, rows_inserted=inserted)
    logger.info(
        "load succeeded load_id=%s rows_parsed=%s rows_inserted=%s duplicates_skipped=%s",
        load_id,
        parsed,
        inserted,
        result.duplicates_skipped,
    )
    return result
