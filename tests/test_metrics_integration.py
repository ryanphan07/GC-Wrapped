"""`gcw metrics` against the synthetic dev database, with sentinel names and text seeded in.

The dbt and pytest steps are replaced (they need dbt packages and a nested pytest); the database
step is replaced too because the test database is already up. The warehouse step is the real one.
The dbt mart is stood in for by a throwaway schema so a real analytics.fct_messages is untouched.
"""

import psycopg
import pytest

from gcwrapped import metrics
from gcwrapped.metrics import DbtCounts, PytestCounts, Steps, WarehouseStats

pytestmark = pytest.mark.integration

PROBE_SCHEMA = "metrics_probe"


def _seed(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        run = conn.execute(
            "INSERT INTO raw.load_runs (status, roots_count, rows_parsed, rows_inserted, "
            "finished_at) VALUES ('succeeded', 1, 5, 4, now() + interval '2.5 seconds') "
            "RETURNING load_id"
        ).fetchone()
        assert run is not None
        rows = [
            # (key, thread, sender): 4 messages, 2 threads, 2 senders; one extra row for a third
            # sender that exists only in raw (not yet modeled).
            ("k1", "111", "SENTINELNAME a"),
            ("k2", "111", "SENTINELNAME b"),
            ("k3", "222", "SENTINELNAME a"),
            ("k4", "222", "SENTINELNAME a"),
            ("k5", "222", "SENTINELNAME c"),
        ]
        for key, thread, sender in rows:
            conn.execute(
                "INSERT INTO raw.messages (message_key, platform, thread_id, sender_name, "
                "sent_at, content, media_count, source_file, load_id) "
                "VALUES (%s, 'instagram', %s, %s, now(), 'SENTINELTEXT body', 0, 'f.json', %s)",
                (key, thread, sender, run[0]),
            )
        conn.execute(f"DROP SCHEMA IF EXISTS {PROBE_SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {PROBE_SCHEMA}")
        conn.execute(
            f"CREATE TABLE {PROBE_SCHEMA}.fct_messages AS "
            "SELECT message_key, thread_id, sender_name FROM raw.messages "
            "WHERE message_key <> 'k5'"
        )


def test_metrics_counts_are_correct_and_leak_nothing(
    migrated_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(migrated_db)
    try:
        steps = Steps(
            database=lambda: None,
            warehouse=lambda: metrics.warehouse_stats(
                migrated_db, modeled_messages=(PROBE_SCHEMA, "fct_messages")
            ),
            dbt=lambda: DbtCounts(1, 2, 1, 1, 0, 1, 0, 0),
            pytest=lambda: PytestCounts(3, 4),
        )

        assert metrics.run(steps) == 0
    finally:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {PROBE_SCHEMA} CASCADE")

    out = capsys.readouterr().out
    lines = out.splitlines()
    assert "target database: gcw_test" in lines
    assert "warehouse raw messages: 5" in lines
    assert "warehouse modeled messages: 4" in lines
    assert "warehouse threads: 2" in lines
    assert "warehouse distinct senders: 2" in lines
    assert "latest load status: succeeded" in lines
    assert "latest load rows parsed: 5" in lines
    assert "latest load rows inserted: 4" in lines
    assert any(line.startswith("latest load seconds: 2.") for line in lines)
    assert lines[-1] == "metrics: ok"
    assert "sentinel" not in out.lower()


def test_warehouse_stats_with_no_load_run_fails_by_type_name(
    migrated_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    def stats() -> WarehouseStats:
        return metrics.warehouse_stats(migrated_db, modeled_messages=("raw", "messages"))

    steps = Steps(
        database=lambda: None,
        warehouse=stats,
        dbt=lambda: DbtCounts(0, 0, 0, 0, 0, 0, 0, 0),
        pytest=lambda: PytestCounts(0, 0),
    )

    assert metrics.run(steps) == 1
    assert capsys.readouterr().out == "metrics: failed at warehouse (NoLoadRunError)\n"
