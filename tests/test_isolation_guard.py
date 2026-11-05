"""Negative tests for the test-isolation guard. Never weaken, skip or xfail these.

Every real-stack value is fed in as a fake (direct arguments or a temp directory standing in for
$HOME). Nothing here touches the real private directory.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import isolation_guard
from isolation_guard import (
    PRIVATE_DIR_NAME,
    IsolationError,
    check_isolation,
    check_session,
    effective_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEV = {
    "POSTGRES_PORT": "5432",
    "GCW_DB_NAME": "gcw_dev",
    "GCW_LANDING_HOST_DIR": "./data/landing",
}


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A fake $HOME with a private directory inside it."""
    (tmp_path / "home" / PRIVATE_DIR_NAME).mkdir(parents=True)
    return tmp_path / "home"


def _check(config: dict[str, str], home: Path) -> None:
    check_isolation(config, repo_root=REPO_ROOT, home=home, cwd=REPO_ROOT)


def test_dev_configuration_passes(home: Path) -> None:
    _check(DEV, home)


@pytest.mark.parametrize(
    "override",
    [
        {"POSTGRES_PORT": "5433"},
        {"POSTGRES_HOST_PORT": "5433"},
        {"GCW_DB_NAME": "gcw_real"},
        {"COMPOSE_PROJECT_NAME": "gc-wrapped-real"},
    ],
)
def test_real_stack_values_are_rejected(override: dict[str, str], home: Path) -> None:
    with pytest.raises(IsolationError):
        _check({**DEV, **override}, home)


@pytest.mark.parametrize(
    "var", ["ENV_FILE", "GCW_LANDING_HOST_DIR", "GCW_LANDING_DIR", "GCW_LOG_DIR"]
)
def test_paths_under_private_dir_are_rejected(var: str, home: Path) -> None:
    private = home / PRIVATE_DIR_NAME
    with pytest.raises(IsolationError):
        _check({**DEV, var: str(private / "sub" / "x")}, home)


def test_tilde_and_symlink_resolve_before_comparing(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(home))
    with pytest.raises(IsolationError):
        _check({**DEV, "GCW_LOG_DIR": f"~/{PRIVATE_DIR_NAME}/logs"}, home)

    link = tmp_path / "innocent"
    link.symlink_to(home / PRIVATE_DIR_NAME)
    with pytest.raises(IsolationError):
        _check({**DEV, "GCW_LANDING_DIR": str(link)}, home)


def test_dotdot_cannot_hide_a_private_path(home: Path) -> None:
    sneaky = home / "elsewhere" / ".." / PRIVATE_DIR_NAME / "landing"
    with pytest.raises(IsolationError):
        _check({**DEV, "GCW_LANDING_HOST_DIR": str(sneaky)}, home)


def test_landing_outside_repo_is_rejected(home: Path, tmp_path: Path) -> None:
    with pytest.raises(IsolationError):
        _check({**DEV, "GCW_LANDING_HOST_DIR": str(tmp_path / "landing")}, home)


def test_landing_defaults_to_inside_the_repo(home: Path) -> None:
    _check({"GCW_DB_NAME": "gcw_dev"}, home)


def test_error_message_never_contains_the_value(home: Path) -> None:
    value = str(home / PRIVATE_DIR_NAME / "SENTINELPATH")
    with pytest.raises(IsolationError) as info:
        _check({**DEV, "GCW_LOG_DIR": value}, home)
    assert "SENTINELPATH" not in str(info.value)
    assert PRIVATE_DIR_NAME not in str(info.value).replace("private real-data directory", "")


def test_env_file_values_count_and_environ_overrides(home: Path, tmp_path: Path) -> None:
    env_file = tmp_path / "t.env"
    env_file.write_text("# c\nexport POSTGRES_PORT=5433\nGCW_DB_NAME='gcw_dev'\n", encoding="utf-8")
    environ = {"ENV_FILE": str(env_file)}

    with pytest.raises(IsolationError):
        check_session(environ, cwd=tmp_path, repo_root=REPO_ROOT, home=home)

    environ["POSTGRES_PORT"] = "5432"  # process environment wins, as in config.py
    check_session(environ, cwd=tmp_path, repo_root=REPO_ROOT, home=home)


def test_env_file_under_private_dir_is_never_opened(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(path: Path) -> dict[str, str]:
        raise AssertionError("the private env file was opened")

    monkeypatch.setattr(isolation_guard, "_read_env_file", boom)
    environ = {"ENV_FILE": str(home / PRIVATE_DIR_NAME / "real.env")}

    with pytest.raises(IsolationError):
        effective_config(environ, cwd=REPO_ROOT, home=home)
    with pytest.raises(IsolationError):
        check_session(environ, cwd=REPO_ROOT, repo_root=REPO_ROOT, home=home)


def test_session_fixture_aborts_the_whole_run(tmp_path: Path) -> None:
    """Wiring: a real session pointed at a real-stack value stops at the guard, not at a test."""
    env_file = tmp_path / "bad.env"
    env_file.write_text("POSTGRES_PORT=5433\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in isolation_guard.GUARDED_VARS}
    env["ENV_FILE"] = str(env_file)

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_config.py", "-q", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 3
    assert "test-isolation guard" in result.stdout + result.stderr
    assert " passed" not in result.stdout
