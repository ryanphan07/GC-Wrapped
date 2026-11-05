"""`make metrics`: a fixed-label, aggregate-only report of warehouse, dbt and pytest counts.

Why it looks like this (decisions.md, 2026-11-05): Ryan runs it on the real database and copies
numbers into METRICS.md after reading them, so every output line is a fixed label plus a number
(plus the target database name and the load status), and nothing is printed until every step has
succeeded. A failure prints one fixed line with the step name and the exception's TYPE name only,
never str(exc): driver and subprocess messages can echo values.

Sources: the warehouse named by ENV_FILE (counts only), dbt's target/manifest.json after
`dbt parse` (what is defined, split by resource type; needs no database), and `pytest
--collect-only` (what exists, nothing is run). It never edits a tracked file.
"""

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from gcwrapped.config import env_file_path, get_database_settings
from gcwrapped.db import database_name, database_url
from gcwrapped.runs import RunSummary, latest_run

ALLOWED_DATABASES = frozenset({"gcw_dev", "gcw_real", "gcw_ci"})
DBT_PROJECT_NAME = "gcwrapped"
DBT_DIR = Path("dbt")
# The selection `make test` uses (a test pins the Makefile to this). The complement is derived as
# "everything collected minus this", so nothing falls between the two numbers.
UNIT_PYTEST_ARGS = (
    "-m",
    "not integration and not warehouse",
    "--ignore=tests/test_dashboard_warehouse.py",
)
SUBPROCESS_TIMEOUT_SECONDS = 600

STEP_DATABASE = "database"
STEP_WAREHOUSE = "warehouse"
STEP_DBT = "dbt"
STEP_PYTEST = "pytest"


class MetricsError(Exception):
    """A metrics precondition failed. Only the class name is ever printed."""


class UnexpectedDatabaseError(MetricsError):
    """The target database is not one of the known names."""


class NoLoadRunError(MetricsError):
    """raw.load_runs has no finished run to report."""


class PytestCountError(MetricsError):
    """pytest's collection summary line was not found."""


@dataclass(frozen=True)
class WarehouseStats:
    database: str
    raw_messages: int
    modeled_messages: int
    threads: int
    participants: int
    load_id: int
    load_status: str
    rows_parsed: int
    rows_inserted: int
    load_seconds: float


@dataclass(frozen=True)
class DbtCounts:
    models: int
    data_tests: int
    generic_tests: int
    singular_tests: int
    unit_tests: int
    sources: int
    seeds: int


@dataclass(frozen=True)
class PytestCounts:
    unit: int
    integration: int


@dataclass(frozen=True)
class Steps:
    """One callable per step, so tests can replace the slow or external ones."""

    database: Callable[[], None]
    warehouse: Callable[[], WarehouseStats]
    dbt: Callable[[], DbtCounts]
    pytest: Callable[[], PytestCounts]


# --- Step 1: the database ------------------------------------------------------------------------


def start_database() -> None:
    """Start only the db service of the compose project ENV_FILE names; leave it running.

    Output is captured, not shown: compose prints container names. `make down` stops it.
    """
    subprocess.run(
        ["docker", "compose", "--env-file", env_file_path(), "up", "-d", "--wait", "db"],
        check=True,
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )


# --- Step 2: the warehouse -----------------------------------------------------------------------


def warehouse_stats(
    url: str, *, modeled_messages: tuple[str, str] = ("analytics", "fct_messages")
) -> WarehouseStats:
    """Counts from the raw messages table, the dbt messages model and the latest load run.

    `modeled_messages` is (schema, table); the default is the dbt fct_messages mart. Participants
    are distinct sender names across the model and threads are distinct thread IDs; both are
    counted inside the database and only the numbers come back.
    """
    name = database_name(url)
    if name not in ALLOWED_DATABASES:
        raise UnexpectedDatabaseError
    modeled = sql.Identifier(*modeled_messages)
    with psycopg.connect(url) as conn:
        raw_row = conn.execute("SELECT count(*) FROM raw.messages").fetchone()
        modeled_row = conn.execute(
            sql.SQL(
                "SELECT count(*), count(DISTINCT thread_id), count(DISTINCT sender_name) FROM {}"
            ).format(modeled)
        ).fetchone()
    run = latest_run(url)
    if raw_row is None or modeled_row is None:
        raise MetricsError
    return _stats(name, raw_row[0], modeled_row, run)


def _stats(
    database: str, raw_messages: int, modeled_row: tuple[int, int, int], run: RunSummary | None
) -> WarehouseStats:
    # A run still open has no duration; a run with no counts cannot be reported as numbers.
    if (
        run is None
        or run.elapsed_seconds is None
        or run.rows_parsed is None
        or run.rows_inserted is None
    ):
        raise NoLoadRunError
    return WarehouseStats(
        database=database,
        raw_messages=raw_messages,
        modeled_messages=modeled_row[0],
        threads=modeled_row[1],
        participants=modeled_row[2],
        load_id=run.load_id,
        load_status=run.status,
        rows_parsed=run.rows_parsed,
        rows_inserted=run.rows_inserted,
        load_seconds=run.elapsed_seconds,
    )


# --- Step 3: dbt (manifest) ----------------------------------------------------------------------


def count_manifest(manifest: Mapping[str, Any]) -> DbtCounts:
    """Counts by resource type for this dbt project only (installed packages are excluded).

    A generic test carries `test_metadata`; a singular test is a SQL file in tests/ and has none.
    """

    def ours(node: Mapping[str, Any]) -> bool:
        return bool(node.get("package_name") == DBT_PROJECT_NAME)

    nodes = [n for n in manifest.get("nodes", {}).values() if ours(n)]

    def of_type(kind: str) -> list[Mapping[str, Any]]:
        return [n for n in nodes if n.get("resource_type") == kind]

    tests = of_type("test")
    generic = [t for t in tests if "test_metadata" in t]
    return DbtCounts(
        models=len(of_type("model")),
        data_tests=len(tests),
        generic_tests=len(generic),
        singular_tests=len(tests) - len(generic),
        unit_tests=sum(1 for u in manifest.get("unit_tests", {}).values() if ours(u)),
        sources=sum(1 for s in manifest.get("sources", {}).values() if ours(s)),
        seeds=len(of_type("seed")),
    )


def dbt_counts(project_dir: Path = DBT_DIR) -> DbtCounts:
    """Run `dbt parse` with the repo's usual invocation, then count target/manifest.json.

    Settings come from ENV_FILE (not from whatever the shell exported) and are handed to dbt as its
    profile's environment variables. dbt output is captured, never shown. Packages are installed
    first only if missing; both only write dbt's own gitignored directories.
    """
    db = get_database_settings()
    env = {
        **os.environ,
        "GCW_DB_NAME": db.gcw_db_name,
        "POSTGRES_USER": db.postgres_user,
        "POSTGRES_PASSWORD": db.postgres_password.get_secret_value(),
        "POSTGRES_HOST": db.postgres_host,
        "POSTGRES_PORT": str(db.postgres_port),
    }
    dbt = str(Path(sys.executable).parent / "dbt")
    common = ["--project-dir", str(project_dir), "--profiles-dir", str(project_dir)]
    commands = [["parse", *common]]
    if not (project_dir / "dbt_packages").is_dir():
        commands.insert(0, ["deps", *common])
    for command in commands:
        subprocess.run(
            [dbt, *command],
            check=True,
            capture_output=True,
            env=env,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    manifest = json.loads((project_dir / "target" / "manifest.json").read_text(encoding="utf-8"))
    return count_manifest(manifest)


# --- Step 4: pytest (collection only) ------------------------------------------------------------

_COLLECTED = re.compile(r"^(\d+)(?:/\d+)? tests? collected", re.MULTILINE)


def parse_collected(output: str) -> int:
    """The selected-test count from pytest's `-q --collect-only` summary line."""
    match = _COLLECTED.search(output)
    if match is None:
        raise PytestCountError
    return int(match[1])


def _collect(*args: str) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )
    return parse_collected(result.stdout)


def pytest_counts() -> PytestCounts:
    """Tests `make test` selects, and every other test (deselected by marker or ignored)."""
    unit = _collect(*UNIT_PYTEST_ARGS)
    total = _collect()
    return PytestCounts(unit=unit, integration=total - unit)


# --- Output --------------------------------------------------------------------------------------

DEFAULT_STEPS = Steps(
    database=start_database,
    warehouse=lambda: warehouse_stats(database_url()),
    dbt=dbt_counts,
    pytest=pytest_counts,
)


def render(w: WarehouseStats, d: DbtCounts, p: PytestCounts) -> list[str]:
    """The report, in order. Every line is a fixed label and a number (or database/status word)."""
    return [
        f"target database: {w.database}",
        f"warehouse raw messages: {w.raw_messages}",
        f"warehouse modeled messages: {w.modeled_messages}",
        f"warehouse threads: {w.threads}",
        f"warehouse participants: {w.participants}",
        f"latest load id: {w.load_id}",
        f"latest load status: {w.load_status}",
        f"latest load rows parsed: {w.rows_parsed}",
        f"latest load rows inserted: {w.rows_inserted}",
        f"latest load seconds: {w.load_seconds:.2f}",
        f"dbt models: {d.models}",
        f"dbt data tests: {d.data_tests}",
        f"dbt generic tests: {d.generic_tests}",
        f"dbt singular tests: {d.singular_tests}",
        f"dbt unit tests: {d.unit_tests}",
        f"dbt sources: {d.sources}",
        f"dbt seeds: {d.seeds}",
        f"pytest unit tests collected: {p.unit}",
        f"pytest integration tests collected: {p.integration}",
        "metrics: ok",
    ]


def run(steps: Steps = DEFAULT_STEPS) -> int:
    """Run every step, then print the report. On any failure print one line and exit non-zero."""
    step = STEP_DATABASE
    try:
        steps.database()
        step = STEP_WAREHOUSE
        warehouse = steps.warehouse()
        step = STEP_DBT
        dbt = steps.dbt()
        step = STEP_PYTEST
        collected = steps.pytest()
    except Exception as exc:  # type name only: messages from drivers and subprocesses can echo data
        print(f"metrics: failed at {step} ({type(exc).__name__})")
        return 1
    for line in render(warehouse, dbt, collected):
        print(line)
    return 0
