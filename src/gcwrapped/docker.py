"""Docker helpers: a preflight that tells "Docker is not running" apart from a compose error.

`docker compose up` fails the same way (CalledProcessError) whether the daemon is down or the
compose file is wrong, so commands that print only an exception type name could not say which.
`require_docker` runs `docker info` first and raises DockerUnavailableError instead. Its output
is discarded, never printed or logged: it can contain the host's registry, paths and usernames.
"""

import subprocess

from gcwrapped.config import env_file_path

PREFLIGHT_TIMEOUT_SECONDS = 30
COMPOSE_TIMEOUT_SECONDS = 600


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
