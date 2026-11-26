"""Docker helpers: a preflight that tells "Docker is not running" apart from a compose error.

`docker compose up` fails the same way (CalledProcessError) whether the daemon is down or the
compose file is wrong, so commands that print only an exception type name could not say which.
`require_docker` runs `docker info` first and raises DockerUnavailableError instead. Its output
is discarded, never printed or logged: it can contain the host's registry, paths and usernames.

`require_no_other_stack` keeps two GC Wrapped stacks' model servers from running side by side
(D126). It reads container names and compose-project labels from `docker ps`, nothing else.
"""

import shutil
import subprocess

from gcwrapped.config import ComposeSettings, env_file_path

PREFLIGHT_TIMEOUT_SECONDS = 30
COMPOSE_TIMEOUT_SECONDS = 600
PROJECT_NAME = "gc-wrapped"  # `name:` in docker-compose.yml; every other stack's starts with it
MODEL_SERVICES = ("llm", "embed")
PS_COMMAND = ["docker", "ps", "--format", '{{.Names}}\t{{.Label "com.docker.compose.project"}}']


class DockerUnavailableError(Exception):
    """The Docker daemon is not reachable. Only the class name is ever printed."""


def require_docker() -> None:
    """Raise DockerUnavailableError unless `docker info` succeeds within the timeout."""
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PREFLIGHT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        # OSError: no docker binary. SubprocessError: non-zero exit or timeout.
        raise DockerUnavailableError from None


class OtherStackRunning(Exception):
    """Another GC Wrapped project's llm or embed container is running. Names are never printed."""


def other_model_containers(ps_output: str, own_project: str) -> int:
    """How many llm or embed containers of another GC Wrapped project `ps_output` lists."""
    found = 0
    for line in ps_output.splitlines():
        name, _, project = line.partition("\t")
        ours = project == PROJECT_NAME or project.startswith(f"{PROJECT_NAME}-")
        if ours and project != own_project:
            found += name.startswith(tuple(f"{project}-{s}-" for s in MODEL_SERVICES))
    return found


def running_containers() -> str:
    """One line per running container: its name, a tab, its compose project label."""
    try:
        listed = subprocess.run(
            PS_COMMAND,
            check=True,
            capture_output=True,
            text=True,
            timeout=PREFLIGHT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        raise DockerUnavailableError from None
    return listed.stdout


def require_no_other_stack() -> None:
    """Raise OtherStackRunning while another GC Wrapped project's model containers run, and
    DockerUnavailableError if `docker ps` cannot say.

    Does nothing where there is no docker binary: that is inside a compose container (Airflow's
    tag task), which reaches its own stack's network and cannot start or list containers.
    """
    if shutil.which("docker") is None:
        return
    own = ComposeSettings().compose_project_name or PROJECT_NAME
    if other_model_containers(running_containers(), own):
        raise OtherStackRunning


def start_database() -> None:
    """Start only the db service of the compose project ENV_FILE names; leave it running.

    Output is captured, not shown: compose prints container names. `make down` stops it.
    """
    require_docker()
    subprocess.run(
        ["docker", "compose", "--env-file", env_file_path(), "up", "-d", "--wait", "db"],
        check=True,
        capture_output=True,
        timeout=COMPOSE_TIMEOUT_SECONDS,
    )
