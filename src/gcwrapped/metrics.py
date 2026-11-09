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
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from gcwrapped.config import env_file_path
from gcwrapped.db import database_name, database_url
from gcwrapped.runs import RunSummary, latest_run

ALLOWED_DATABASES = frozenset({"gcw_dev", "gcw_real", "gcw_ci", "gcw_test"})
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
STEP_RENDER = "render"

# Placeholder profile values for `dbt parse`, which never connects. Real settings are not passed.
DBT_PARSE_ENV = {
    "GCW_DB_NAME": "gcw_parse_only",
    "POSTGRES_USER": "parse_only",
    "POSTGRES_PASSWORD": "parse_only",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
}

# Process variables the dbt subprocess may inherit, each copied only when set (D67). An allowlist,
# not an override list: a new secret in the parent environment (an API key, any real.env key)
# never reaches dbt. Never add POSTGRES_*, GCW_*, DATABASE_URL, DASHBOARD_DATABASE_URL, ENV_FILE or
# any *_KEY / *_TOKEN / *_SECRET / *_PASSWORD name. If dbt needs another variable, add it here.
DBT_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)
# dbt reads DBT_<FLAG> from the environment; this is --no-send-anonymous-usage-stats.
DBT_TELEMETRY_ENV = {"DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}

# One fullmatch regex per output label, in output order. run() checks every rendered line against
# these before printing anything, so the leak guarantee holds on values the tests never saw.
LINE_SHAPES = (
    r"target database: (gcw_dev|gcw_real|gcw_ci|gcw_test)",
    r"warehouse raw messages: \d+",
    r"warehouse modeled messages: \d+",
    r"warehouse threads: \d+",
    r"warehouse distinct senders: \d+",
    r"latest load id: \d+",
    r"latest load status: (succeeded|failed)",
    r"latest load rows parsed: \d+",
    r"latest load rows inserted: \d+",
    r"latest load seconds: \d+\.\d{2}",
    r"consent senders yes: \d+",
    r"external-eligible messages: \d+",
    r"dbt models: \d+",
    r"dbt data tests: \d+",
    r"dbt generic tests: \d+",
    r"dbt singular tests: \d+",
    r"dbt unit tests: \d+",
    r"dbt sources: \d+",
    r"dbt seeds: \d+",
    r"dbt hooks: \d+",
    r"dbt buildable nodes: \d+",
    r"pytest unit tests collected: \d+",
    r"pytest non-unit tests collected: \d+",
    r"metrics: ok",
)


class MetricsError(Exception):
    """A metrics precondition failed. Only the class name is ever printed."""


class UnexpectedDatabaseError(MetricsError):
    """The target database is not one of the known names."""


class NoLoadRunError(MetricsError):
    """raw.load_runs has no finished run to report."""


class ShapeError(MetricsError):
    """A rendered line did not match its allowed shape, or the line count was wrong."""


class PytestCountError(MetricsError):
    """pytest's collection summary line was not found."""


@dataclass(frozen=True)
class WarehouseStats:
    database: str
    raw_messages: int
    modeled_messages: int
    threads: int
    distinct_senders: int
    load_id: int
    load_status: str
    rows_parsed: int
    rows_inserted: int
    load_seconds: float
    consent_senders_yes: int  # raw.sender_consent rows with consent = 'yes' (D69)
    external_eligible_messages: int  # fct_message_eligibility rows with external_ok


@dataclass(frozen=True)
class DbtCounts:
    models: int
    data_tests: int
    generic_tests: int
    singular_tests: int
    unit_tests: int
    sources: int
    seeds: int
    hooks: int  # on-run-start/on-run-end hooks: manifest "operation" nodes, which dbt build runs

    @property
    def buildable(self) -> int:
        """What `dbt build` executes and reports in PASS=: models, seeds, tests, unit tests, hooks.

        The project has no snapshots; one added later must be counted here too.
        """
        return self.models + self.seeds + self.data_tests + self.unit_tests + self.hooks


@dataclass(frozen=True)
class PytestCounts:
    unit: int
    non_unit: int


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
    url: str,
    *,
    modeled_messages: tuple[str, str] = ("analytics", "fct_messages"),
    eligibility: tuple[str, str] = ("analytics", "fct_message_eligibility"),
) -> WarehouseStats:
    """Counts from the raw messages table, the dbt messages model and the latest load run.

    `modeled_messages` and `eligibility` are (schema, table); the defaults are the dbt
    fct_messages and fct_message_eligibility marts. The consent count reads raw.sender_consent.
    Distinct senders are distinct sender names across the model and threads are distinct thread
    IDs; both are counted inside the database and only the numbers come back.
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
        consent_row = conn.execute(
            "SELECT count(*) FROM raw.sender_consent WHERE consent = 'yes'"
        ).fetchone()
        eligible_row = conn.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE external_ok").format(
                sql.Identifier(*eligibility)
            )
        ).fetchone()
    run = latest_run(url)
    if raw_row is None or modeled_row is None or consent_row is None or eligible_row is None:
        raise MetricsError
    return _stats(name, raw_row[0], modeled_row, run, consent_row[0], eligible_row[0])


def _stats(
    database: str,
    raw_messages: int,
    modeled_row: tuple[int, int, int],
    run: RunSummary | None,
    consent_senders_yes: int,
    external_eligible_messages: int,
) -> WarehouseStats:
    # A run still open has no duration; a run with no counts cannot be reported as numbers.
    if (
        run is None
        or run.status not in ("succeeded", "failed")
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
        distinct_senders=modeled_row[2],
        load_id=run.load_id,
        load_status=run.status,
        rows_parsed=run.rows_parsed,
        rows_inserted=run.rows_inserted,
        load_seconds=run.elapsed_seconds,
        consent_senders_yes=consent_senders_yes,
        external_eligible_messages=external_eligible_messages,
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
        hooks=len(of_type("operation")),
    )


def dbt_counts(project_dir: Path = DBT_DIR) -> DbtCounts:
    """Run `dbt parse` with placeholder credentials, then count the manifest it wrote.

    dbt never connects to parse, so ENV_FILE's database settings are not passed. The subprocess
    environment is only DBT_ENV_ALLOWLIST (when set), the placeholder profile values and
    telemetry off, never the rest of os.environ (D67). Target and log
    directories are a temporary directory, so nothing is written under the repo except installed
    packages (dbt/dbt_packages, gitignored package code). dbt output is captured, never shown.
    """
    env = {
        **{name: os.environ[name] for name in DBT_ENV_ALLOWLIST if name in os.environ},
        **DBT_PARSE_ENV,
        **DBT_TELEMETRY_ENV,
    }
    dbt = str(Path(sys.executable).parent / "dbt")
    with tempfile.TemporaryDirectory() as tmp:
        base = ["--project-dir", str(project_dir), "--profiles-dir", str(project_dir)]
        logs = ["--log-path", str(Path(tmp) / "logs")]
        # `dbt deps` rejects --target-path (it builds no target), so each command gets only the
        # options it accepts.
        commands = [["parse", *base, "--target-path", str(Path(tmp) / "target"), *logs]]
        if not (project_dir / "dbt_packages").is_dir():
            commands.insert(0, ["deps", *base, *logs])
        for command in commands:
            subprocess.run(
                [dbt, *command],
                check=True,
                capture_output=True,
                env=env,
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
            )
        manifest = json.loads((Path(tmp) / "target" / "manifest.json").read_text(encoding="utf-8"))
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
    return PytestCounts(unit=unit, non_unit=total - unit)


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
        f"warehouse distinct senders: {w.distinct_senders}",
        f"latest load id: {w.load_id}",
        f"latest load status: {w.load_status}",
        f"latest load rows parsed: {w.rows_parsed}",
        f"latest load rows inserted: {w.rows_inserted}",
        f"latest load seconds: {w.load_seconds:.2f}",
        f"consent senders yes: {w.consent_senders_yes}",
        f"external-eligible messages: {w.external_eligible_messages}",
        f"dbt models: {d.models}",
        f"dbt data tests: {d.data_tests}",
        f"dbt generic tests: {d.generic_tests}",
        f"dbt singular tests: {d.singular_tests}",
        f"dbt unit tests: {d.unit_tests}",
        f"dbt sources: {d.sources}",
        f"dbt seeds: {d.seeds}",
        f"dbt hooks: {d.hooks}",
        f"dbt buildable nodes: {d.buildable}",
        f"pytest unit tests collected: {p.unit}",
        f"pytest non-unit tests collected: {p.non_unit}",
        "metrics: ok",
    ]


def check_shapes(lines: list[str]) -> None:
    """Raise ShapeError unless there is one line per pattern, in order, each fully matching."""
    if len(lines) != len(LINE_SHAPES):
        raise ShapeError
    for line, shape in zip(lines, LINE_SHAPES, strict=True):
        if re.fullmatch(shape, line) is None:
            raise ShapeError


def run(steps: Steps = DEFAULT_STEPS) -> int:
    """Run every step, render and shape-check the report, then print it.

    On any failure print one line (step and exception type only) and exit non-zero.
    """
    step = STEP_DATABASE
    try:
        steps.database()
        step = STEP_WAREHOUSE
        warehouse = steps.warehouse()
        step = STEP_DBT
        dbt = steps.dbt()
        step = STEP_PYTEST
        collected = steps.pytest()
        step = STEP_RENDER
        lines = render(warehouse, dbt, collected)
        check_shapes(lines)
    except Exception as exc:  # type name only: messages from drivers and subprocesses can echo data
        print(f"metrics: failed at {step} ({type(exc).__name__})")
        return 1
    for line in lines:
        print(line)
    return 0
