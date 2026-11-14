"""The labeling tool against the real gcw_test database (D85, D86).

The SQL, the guard and the files run for real; only docker (the db start) is replaced, and the dbt
view analytics.fct_message_eligibility is stood in for by a view with the same definition over the
raw tables (the dbt model is a join of staging, which is a pass-through of these).
"""

import hashlib
import re
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from gcwrapped.config import LabelSettings, get_database_settings
from gcwrapped.labeling import commands
from gcwrapped.labeling.files import read_questions, read_tags
from gcwrapped.labeling.source import load_eligible
from gcwrapped.labeling.summary import LINE_SHAPES
from labeling_support import Scripted, StepClock

pytestmark = pytest.mark.integration

SENTINEL = "SENTINELTEXT"

# (thread, sender, content, sent minute). Thread 111: Ana yes, Bo no, Cy blank. Eve is "local"
# (local_ok only, D88).
ROWS = [
    ("111", "Ana", f"{SENTINEL} a1", 1),
    ("111", "Bo", f"{SENTINEL} declined between", 2),
    ("111", "Ana", f"{SENTINEL} a2", 3),
    ("111", "Cy", f"{SENTINEL} undecided between", 4),
    ("111", "Ana", f"{SENTINEL} a3", 5),
    ("111", "Ana", "   ", 6),  # no text
    ("111", "Ana", None, 7),  # no content at all
    ("111", "Ana", f"{SENTINEL} a4", 8),
    ("222", "Dee", f"{SENTINEL} d1", 1),  # sender missing from the ledger
    ("333", "Eve", f"{SENTINEL} e1", 1),
    ("333", "Eve", f"{SENTINEL} e2", 2),
]
CONSENT = [("111", "Ana", "yes"), ("111", "Bo", "no"), ("111", "Cy", ""), ("333", "Eve", "local")]


def _key(thread: str, n: int) -> str:
    return hashlib.sha256(f"{thread}/{n}".encode()).hexdigest()


@pytest.fixture
def warehouse(migrated_db: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    labels = tmp_path / "labels"
    labels.mkdir()
    monkeypatch.setenv("GCW_LABEL_DIR", str(labels))
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        conn.execute("DROP VIEW IF EXISTS analytics.fct_message_eligibility")
        run = conn.execute(
            "INSERT INTO raw.load_runs (roots_count) VALUES (1) RETURNING load_id"
        ).fetchone()
        assert run is not None
        for n, (thread, sender, content, minute) in enumerate(ROWS):
            conn.execute(
                "INSERT INTO raw.messages (message_key, platform, thread_id, sender_name, sent_at, "
                "content, media_count, source_file, load_id) VALUES "
                "(%s, 'instagram', %s, %s, '2024-01-01T00:00:00Z'::timestamptz "
                "+ make_interval(mins => %s), %s, 0, 'x', %s)",
                (_key(thread, n), thread, sender, minute, content, run[0]),
            )
        for thread, sender, consent in CONSENT:
            conn.execute(
                "INSERT INTO raw.sender_consent (thread_id, sender_name, consent) "
                "VALUES (%s, %s, %s)",
                (thread, sender, consent),
            )
        # Same definition as dbt/models/marts/fct_message_eligibility.sql (D69, D75, D88).
        conn.execute(
            "CREATE VIEW analytics.fct_message_eligibility AS "
            "SELECT m.message_key, m.thread_id, m.sender_name, "
            "coalesce(c.consent IN ('yes', 'local'), false) AS local_ok, "
            "coalesce(c.consent = 'yes', false) AS external_ok "
            "FROM raw.messages AS m LEFT JOIN raw.sender_consent AS c "
            "ON c.thread_id = m.thread_id AND c.sender_name = m.sender_name"
        )
    yield migrated_db
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DROP VIEW IF EXISTS analytics.fct_message_eligibility")


def _no_docker() -> None:
    return None


def test_only_consented_messages_with_text_are_eligible(warehouse: str) -> None:
    index = load_eligible(warehouse)
    texts = {m.text for t in index.ids_by_thread().values() for m in map(index.get, t)}
    assert texts == {f"{SENTINEL} {s}" for s in ("a1", "a2", "a3", "a4", "e1", "e2")}
    assert set(index.ids_by_thread()) == {"111", "333"}  # 222's sender is not in the ledger


def test_context_in_sql_order_skips_declined_and_undecided_senders(warehouse: str) -> None:
    index = load_eligible(warehouse)
    a3 = _key("111", 4)
    assert [m.text for m in index.before(a3, 2)] == [f"{SENTINEL} a1", f"{SENTINEL} a2"]
    assert [m.text for m in index.after(a3, 3)] == [f"{SENTINEL} a4"]  # blank/NULL rows skipped


def test_a_revoked_consent_removes_the_message_at_once(warehouse: str) -> None:
    with psycopg.connect(warehouse, autocommit=True) as conn:
        conn.execute("UPDATE raw.sender_consent SET consent = 'no' WHERE sender_name = 'Eve'")
    assert "333" not in load_eligible(warehouse).ids_by_thread()


def test_scripted_runs_complete_and_the_summary_is_fixed_lines(
    warehouse: str, capsys: pytest.CaptureFixture[str]
) -> None:
    get_database_settings.cache_clear()
    clock = StepClock()
    assert commands.run_label_tags(Scripted("1g2n3b4gq"), _no_docker, clock) == 0
    assert commands.run_label_questions(Scripted("wsq", ["my own words", "1"]), _no_docker) == 0
    tags = read_tags(Path(LabelSettings().gcw_label_dir) / "tag_labels.csv")
    assert len(tags) == 4  # the whole eligible pool is under 200, so all six are in the sample
    questions = read_questions(Path(LabelSettings().gcw_label_dir) / "questions.csv")
    assert len(questions) == 1 and len(questions[0].gold_message_ids) == 2
    capsys.readouterr()
    assert commands.run_label_summary(_no_docker) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(LINE_SHAPES)
    for line, shape in zip(lines, LINE_SHAPES, strict=True):
        assert re.fullmatch(shape, line), line
    assert lines[0] == "target database: gcw_test"
    assert "threads covered: 2" in lines  # counted inside the database from message IDs
    assert SENTINEL not in "\n".join(lines)
