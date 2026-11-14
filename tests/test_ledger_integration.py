"""The consent ledger against the real gcw_test database: load, rollback, idempotency, template.

Only docker (the db start) is replaced. The warehouse read, migrate and the load run for real; the
dbt mart analytics.dim_members is stood in for by a table with the same columns the code reads.
"""

import os
import shutil
import stat
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest

from gcwrapped import ledger
from gcwrapped.ledger import HEADER, LedgerRow, check_ledger, write_ledger
from gcwrapped.migrate import MIGRATIONS_DIR, migrate

pytestmark = pytest.mark.integration

HEADER_LINE = ",".join(HEADER)
MEMBERS = [
    ("111", "SENTINELNAME a, 🎉", 5),
    ("111", "SENTINELNAME b", 9),
    ("222", "SENTINELNAME c", 2),
    ("222", "SENTINELNAME reactor", 0),  # reacted but never sent: needs no decision
]


def _no_start() -> None:
    return None


@pytest.fixture
def warehouse(migrated_db: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[str]:
    """gcw_test with dim_members seeded, the allowlist 111,222 and a ledger path in tmp_path."""
    monkeypatch.setenv("GCW_THREAD_ALLOWLIST", "111,222")
    monkeypatch.setenv("GCW_CONSENT_LEDGER", str(tmp_path / "SENTINELDIR" / "senders.csv"))
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        conn.execute("DROP TABLE IF EXISTS analytics.dim_members")
        conn.execute(
            "CREATE TABLE analytics.dim_members "
            "(thread_id text, member_name text, message_count integer)"
        )
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO analytics.dim_members VALUES (%s, %s, %s)", MEMBERS)
    yield migrated_db
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS analytics.dim_members")


def _ledger_path() -> Path:
    return Path(os.environ["GCW_CONSENT_LEDGER"])


def _table(url: str) -> list[tuple[str, str, str]]:
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT thread_id, sender_name, consent FROM raw.sender_consent ORDER BY 1, 2"
        ).fetchall()


def _write_ledger(*lines: str) -> Path:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([HEADER_LINE, *lines]) + "\n", encoding="utf-8")
    return path


GOOD = ('yes,111,"SENTINELNAME a, 🎉",5', "no,111,SENTINELNAME b,9", ",222,SENTINELNAME c,2")


def test_the_table_only_accepts_yes_local_no_or_blank(warehouse: str) -> None:
    with psycopg.connect(warehouse) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO raw.sender_consent VALUES ('1', 'x', 'maybe')")


def test_load_fills_the_table_and_prints_the_counts(
    warehouse: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_ledger(*GOOD)

    assert ledger.run_load(_no_start) == 0

    out = capsys.readouterr().out
    assert out.splitlines() == [
        "target database: gcw_test",
        "ledger rows: 3",
        "consented (yes): 1",
        "consented (local): 0",
        "declined (no): 1",
        "undecided (blank, treated as no): 1",
        "consent-load: ok",
    ]
    assert "sentinel" not in out.lower()
    assert _table(warehouse) == [
        ("111", "SENTINELNAME a, 🎉", "yes"),
        ("111", "SENTINELNAME b", "no"),
        ("222", "SENTINELNAME c", ""),
    ]


def test_load_twice_gives_identical_output_and_the_same_rows(
    warehouse: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_ledger(*GOOD)

    assert ledger.run_load(_no_start) == 0
    first_out, first_rows = capsys.readouterr().out, _table(warehouse)
    assert ledger.run_load(_no_start) == 0
    second_out, second_rows = capsys.readouterr().out, _table(warehouse)

    assert first_out == second_out
    assert first_rows == second_rows and len(second_rows) == 3


def test_a_changed_ledger_replaces_the_table_including_yes_to_no(warehouse: str) -> None:
    _write_ledger(*GOOD)
    assert ledger.run_load(_no_start) == 0

    _write_ledger('no,111,"SENTINELNAME a, 🎉",5', "yes,222,SENTINELNAME c,2")
    assert ledger.run_load(_no_start) == 0

    assert _table(warehouse) == [
        ("111", "SENTINELNAME a, 🎉", "no"),
        ("222", "SENTINELNAME c", "yes"),
    ]


@pytest.mark.parametrize(
    "bad_row",
    [
        "maybe,111,SENTINELNAME b,9",
        "yes,333,SENTINELNAME b,9",
        "yes,111,SENTINELNAME nobody,9",
        "yes,111,SENTINELNAME b,9\nno,111,SENTINELNAME b,9",
        "yes,222,SENTINELNAME reactor,0",  # in the warehouse, but sent nothing
    ],
)
def test_a_bad_ledger_loads_nothing_and_keeps_the_previous_table(
    warehouse: str, capsys: pytest.CaptureFixture[str], bad_row: str
) -> None:
    _write_ledger(*GOOD)
    assert ledger.run_load(_no_start) == 0
    before = _table(warehouse)
    capsys.readouterr()

    _write_ledger('yes,111,"SENTINELNAME a, 🎉",5', bad_row)
    assert ledger.run_load(_no_start) == 1

    out = capsys.readouterr().out
    assert out.splitlines()[-1] == "consent-load: failed"
    assert "sentinel" not in out.lower()
    assert _table(warehouse) == before


def test_a_failure_inside_the_transaction_rolls_back_the_delete(warehouse: str) -> None:
    ok = [
        LedgerRow("yes", "111", "SENTINELNAME b", "9"),
        LedgerRow("", "222", "SENTINELNAME c", "2"),
    ]
    ledger.replace_consent(warehouse, ok)
    before = _table(warehouse)

    broken = [LedgerRow("no", "111", "SENTINELNAME b", "9"), LedgerRow("maybe", "222", "x", "1")]
    with pytest.raises(psycopg.errors.CheckViolation):
        ledger.replace_consent(warehouse, broken)

    assert _table(warehouse) == before


def test_a_duplicate_row_inside_the_transaction_rolls_back_too(warehouse: str) -> None:
    ledger.replace_consent(warehouse, [LedgerRow("yes", "111", "SENTINELNAME b", "9")])
    before = _table(warehouse)

    twice = [LedgerRow("no", "111", "x", "1"), LedgerRow("yes", "111", "x", "1")]
    with pytest.raises(psycopg.errors.UniqueViolation):
        ledger.replace_consent(warehouse, twice)

    assert _table(warehouse) == before


def test_the_table_accepts_local_and_still_rejects_anything_else(warehouse: str) -> None:
    ledger.replace_consent(warehouse, [LedgerRow("local", "111", "SENTINELNAME b", "9")])
    assert _table(warehouse) == [("111", "SENTINELNAME b", "local")]

    with pytest.raises(psycopg.errors.CheckViolation):
        ledger.replace_consent(warehouse, [LedgerRow("Local", "111", "SENTINELNAME b", "9")])

    assert _table(warehouse) == [("111", "SENTINELNAME b", "local")]  # rolled back


def test_load_creates_the_table_on_a_database_that_predates_the_migration(
    clean_db: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    only_001 = tmp_path / "migrations"
    only_001.mkdir()
    shutil.copy(MIGRATIONS_DIR / "001_raw_schema.sql", only_001)
    migrate(clean_db, only_001)
    with psycopg.connect(clean_db, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        conn.execute("DROP TABLE IF EXISTS analytics.dim_members")
        conn.execute(
            "CREATE TABLE analytics.dim_members "
            "(thread_id text, member_name text, message_count integer)"
        )
        conn.execute("INSERT INTO analytics.dim_members VALUES ('111', 'SENTINELNAME b', 9)")
    monkeypatch.setenv("GCW_THREAD_ALLOWLIST", "111")
    monkeypatch.setenv("GCW_CONSENT_LEDGER", str(tmp_path / "l.csv"))
    (tmp_path / "l.csv").write_text(f"{HEADER_LINE}\nyes,111,SENTINELNAME b,9\n", encoding="utf-8")
    try:
        assert ledger.run_load(_no_start) == 0
        assert _table(clean_db) == [("111", "SENTINELNAME b", "yes")]
    finally:
        with psycopg.connect(clean_db, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS analytics.dim_members")


def test_warehouse_members_leaves_out_people_who_never_sent_anything(warehouse: str) -> None:
    members = ledger.warehouse_members(warehouse)

    assert members == {
        ("111", "SENTINELNAME a, 🎉"): 5,
        ("111", "SENTINELNAME b"): 9,
        ("222", "SENTINELNAME c"): 2,
    }


def test_template_creates_then_extends_without_changing_decisions(
    warehouse: str, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _ledger_path()

    assert ledger.run_template(_no_start) == 0
    first = capsys.readouterr().out
    assert first.splitlines() == [
        "target database: gcw_test",
        "ledger rows: 3",
        "new rows added: 3",
        "rows with a decision kept: 0",
        "stale rows: 0",
        "consent-template: ok",
    ]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # Ryan decides: "a" yes, "b" no, "c" left blank.
    answers = {"SENTINELNAME a, 🎉": "yes", "SENTINELNAME b": "no"}
    decided = [
        replace(row, consent=answers.get(row.sender_name, "")) for row in check_ledger(path).rows
    ]
    write_ledger(path, decided)
    # The warehouse changes: a new sender appears, one leaves.
    with psycopg.connect(warehouse, autocommit=True) as conn:
        conn.execute("INSERT INTO analytics.dim_members VALUES ('222', 'SENTINELNAME new', 4)")
        conn.execute("DELETE FROM analytics.dim_members WHERE member_name = 'SENTINELNAME b'")

    assert ledger.run_template(_no_start) == 0
    second = capsys.readouterr().out
    assert second.splitlines() == [
        "target database: gcw_test",
        "ledger rows: 4",
        "new rows added: 1",
        "rows with a decision kept: 2",
        "stale rows: 1",
        "consent-template: ok",
    ]
    by_name = {r.sender_name: r.consent for r in check_ledger(path).rows}
    assert by_name["SENTINELNAME a, 🎉"] == "yes"
    assert by_name["SENTINELNAME b"] == "no"  # stale, kept
    assert by_name["SENTINELNAME new"] == ""

    # Run again: nothing new, nothing changes.
    before = path.read_bytes()
    assert ledger.run_template(_no_start) == 0
    third = capsys.readouterr().out
    assert "new rows added: 0" in third.splitlines()
    assert path.read_bytes() == before
    assert "sentinel" not in (first + second + third).lower()


def test_a_fresh_template_loads_as_all_undecided(
    warehouse: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ledger.run_template(_no_start) == 0
    assert ledger.run_load(_no_start) == 0

    out = capsys.readouterr().out.splitlines()
    assert "consented (yes): 0" in out  # a fresh template is all blank: deny by default
    assert "undecided (blank, treated as no): 3" in out
    assert [c for _t, _s, c in _table(warehouse)] == ["", "", ""]
