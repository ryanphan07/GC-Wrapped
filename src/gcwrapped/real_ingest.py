"""`make real-ingest ENV_FILE=<path>`: guarded pipeline run that prints allowlisted lines only.

Why this exists (decisions.md, 2026-11-05): Airflow logs, dbt output and Python tracebacks can echo
row values, and a denylist cannot know every name. So the full output goes to a private log file
(GCW_LOG_DIR, outside the repo) and the terminal gets only whole lines that match a fixed set of
patterns made of fixed labels and numbers. Safe by construction: a line is printed only if the whole
remaining line (after known wrappers are removed) fully matched one fixed pattern, and what is
printed is that line, which can therefore contain nothing but a fixed label and numbers. The runner
also prints two fixed step headers from code.

Everything here is tested on synthetic data with sentinel strings. The code never knows where a real
env file lives; the caller passes it.
"""

import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from gcwrapped.consent import ALLOWLIST_VAR, AllowlistError, parse_allowlist

# What the dev stack uses (docker-compose.yml `name:` and port defaults; a test pins them).
DEV_COMPOSE_PROJECT = "gc-wrapped"
DEV_POSTGRES_PORT = 5432
DEV_DASHBOARD_PORT = 8501
DEV_ENV_FILE_NAMES = frozenset({".env", ".env.example"})
DEV_DB_NAME = "gcw_dev"
DEFAULT_LANDING = "./data/landing"
REPO_ROOT = Path(__file__).resolve().parents[2]

# --- Summarizer ----------------------------------------------------------------------------------
# Every pattern is a fixed label plus integers (or one enumerated word). Nothing here can match a
# name, a message, a path or a traceback line. A new line shape is added here, deliberately.
_NUM = r"(\d+)"
_SECONDS = r"(\d+\.\d{2})"
_STATUS = r"(succeeded|failed|running)"
_SHAPES = r"(<name>_<digits>|<digits>|other)"
ALLOWED_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        # gcw check-landing
        rf"export roots: {_NUM}",
        rf"root {_NUM} folders {_SHAPES}: {_NUM}",
        rf"root {_NUM} allowlisted IDs found: {_NUM}",
        rf"root {_NUM} allowlisted threads to ingest: {_NUM}",
        rf"total folders {_SHAPES}: {_NUM}",
        rf"allowlisted IDs configured: {_NUM}",
        rf"allowlisted IDs found: {_NUM}",
        rf"allowlisted IDs not found: {_NUM}",
        rf"allowlisted threads to ingest: {_NUM}",
        # gcw migrate / ingest / last-run
        r"target database: (gcw_dev|gcw_real|gcw_ci)",
        rf"migrations applied: {_NUM}",
        rf"threads allowlisted: {_NUM}",
        rf"folders skipped \(not allowlisted\): {_NUM}",
        rf"folders skipped \(unrecognised shape\): {_NUM}",
        rf"rows parsed: {_NUM}",
        rf"rows inserted: {_NUM}",
        rf"duplicates skipped: {_NUM}",
        rf"elapsed seconds: {_SECONDS}",
        rf"load id: {_NUM}",
        rf"status: {_STATUS}",
        # dbt
        rf"Done\. PASS={_NUM} WARN={_NUM} ERROR={_NUM} SKIP={_NUM}"
        rf"(?: NO-OP={_NUM})?(?: REUSED={_NUM})? TOTAL={_NUM}",
        # this runner
        rf"pipeline seconds: {_SECONDS}",
    )
)

# Wrappers a line may carry. They are removed before matching and never printed:
#  - ANSI colour codes;
#  - Airflow's structured task log, `<UTC time> [info     ] <message>   [<logger>] loc=<file:line>`
#    (the message is whatever the task wrote to stdout, so it is matched like any other line);
#  - dbt's `08:24:21  ` clock prefix.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_AIRFLOW_LOG = re.compile(
    r"\d{4}-\d{2}-\d{2}T[0-9:.]+Z \[[a-z]+\s*\] (?P<message>.*?)\s+\[[A-Za-z0-9_.]+\](?: loc=\S+)?"
)
_CLOCK = re.compile(r"\d{2}:\d{2}:\d{2}\s+")


def summarize_line(line: str) -> str | None:
    """The line to show for one raw output line, or None to show nothing.

    After the wrappers above are removed, the WHOLE remaining line must match one allowed pattern.
    What is returned is that remaining line, so it can only be a fixed label plus numbers.
    """
    text = _ANSI.sub("", line).strip()
    if wrapped := _AIRFLOW_LOG.fullmatch(text):
        text = wrapped["message"].strip()
    if clock := _CLOCK.match(text):
        text = text[clock.end() :]
    for pattern in ALLOWED_LINE_PATTERNS:
        if pattern.fullmatch(text):
            return text
    return None


def summarize(lines: Iterable[str]) -> list[str]:
    return [shown for line in lines if (shown := summarize_line(line)) is not None]


# --- Guards --------------------------------------------------------------------------------------


class GuardError(Exception):
    """A real-ingest precondition failed. The message is safe to print (no env values)."""


def parse_env_file(path: Path) -> dict[str, str]:
    """Plain KEY=value lines, as `make -include` reads them (no quotes, no `export`)."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


# ASCII digits only, as in consent.py: str.isdigit() accepts characters such as "²".
_ASCII_DIGITS = re.compile(r"[0-9]+")


def _port(env: Mapping[str, str], name: str, dev_port: int) -> None:
    raw = env.get(name, "")
    if not raw:
        raise GuardError(f"{name} is not set in the env file")
    if _ASCII_DIGITS.fullmatch(raw) is None:
        raise GuardError(f"{name} in the env file is not a number")
    if int(raw) == dev_port:
        raise GuardError(f"{name} in the env file equals the dev stack's port")


@dataclass(frozen=True)
class Plan:
    """What a guarded run will use, taken from the env file."""

    env_file: Path
    env: dict[str, str]
    allowlist_size: int
    log_dir: Path
    landing: str


def check_guards(env_file: str, *, repo_root: Path = REPO_ROOT) -> Plan:
    """Raise GuardError unless this env file is safe to point a real-data run at.

    Runs before anything starts. Messages name the variable that is wrong, never its value.
    """
    if not env_file:
        raise GuardError("ENV_FILE is not set (make real-ingest ENV_FILE=<path to your env file>)")
    path = Path(env_file)
    if path.name in DEV_ENV_FILE_NAMES:
        raise GuardError("ENV_FILE is a dev env file (.env / .env.example); use a real env file")
    if not path.is_file():
        raise GuardError("ENV_FILE does not exist or is not a file")
    env = parse_env_file(path)

    project = env.get("COMPOSE_PROJECT_NAME", "")
    if not project:
        raise GuardError("COMPOSE_PROJECT_NAME is not set in the env file")
    if project == DEV_COMPOSE_PROJECT:
        raise GuardError("COMPOSE_PROJECT_NAME in the env file is the dev project name")
    _port(env, "POSTGRES_HOST_PORT", DEV_POSTGRES_PORT)
    _port(env, "DASHBOARD_PORT", DEV_DASHBOARD_PORT)

    db_name = env.get("GCW_DB_NAME", "")
    if not db_name:
        raise GuardError("GCW_DB_NAME is not set in the env file")
    if db_name == DEV_DB_NAME:
        raise GuardError("GCW_DB_NAME in the env file is the dev database")

    try:
        allowlist = parse_allowlist(env.get(ALLOWLIST_VAR, ""))
    except AllowlistError as exc:
        raise GuardError(str(exc)) from None
    if not allowlist:
        raise GuardError(f"{ALLOWLIST_VAR} is not set or is empty in the env file")

    log_dir_raw = env.get("GCW_LOG_DIR", "")
    if not log_dir_raw:
        raise GuardError("GCW_LOG_DIR is not set in the env file")
    log_dir = Path(log_dir_raw).expanduser()
    if not log_dir.is_dir():
        raise GuardError("GCW_LOG_DIR does not exist or is not a directory")
    if log_dir.resolve().is_relative_to(repo_root.resolve()):
        raise GuardError("GCW_LOG_DIR is inside the repo; the full log must live outside it")

    return Plan(
        env_file=path,
        env=env,
        allowlist_size=len(allowlist),
        log_dir=log_dir,
        landing=env.get("GCW_LANDING_HOST_DIR", DEFAULT_LANDING),
    )


# --- Runner --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    name: str
    command: list[str]


def build_steps(plan: Plan) -> list[Step]:
    return [
        Step(
            "check-landing", [sys.executable, "-m", "gcwrapped.cli", "check-landing", plan.landing]
        ),
        Step("e2e", ["make", "e2e", f"ENV_FILE={plan.env_file}"]),
    ]


def step_header(step: Step) -> str:
    """Fixed text from code (the step name is ours, never from the run), shown before each step."""
    return f"== step: {step.name} =="


def _run_step(step: Step, env: Mapping[str, str], log: TextIO, show: Callable[[str], None]) -> int:
    """Run one step: every output line goes to the private log, allowlisted lines to the screen."""
    with subprocess.Popen(
        step.command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        env=dict(env),
        cwd=REPO_ROOT,
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            log.write(line)
            shown = summarize_line(line)
            if shown is not None:
                show(shown)
        return proc.wait()


def run(
    env_file: str,
    *,
    steps_for: Callable[[Plan], list[Step]] = build_steps,
    show: Callable[[str], None] = lambda s: print(s, flush=True),
) -> int:
    """Guards, then each step in order. Returns the failing step's exit status, or 0."""
    try:
        plan = check_guards(env_file)
    except GuardError as exc:
        show(f"real-ingest: refused: {exc}")
        return 2
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = plan.log_dir / f"real-ingest-{stamp}.log"
    # ENV_FILE is set for every step so host-side gcw commands read this file, not the repo's .env.
    env = {**os.environ, **plan.env, "ENV_FILE": str(plan.env_file)}
    started = time.perf_counter()
    status = 0
    failed = ""
    try:
        # 0600, exclusive: the log holds raw pipeline output, so it is private and never reused.
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as log:
            for step in steps_for(plan):
                header = step_header(step)
                log.write(header + "\n")
                show(header)
                status = _run_step(step, env, log, show)
                if status != 0:
                    failed = step.name
                    break
    except OSError as exc:  # class name only: the message can contain a path
        show(f"real-ingest: failed at log setup ({type(exc).__name__}); nothing was run")
        return 1
    show(f"pipeline seconds: {time.perf_counter() - started:.2f}")
    if failed:
        show(f"real-ingest: failed at {failed}; full log in GCW_LOG_DIR")
        return status
    show("real-ingest: ok")
    return 0
