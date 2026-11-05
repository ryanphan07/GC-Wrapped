"""Unhandled CLI errors print the exception type only (decisions.md, 2026-11-05). Synthetic data.

Cases (a) and (d) patch `cli._migrate` to build DatabaseSettings() directly, because the real
commands already catch that ValidationError; the wrapper in main() is what is under test. The real
commands are run unpatched further down, and the import-time test proves no module loads settings
(and so bypasses the wrapper) when it is imported.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from synthetic.instagram import SYNTHETIC_THREAD_IDS, generate_pair

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


def _gcw(argv: list[str], env_file: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    code = f"import sys; from gcwrapped import cli; sys.exit(cli.main({argv!r}))"
    return _run(code, env_file, tmp_path)


@pytest.mark.parametrize("command", ["migrate", "last-run", "ingest"])
def test_real_commands_do_not_echo_a_bad_setting(command: str, tmp_path: Path) -> None:
    """No patching: the real command meets POSTGRES_PORT=SENTINELVALUE and must not print it."""
    export, _ = generate_pair(tmp_path / "export")
    allowlist = f"GCW_THREAD_ALLOWLIST={','.join(SYNTHETIC_THREAD_IDS)}\n"
    env = _env(tmp_path, GOOD_DB + allowlist + "POSTGRES_PORT=SENTINELVALUE\n")
    argv = [command, str(export)] if command == "ingest" else [command]

    result = _gcw(argv, env, tmp_path)

    assert result.returncode != 0
    assert "sentinel" not in (result.stdout + result.stderr).lower()


# Modules that cannot be imported in every environment, skipped by name: the dashboard queries
# need pandas, which only the optional `dashboard` dependency group installs.
OPTIONAL_IMPORTS = {"gcwrapped.dashboard.queries": "pandas"}

IMPORT_ALL = """
import importlib, pkgutil, sys
import gcwrapped

OPTIONAL = {optional!r}
imported = 0
for info in pkgutil.walk_packages(gcwrapped.__path__, "gcwrapped."):
    try:
        importlib.import_module(info.name)
    except ModuleNotFoundError as exc:
        if OPTIONAL.get(info.name) != exc.name:
            raise
        continue
    imported += 1
print(imported)
"""


def test_importing_every_module_loads_no_settings(tmp_path: Path) -> None:
    """ENV_FILE names a missing file, so any import-time settings load would raise.

    The `gcw` console script imports cli before main() runs; a settings load at import time would
    fail outside main()'s error wrapper and could print a raw input value.
    """
    result = _run(
        IMPORT_ALL.format(optional=OPTIONAL_IMPORTS),
        tmp_path / "missing_SENTINELPATH.env",
        tmp_path,
    )

    assert result.returncode == 0, "a module loads settings at import time"
    assert int(result.stdout.strip()) >= 15
    assert "sentinel" not in (result.stdout + result.stderr).lower()
