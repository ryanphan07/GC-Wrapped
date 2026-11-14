from pathlib import Path

import psycopg
import pytest

from gcwrapped.cli import main
from gcwrapped.migrate import migrate

pytestmark = pytest.mark.integration


def _applied(url: str) -> list[str]:
    with psycopg.connect(url) as conn:
        return [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]


def test_migrate_creates_raw_tables_and_is_idempotent(clean_db: str) -> None:
    first = migrate(clean_db)
    second = migrate(clean_db)

    assert first == ["001_raw_schema", "002_sender_consent", "003_consent_local"]
    assert second == []
    assert _applied(clean_db) == ["001_raw_schema", "002_sender_consent", "003_consent_local"]
    with psycopg.connect(clean_db) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'raw'"
            )
        }
    assert tables == {"messages", "load_runs", "sender_consent"}


def test_cli_migrate_is_idempotent(clean_db: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["migrate"]) == 0
    first = capsys.readouterr().out
    assert main(["migrate"]) == 0
    second = capsys.readouterr().out

    assert "target database: gcw_test" in first
    assert "applied: 001_raw_schema" in first
    assert "applied: 002_sender_consent" in first
    assert "applied: 003_consent_local" in first
    assert "migrations applied: 3" in first
    assert "migrations applied: 0" in second
    assert "applied: 001" not in second


def test_failed_migration_rolls_back_fully_and_is_not_recorded(
    clean_db: str, tmp_path: Path
) -> None:
    (tmp_path / "001_ok.sql").write_text("CREATE TABLE migration_probe (id int);", encoding="utf-8")
    (tmp_path / "002_bad.sql").write_text(
        "CREATE TABLE migration_probe_2 (id int); SELECT 1/0;", encoding="utf-8"
    )

    with pytest.raises(psycopg.Error):
        migrate(clean_db, tmp_path)

    assert _applied(clean_db) == ["001_ok"]  # earlier file stays applied
    with psycopg.connect(clean_db) as conn:
        row = conn.execute("SELECT to_regclass('migration_probe_2')").fetchone()
    assert row == (None,)  # the failed file's DDL was rolled back
    # Fixing the file and re-running applies it.
    (tmp_path / "002_bad.sql").write_text("CREATE TABLE migration_probe_2 (id int);")
    assert migrate(clean_db, tmp_path) == ["002_bad"]
    with psycopg.connect(clean_db, autocommit=True) as conn:
        conn.execute("DROP TABLE migration_probe_2")
