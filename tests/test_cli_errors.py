"""Unhandled CLI errors print the exception type only (decisions.md, 2026-11-05). Synthetic data.

Cases (a) and (d) patch `cli._migrate` to build DatabaseSettings() directly, because the real
commands already catch that ValidationError; the wrapper in main() is what is under test.
"""

import subprocess
import sys
from pathlib import Path

import pytest

PATCHED = (
    "import sys; from gcwrapped import cli; from gcwrapped.config import DatabaseSettings; "
    "cli._migrate = lambda: (DatabaseSettings(), 0)[1]; sys.exit(cli.main(['migrate']))"
)
REAL = "import sys; from gcwrapped import cli; sys.exit(cli.main(['last-run']))"
GOOD_DB = "GCW_DB_NAME=gcw_dev\nPOSTGRES_USER=u\nPOSTGRES_PASSWORD=p\n"


def _run(
    code: str, env_file: Path, tmp_path: Path, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "ENV_FILE": str(env_file), **(extra or {})}
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _env(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "t.env"
    path.write_text(text, encoding="utf-8")
    return path


def test_bad_int_setting_prints_type_only(tmp_path: Path) -> None:
    result = _run(PATCHED, _env(tmp_path, GOOD_DB + "POSTGRES_PORT=SENTINELVALUE\n"), tmp_path)

    assert result.returncode == 1
    assert result.stderr == "gcw: failed: ValidationError\n"
    assert "sentinel" not in (result.stdout + result.stderr).lower()


def test_bad_log_level_prints_type_only(tmp_path: Path) -> None:
    result = _run(REAL, _env(tmp_path, "LOG_LEVEL=SENTINELLEVEL\n"), tmp_path)

    assert result.returncode == 1
    assert result.stderr.startswith("gcw: failed: ")
    assert result.stderr.count("\n") == 1
    assert "sentinel" not in (result.stdout + result.stderr).lower()


def test_missing_env_file_prints_type_only(tmp_path: Path) -> None:
    result = _run(REAL, tmp_path / "missing_SENTINELPATH.env", tmp_path)

    assert result.returncode == 1
    assert result.stderr == "gcw: failed: EnvFileMissingError\n"
    assert "sentinel" not in (result.stdout + result.stderr).lower()


def test_traceback_escape_hatch(tmp_path: Path) -> None:
    env = _env(tmp_path, GOOD_DB + "POSTGRES_PORT=SENTINELVALUE\n")
    result = _run(PATCHED, env, tmp_path, {"GCW_TRACEBACK": "1"})

    assert result.returncode != 0
    assert "Traceback" in result.stdout + result.stderr


@pytest.mark.parametrize("flag", [None, "0"])
def test_traceback_is_off_unless_exactly_one(tmp_path: Path, flag: str | None) -> None:
    env = _env(tmp_path, GOOD_DB + "POSTGRES_PORT=x\n")
    result = _run(PATCHED, env, tmp_path, {} if flag is None else {"GCW_TRACEBACK": flag})

    assert "Traceback" not in result.stderr
