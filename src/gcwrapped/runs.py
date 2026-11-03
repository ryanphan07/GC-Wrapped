"""Read-only view of `raw.load_runs`, for run summaries."""

from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class RunSummary:
    load_id: int
    status: str
    rows_parsed: int | None
    rows_inserted: int | None
    elapsed_seconds: float | None  # None while the run is still open


_LATEST = (
    "SELECT load_id, status, rows_parsed, rows_inserted, "
    "EXTRACT(EPOCH FROM finished_at - started_at)::float8 "
    "FROM raw.load_runs ORDER BY load_id DESC LIMIT 1"
)


def latest_run(database_url: str) -> RunSummary | None:
    """The most recent load run, or None if nothing has been ingested yet."""
    with psycopg.connect(database_url) as conn:
        row = conn.execute(_LATEST).fetchone()
    if row is None:
        return None
    return RunSummary(
        load_id=row[0],
        status=row[1],
        rows_parsed=row[2],
        rows_inserted=row[3],
        elapsed_seconds=row[4],
    )
