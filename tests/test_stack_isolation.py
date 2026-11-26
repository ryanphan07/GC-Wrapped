"""Two GC Wrapped stacks never run model servers side by side (D126).

The external boundary is the `docker` binary: a fake one on PATH prints a `docker ps` listing and
records every call made to it, so a test can show that a refused command started nothing.
"""

import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from gcwrapped import docker, eval_all
from gcwrapped.answers import commands as answers
from gcwrapped.config import get_database_settings
from gcwrapped.dashboard import services as dashboard_services
from gcwrapped.docker import (
    DockerUnavailableError,
    OtherStackRunning,
    other_model_containers,
    require_no_other_stack,
)
from gcwrapped.retrieval import commands as retrieval
from gcwrapped.tagging import commands as tagging
from gcwrapped.tagging import model

REPO = Path(__file__).resolve().parents[1]
REAL_PS = docker.running_containers  # conftest's autouse fixture replaces it in every test

DEV = "gc-wrapped"
REAL = "gc-wrapped-real"
REAL_LLM = f"{REAL}-llm-1\t{REAL}"
REAL_DB = f"{REAL}-db-1\t{REAL}"
DEV_STACK = f"{DEV}-db-1\t{DEV}\n{DEV}-llm-1\t{DEV}\n{DEV}-embed-1\t{DEV}"


@pytest.mark.parametrize(
    ("listing", "own", "found"),
    [
        (REAL_LLM, DEV, 1),
        (f"{REAL}-embed-1\t{REAL}", DEV, 1),
        (f"{REAL}-llm-run-0a1b2c\t{REAL}", DEV, 1),
        (REAL_DB, DEV, 0),  # a database alone is allowed: hand labeling runs beside dev work
        (f"{REAL}-dashboard-1\t{REAL}\n{REAL}-airflow-1\t{REAL}", DEV, 0),
        (DEV_STACK, DEV, 0),
        (DEV_STACK, REAL, 2),  # the real stack refuses while the dev model servers run
        (f"{DEV}-llm-1\t{DEV}\n{REAL_LLM}", REAL, 1),
        ("gc-wrapped-ci-eval-llm-1\tgc-wrapped-ci-eval", DEV, 1),
        ("other-llm-1\tother\nllm\t\ngc-wrappedx-llm-1\tgc-wrappedx", DEV, 0),
        ("", DEV, 0),
    ],
)
def test_only_another_projects_llm_and_embed_containers_count(
    listing: str, own: str, found: int
) -> None:
    assert other_model_containers(listing, own) == found


def test_the_project_name_is_the_compose_files() -> None:
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"\nname: {docker.PROJECT_NAME}\n" in compose
    for service in docker.MODEL_SERVICES:
        assert f"\n  {service}:\n" in compose


class FakeDocker:
    def __init__(self, directory: Path) -> None:
        self._script = directory / "docker"
        self._listing = directory / "listing"
        self._log = directory / "calls"

    def answer(self, listing: str, status: int = 0) -> None:
        self._listing.write_text(f"{listing}\n", encoding="utf-8")
        self._script.write_text(  # shell builtins only: PATH holds nothing else
            f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{self._log}'\n"
            f"while IFS= read -r line; do printf '%s\\n' \"$line\"; done < '{self._listing}'\n"
            f"exit {status}\n",
            encoding="utf-8",
        )
        self._script.chmod(0o755)

    def calls(self) -> list[str]:
        return self._log.read_text(encoding="utf-8").splitlines() if self._log.exists() else []


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeDocker]:
    """A `docker` on an otherwise empty PATH, behind the real `docker ps` call, and settings that
    reach every command's docker step without a database, a model file or Docker."""
    monkeypatch.setattr(docker, "running_containers", REAL_PS)
    monkeypatch.setenv("PATH", str(tmp_path))
    for key, value in (
        ("COMPOSE_PROJECT_NAME", DEV),
        ("GCW_DB_NAME", "gcw_dev"),
        ("POSTGRES_USER", "u"),
        ("POSTGRES_PASSWORD", "p"),
        ("DATABASE_URL", ""),
        ("GCW_LLM_URL", "http://127.0.0.1:1"),
        ("GCW_EMBED_URL", "http://127.0.0.1:1"),
    ):
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    get_database_settings.cache_clear()
    yield FakeDocker(tmp_path)
    get_database_settings.cache_clear()


def test_the_check_reads_names_and_project_labels_only(fake_docker: FakeDocker) -> None:
    fake_docker.answer(DEV_STACK)
    require_no_other_stack()
    (call,) = fake_docker.calls()
    assert call == 'ps --format {{.Names}}\t{{.Label "com.docker.compose.project"}}'


def test_the_real_database_alone_is_allowed(fake_docker: FakeDocker) -> None:
    fake_docker.answer(f"{REAL_DB}\n{DEV_STACK}")
    require_no_other_stack()


def test_the_real_llm_is_refused(fake_docker: FakeDocker) -> None:
    fake_docker.answer(f"{REAL_DB}\n{REAL_LLM}")
    with pytest.raises(OtherStackRunning):
        require_no_other_stack()


def test_the_own_project_comes_from_the_env_file(
    fake_docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "other.env"
    env_file.write_text(f"COMPOSE_PROJECT_NAME={REAL}\n", encoding="utf-8")
    monkeypatch.setenv("ENV_FILE", str(env_file))
    monkeypatch.delenv("COMPOSE_PROJECT_NAME")
    fake_docker.answer(REAL_LLM)
    require_no_other_stack()  # its own
    fake_docker.answer(DEV_STACK)
    with pytest.raises(OtherStackRunning):
        require_no_other_stack()


def test_a_docker_ps_that_fails_is_a_refusal(fake_docker: FakeDocker) -> None:
    fake_docker.answer("", status=1)
    with pytest.raises(DockerUnavailableError):
        require_no_other_stack()


def test_without_a_docker_binary_there_is_nothing_to_ask(fake_docker: FakeDocker) -> None:
    require_no_other_stack()  # no script written: PATH holds no docker, as in a container
    assert fake_docker.calls() == []


COMMANDS: list[tuple[str, Callable[[], int]]] = [
    ("tag", lambda: tagging.run_tag()),
    ("tag-eval", lambda: tagging.run_tag_eval(model.MODEL_ID, synthetic=True)),
    ("embed", lambda: retrieval.run_embed(synthetic=True)),
    ("retrieval-eval", lambda: retrieval.run_retrieval_eval("vector", synthetic=True)),
    ("answer-eval", lambda: answers.run_answer_eval(synthetic=True)),
    ("ask", lambda: answers.run_ask(synthetic=True)),
    ("grade-answers", lambda: answers.run_grade_answers(synthetic=True)),
    ("dashboard-up", lambda: dashboard_services.run()),
    ("embed", lambda: eval_all.run(synthetic=True)),  # eval-all stops at its first step
]


@pytest.mark.parametrize(("command", "run"), COMMANDS)
def test_every_model_command_refuses_with_one_fixed_line_and_starts_nothing(
    fake_docker: FakeDocker,
    capsys: pytest.CaptureFixture[str],
    command: str,
    run: Callable[[], int],
) -> None:
    """The commands run with their real service starters: the only docker call is the listing."""
    fake_docker.answer(REAL_LLM)
    assert run() == 1
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == f"{command}: failed at docker (OtherStackRunning)"
    assert [line for line in out if not line.startswith("== ")] == out[-1:]
    assert REAL not in "".join(out)  # container names are never printed
    assert [call.split()[0] for call in fake_docker.calls()] == ["ps"]


def test_starting_the_llm_with_a_fresh_server_recreates_the_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / model.MODEL_FILE).write_bytes(b"stands in for the model file")
    monkeypatch.setenv("GCW_MODEL_DIR", str(tmp_path))
    monkeypatch.setenv("ENV_FILE", ".env")
    started: list[list[str]] = []

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        started.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", run)
    tagging.start_llm(model.MODEL_ID)
    tagging.start_llm(model.MODEL_ID, fresh=True)
    plain, fresh = (argv for argv in started if argv[1] == "compose")
    assert "--force-recreate" not in plain
    assert fresh[-4:] == ["-d", "--wait", "--force-recreate", "llm"]
