"""Host-side settings load only the file named by ENV_FILE (decisions.md, 2026-11-05).

All data is synthetic. The point is which file's values are used, so the allowlists here differ
only in how many IDs they list.
"""

import os
import subprocess
from pathlib import Path

import pytest

from gcwrapped.cli import main
from gcwrapped.config import (
    ConsentSettings,
    DatabaseSettings,
    Settings,
    env_file_path,
    get_settings,
)
from gcwrapped.consent import load_allowlist
from synthetic.instagram import SYNTHETIC_THREAD_IDS, generate_pair

REPO = Path(__file__).resolve().parents[1]
ONE, TWO, THREE = SYNTHETIC_THREAD_IDS


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A cwd holding a dev-style .env (allowlist of TWO IDs) and none of the relevant variables."""
    for var in (
        "ENV_FILE",
        "LOG_LEVEL",
        "GCW_THREAD_ALLOWLIST",
        "GCW_DB_NAME",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / ".env").write_text(
        f"GCW_THREAD_ALLOWLIST={ONE},{TWO}\n"
        "GCW_DB_NAME=gcw_dev\nPOSTGRES_USER=dev_user\nPOSTGRES_PASSWORD=dev_pw\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(cwd)
    get_settings.cache_clear()
    return cwd


def _other_env(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "other.env"
    path.write_text(text, encoding="utf-8")
    return path


def test_env_file_defaults_to_dot_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert env_file_path() == ".env"
    monkeypatch.setenv("ENV_FILE", "")
    assert env_file_path() == ".env"
    assert load_allowlist() == {ONE, TWO}


def test_named_env_file_wins_over_dot_env_in_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _other_env(tmp_path, f"GCW_THREAD_ALLOWLIST={THREE}\n")
    monkeypatch.setenv("ENV_FILE", str(other))

    assert load_allowlist() == {THREE}


def test_a_key_only_in_dot_env_is_not_read_when_another_file_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _other_env(tmp_path, "LOG_LEVEL=DEBUG\n")  # no allowlist, no DB settings
    monkeypatch.setenv("ENV_FILE", str(other))

    assert load_allowlist() == frozenset()  # not the .env allowlist
    assert ConsentSettings().gcw_thread_allowlist == ""
    assert Settings().log_level == "DEBUG"
    with pytest.raises(ValueError, match="gcw_db_name"):  # dev's database name is not borrowed
        DatabaseSettings()


def test_process_environment_still_overrides_the_named_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _other_env(tmp_path, f"GCW_THREAD_ALLOWLIST={THREE}\n")
    monkeypatch.setenv("ENV_FILE", str(other))
    monkeypatch.setenv("GCW_THREAD_ALLOWLIST", f"{ONE},{TWO}")

    assert load_allowlist() == {ONE, TWO}


def test_check_landing_uses_the_named_files_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    landing = tmp_path / "landing"
    generate_pair(landing)
    monkeypatch.setenv("ENV_FILE", str(_other_env(tmp_path, f"GCW_THREAD_ALLOWLIST={THREE}\n")))

    assert main(["check-landing", str(landing)]) == 0

    out = capsys.readouterr().out
    assert "allowlisted IDs configured: 1" in out  # .env in cwd lists two


def test_make_check_landing_uses_the_named_files_allowlist(tmp_path: Path) -> None:
    landing = tmp_path / "landing"
    generate_pair(landing)
    other = _other_env(tmp_path, f"GCW_THREAD_ALLOWLIST={THREE}\n")
    env = {k: v for k, v in os.environ.items() if k not in ("ENV_FILE", "GCW_THREAD_ALLOWLIST")}

    result = subprocess.run(
        ["make", "check-landing", f"ENV_FILE={other}", f"GCW_LANDING_HOST_DIR={landing}"],
        cwd=REPO,  # the repo's own .env (if any) is in cwd here and must not be used
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout[-500:]
    assert "allowlisted IDs configured: 1" in result.stdout
