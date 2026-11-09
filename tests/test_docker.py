"""The Docker preflight (src/gcwrapped/docker.py).

The external boundary is the `docker` binary, so `subprocess.run` is replaced with a stub that
records each command. Nothing here starts Docker or needs it running.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gcwrapped import docker, metrics
from gcwrapped.docker import DockerUnavailableError, require_docker, start_database

SENTINEL = "SENTINELDOCKERINFO"


class Recorder:
    """Stands in for subprocess.run: records argv and raises what `info_failure` says for info."""

    def __init__(self, info_failure: BaseException | None = None) -> None:
        self.argv: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []
        self.info_failure = info_failure

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.argv.append(argv)
        self.kwargs.append(kwargs)
        if argv[:2] == ["docker", "info"] and self.info_failure is not None:
            raise self.info_failure
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def compose_calls(self) -> list[list[str]]:
        return [a for a in self.argv if a[:2] == ["docker", "compose"]]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV_FILE", ".env")


def _install(monkeypatch: pytest.MonkeyPatch, rec: Recorder) -> Recorder:
    monkeypatch.setattr(subprocess, "run", rec)
    return rec


FAILURES: list[Callable[[], BaseException]] = [
    lambda: subprocess.CalledProcessError(1, ["docker", "info"]),
    lambda: subprocess.TimeoutExpired(["docker", "info"], 30),
    lambda: FileNotFoundError("docker"),
]


@pytest.mark.parametrize("make_failure", FAILURES)
def test_docker_info_failing_raises_and_compose_is_never_called(
    monkeypatch: pytest.MonkeyPatch, make_failure: Callable[[], BaseException]
) -> None:
    rec = _install(monkeypatch, Recorder(info_failure=make_failure()))

    with pytest.raises(DockerUnavailableError):
        start_database()

    assert rec.argv == [["docker", "info"]]
    assert rec.compose_calls() == []


def test_the_success_path_runs_info_then_the_unchanged_compose_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _install(monkeypatch, Recorder())

    start_database()

    assert rec.argv == [
        ["docker", "info"],
        ["docker", "compose", "--env-file", ".env", "up", "-d", "--wait", "db"],
    ]
    info_kwargs, compose_kwargs = rec.kwargs
    # docker info: output discarded and bounded in time. compose: captured, as before.
    assert info_kwargs["stdout"] is subprocess.DEVNULL
    assert info_kwargs["stderr"] is subprocess.DEVNULL
    assert info_kwargs["timeout"] == docker.PREFLIGHT_TIMEOUT_SECONDS
    assert compose_kwargs["capture_output"] is True
    assert compose_kwargs["check"] is True


def test_require_docker_alone_does_not_touch_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _install(monkeypatch, Recorder())

    require_docker()

    assert rec.argv == [["docker", "info"]]


def _fake_docker(bin_dir: Path, body: str) -> None:
    script = bin_dir / "docker"
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(0o755)


def test_a_sentinel_in_docker_info_output_never_reaches_stdout_or_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real child process (a fake `docker` on PATH) that fails and prints the sentinel."""
    _fake_docker(
        tmp_path,
        f'echo "{SENTINEL} out"; echo "{SENTINEL} err" >&2; exit 1',
    )
    monkeypatch.setenv("PATH", str(tmp_path))

    code = metrics.run(
        metrics.Steps(
            database=start_database,
            warehouse=lambda: pytest.fail("must stop at the database step"),
            dbt=lambda: pytest.fail("must stop at the database step"),
            pytest=lambda: pytest.fail("must stop at the database step"),
        )
    )

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == "metrics: failed at database (DockerUnavailableError)\n"
    assert SENTINEL not in captured.out + captured.err
