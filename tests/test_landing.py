from pathlib import Path

import pytest

from gcwrapped.cli import main
from gcwrapped.config import get_settings
from gcwrapped.landing import find_export_roots
from synthetic.instagram import generate_pair


@pytest.fixture(autouse=True)
def no_db_env(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    for var in (
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_PORT",
        "DATABASE_URL",
        "GCW_DB_NAME",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
    get_settings.cache_clear()


def test_find_export_roots_returns_export_subfolders(tmp_path: Path) -> None:
    a, b = generate_pair(tmp_path)
    (tmp_path / "half_copied").mkdir()
    (tmp_path / "stray.txt").write_text("x", encoding="utf-8")

    assert find_export_roots(tmp_path) == [a, b]


def test_find_export_roots_empty_or_missing_dir(tmp_path: Path) -> None:
    assert find_export_roots(tmp_path) == []
    assert find_export_roots(tmp_path / "missing") == []


def test_check_landing_prints_count_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    generate_pair(tmp_path)

    assert main(["check-landing", str(tmp_path)]) == 0

    assert capsys.readouterr().out.strip() == "export roots: 2"


def test_check_landing_fails_clearly_when_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check-landing", str(tmp_path)]) == 1

    assert "no export roots" in capsys.readouterr().out


def test_ingest_landing_with_no_roots_fails_before_touching_the_db(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["ingest", "--landing", str(tmp_path)]) == 1

    assert "no export roots" in capsys.readouterr().out


def test_ingest_needs_paths_or_landing_but_not_both(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["ingest"])
    with pytest.raises(SystemExit):
        main(["ingest", str(tmp_path), "--landing", str(tmp_path)])
