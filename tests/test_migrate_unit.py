from pathlib import Path

import pytest

from gcwrapped.migrate import MIGRATIONS_DIR, MigrationError, discover


def _touch(directory: Path, *names: str) -> None:
    for name in names:
        (directory / name).write_text("SELECT 1;", encoding="utf-8")


def test_discover_returns_files_in_numeric_order(tmp_path: Path) -> None:
    _touch(tmp_path, "010_later.sql", "002_second.sql", "001_first.sql")

    assert [m.version for m in discover(tmp_path)] == ["001_first", "002_second", "010_later"]


def test_discover_ignores_non_sql_files(tmp_path: Path) -> None:
    _touch(tmp_path, "001_first.sql", "notes.txt")

    assert [m.version for m in discover(tmp_path)] == ["001_first"]


def test_discover_rejects_bad_filename(tmp_path: Path) -> None:
    _touch(tmp_path, "first.sql")

    with pytest.raises(MigrationError, match=r"first\.sql"):
        discover(tmp_path)


def test_discover_rejects_duplicate_numbers(tmp_path: Path) -> None:
    _touch(tmp_path, "001_a.sql", "001_b.sql")

    with pytest.raises(MigrationError, match="duplicate"):
        discover(tmp_path)


def test_discover_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="not found"):
        discover(tmp_path / "nope")


def test_repo_migrations_are_well_formed() -> None:
    versions = [m.version for m in discover(MIGRATIONS_DIR)]

    assert versions[0] == "001_raw_schema"
