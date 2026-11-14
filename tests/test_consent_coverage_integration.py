"""make consent-coverage against the real gcw_test database (D88).

The queries, the guards and the file run for real; only docker is replaced. The dbt views
analytics.fct_message_eligibility and analytics.dim_members are stood in for by objects with the
same definitions over the raw tables.
"""

import csv
import hashlib
import re
import stat
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from gcwrapped import consent_coverage as cc
from gcwrapped.config import get_database_settings

pytestmark = pytest.mark.integration

SENTINEL = "SENTINELTEXT"
# (thread, title, sender, content). Thread 111: Ana yes (3 texts + 1 blank), Bo local (2), Cy no
# (1), Dan blank (1). Thread 222: Eve in the ledger as yes (1); Fay is not in the ledger (2).
ROWS = [
    ("111", "Title One", "Ana", f"{SENTINEL} 1"),
    ("111", "Title One", "Ana", f"{SENTINEL} 2"),
    ("111", "Title One", "Ana", f"{SENTINEL} 3"),
    ("111", "Title One", "Ana", "   "),
    ("111", "Title One", "Bo", f"{SENTINEL} 4"),
    ("111", "Title One", "Bo", f"{SENTINEL} 5"),
    ("111", "Title One", "Cy", f"{SENTINEL} 6"),
    ("111", "Title One", "Dan", f"{SENTINEL} 7"),
    ("222", "Title Two", "Eve", f"{SENTINEL} 8"),
    ("222", "Title Two", "Fay", f"{SENTINEL} 9"),
    ("222", "Title Two", "Fay", f"{SENTINEL} 10"),
    ("333", "Title Three", "Gus", None),
]
CONSENT = [
    ("111", "Ana", "yes"),
    ("111", "Bo", "local"),
    ("111", "Cy", "no"),
    ("111", "Dan", ""),
    ("222", "Eve", "yes"),
    ("999", "Gone", "yes"),  # stale row: not in the warehouse
]


@pytest.fixture
def warehouse(migrated_db: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    labels = tmp_path / "labels"
    labels.mkdir()
    monkeypatch.setenv("GCW_LABEL_DIR", str(labels))
    get_database_settings.cache_clear()
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        conn.execute("DROP VIEW IF EXISTS analytics.fct_message_eligibility")
        conn.execute("DROP VIEW IF EXISTS analytics.dim_members")
        run = conn.execute(
            "INSERT INTO raw.load_runs (roots_count) VALUES (1) RETURNING load_id"
        ).fetchone()
        assert run is not None
        for n, (thread, title, sender, content) in enumerate(ROWS):
            conn.execute(
                "INSERT INTO raw.messages (message_key, platform, thread_id, thread_title, "
                "sender_name, sent_at, content, media_count, source_file, load_id) VALUES "
                "(%s, 'instagram', %s, %s, %s, now(), %s, 0, 'x', %s)",
                (
                    hashlib.sha256(str(n).encode()).hexdigest(),
                    thread,
                    title,
                    sender,
                    content,
                    run[0],
                ),
            )
        for thread, sender, consent in CONSENT:
            conn.execute(
                "INSERT INTO raw.sender_consent (thread_id, sender_name, consent) "
                "VALUES (%s, %s, %s)",
                (thread, sender, consent),
            )
        # Same definition as dbt/models/marts/fct_message_eligibility.sql (D88).
        conn.execute(
            "CREATE VIEW analytics.fct_message_eligibility AS "
            "SELECT m.message_key, m.thread_id, m.sender_name, "
            "coalesce(c.consent IN ('yes', 'local'), false) AS local_ok, "
            "coalesce(c.consent = 'yes', false) AS external_ok "
            "FROM raw.messages AS m LEFT JOIN raw.sender_consent AS c "
            "ON c.thread_id = m.thread_id AND c.sender_name = m.sender_name"
        )
        conn.execute(
            "CREATE VIEW analytics.dim_members AS "
            "SELECT thread_id, sender_name AS member_name, count(*)::int AS message_count "
            "FROM raw.messages GROUP BY thread_id, sender_name"
        )
    yield migrated_db
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DROP VIEW IF EXISTS analytics.fct_message_eligibility")
        conn.execute("DROP VIEW IF EXISTS analytics.dim_members")


def _no_docker() -> None:
    return None


def test_the_fixed_lines_count_the_real_tables(
    warehouse: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cc.run(_no_docker) == 0

    out = capsys.readouterr().out
    assert out.splitlines() == [
        "target database: gcw_test",
        "text messages: 10",  # 11 rows with a blank and a NULL: 12 - 2
        "ledger rows: 6",
        "consent yes: 3",
        "consent local: 1",
        "consent no: 1",
        "consent blank: 1",
        "senders in the warehouse missing from the ledger: 2",  # Fay and Gus
        "local-eligible text messages: 6",  # Ana 3, Bo 2, Eve 1
        "external-eligible text messages: 4",  # Ana 3, Eve 1
        "threads with a local-eligible message: 2 of 3",
        "senders needed for 50% of text messages: 2",  # 3, 2, 2, 1, 1, 1 of 10: 5 needed
        "senders needed for 80% of text messages: 4",  # 8 needed
        "senders needed for 90% of text messages: 5",  # 9 needed
        "consent-coverage: ok",
    ]
    for line, shape in zip(out.splitlines(), cc.LINE_SHAPES, strict=True):
        assert re.fullmatch(shape, line)
    for leaked in (SENTINEL, "Ana", "Title"):
        assert leaked not in out


def test_the_priority_file_ranks_senders_with_title_share_and_consent(
    warehouse: str, tmp_path: Path
) -> None:
    assert cc.run(_no_docker) == 0

    path = tmp_path / "labels" / cc.PRIORITIES_FILE
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with path.open(encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle))
    assert [(r["rank"], r["sender_name"], r["text_messages"], r["consent"]) for r in records] == [
        ("1", "Ana", "3", "yes"),
        ("2", "Bo", "2", "local"),
        ("3", "Fay", "2", cc.MISSING),
        ("4", "Cy", "1", "no"),
        ("5", "Dan", "1", ""),
        ("6", "Eve", "1", "yes"),
    ]
    assert records[0]["thread_title"] == "Title One"
    assert records[0]["cumulative_share"] == "0.3000"
    assert records[-1]["cumulative_share"] == "1.0000"
    assert "Gus" not in path.read_text(encoding="utf-8")  # sent no text, so not ranked


def test_an_empty_warehouse_has_zero_everywhere(
    warehouse: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with psycopg.connect(warehouse, autocommit=True) as conn:
        conn.execute("DELETE FROM raw.sender_consent")

    assert cc.run(_no_docker) == 0

    lines = capsys.readouterr().out.splitlines()
    assert "local-eligible text messages: 0" in lines
    assert "external-eligible text messages: 0" in lines
    assert "threads with a local-eligible message: 0 of 3" in lines
