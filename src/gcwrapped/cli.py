"""Command-line entry point (`gcw`)."""

import argparse
import logging
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import psycopg
from pydantic import ValidationError

from gcwrapped import ledger, metrics, real_ingest
from gcwrapped.consent import AllowlistError, load_allowlist, require_allowlist
from gcwrapped.db import database_name, database_url
from gcwrapped.labeling import commands as labeling
from gcwrapped.landing import check_landing, find_export_roots
from gcwrapped.load import LoadError, load_messages
from gcwrapped.log import setup_logging
from gcwrapped.migrate import MigrationError, migrate
from gcwrapped.parse_check import check_export, render
from gcwrapped.parsers import instagram_format as fmt
from gcwrapped.parsers.instagram import (
    ParseError,
    ParseStats,
    parse_export,
    parse_export_consented,
)
from gcwrapped.runs import latest_run

logger = logging.getLogger(__name__)

TRACEBACK_VAR = "GCW_TRACEBACK"

_NO_THREADS = "error: no message threads found under the given path(s)"
_NO_ALLOWLISTED_THREADS = (
    "error: no allowlisted message threads found under the given path(s) "
    "(see GCW_THREAD_ALLOWLIST; `gcw check-landing` shows what matches)"
)
_NO_ROOTS = "error: no export roots found in the landing directory (one subfolder per export)"
_NO_DB_CONFIG = (
    "error: database not configured (set GCW_DB_NAME, POSTGRES_USER, POSTGRES_PASSWORD; "
    "DATABASE_URL, if set, must name the same database)"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gcw", description="GC Wrapped pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply unapplied SQL migrations to the target database")
    ingest = sub.add_parser("ingest", help="parse export(s) and load them into Postgres")
    ingest.add_argument(
        "paths",
        type=Path,
        nargs="*",
        help="export root(s); several roots are unioned and deduplicated",
    )
    ingest.add_argument(
        "--landing",
        type=Path,
        metavar="DIR",
        help="ingest every export root (subfolder) under DIR instead of listing paths",
    )
    check = sub.add_parser(
        "check-landing",
        help="consent check of a landing directory: counts of thread folders and allowlisted IDs",
    )
    check.add_argument("landing", type=Path, metavar="DIR")
    sub.add_parser("last-run", help="print counts from the latest raw.load_runs row")
    parse = sub.add_parser("parse", help="parse export(s) and print counts (no DB needed)")
    parse.add_argument(
        "paths",
        type=Path,
        nargs="+",
        help="export root(s); several roots are unioned and deduplicated",
    )
    pcheck = sub.add_parser(
        "parse-check", help="parse a whole export, count errors per class, print counts only"
    )
    pcheck.add_argument("path", type=Path, metavar="EXPORT_DIR", help="one export root")
    sub.add_parser(
        "metrics", help="aggregate counts report: warehouse, dbt and pytest (fixed labels only)"
    )
    sub.add_parser(
        "redaction-audit",
        help="redact every ingested message in memory and print aggregate counts only",
    )
    sub.add_parser(
        "consent-template",
        help="create or extend the per-sender consent ledger from the warehouse (counts only)",
    )
    sub.add_parser(
        "consent-load",
        help="validate the whole consent ledger, then load it into raw.sender_consent",
    )
    sub.add_parser(
        "label-tags",
        help="label the frozen 200-message tag sample in this terminal (D85, D86)",
    )
    sub.add_parser(
        "label-questions",
        help="write retrieval questions from the frozen candidates in this terminal (D86)",
    )
    sub.add_parser(
        "label-summary",
        help="aggregate counts about the labels so far (fixed lines only)",
    )
    real = sub.add_parser(
        "real-ingest",
        help="guarded pipeline run for a real-data env file; prints allowlisted lines only",
    )
    real.add_argument(
        "--env-file", default="", metavar="PATH", help="the env file (empty means not set)"
    )
    return parser


def _parse_check(path: Path) -> int:
    """Counts and exception class names only. Exit 1 if any error or no threads were found."""
    try:
        report = check_export(path)
    except Exception as exc:  # unexpected failure: class name only, no path, no message
        print(f"error: parse-check failed ({type(exc).__name__})")
        return 1
    if not report.threads:
        print(_NO_THREADS)
        return 1
    for line in render(report):
        print(line)
    return 1 if report.errors else 0


def _parse(paths: Sequence[Path]) -> int:
    """Print counts only. Never print message content, names or titles."""
    stats = ParseStats()
    start = time.perf_counter()
    try:
        for _ in parse_export(paths, stats):
            pass
    except ParseError as exc:
        print(f"error: {exc}")
        return 1
    elapsed = time.perf_counter() - start
    if not stats.thread_ids:
        print(_NO_THREADS)
        return 1
    print(f"threads: {len(stats.thread_ids)}")
    print(f"messages: {stats.messages}")
    print(f"duplicates dropped: {stats.duplicates_dropped}")
    print(f"elapsed seconds: {elapsed:.2f}")
    return 0


def _migrate() -> int:
    try:
        url = database_url()
    except ValidationError:
        print(_NO_DB_CONFIG)
        return 1
    print(f"target database: {database_name(url)}")
    try:
        applied = migrate(url)
    except MigrationError as exc:
        print(f"error: {exc}")
        return 1
    except psycopg.Error as exc:
        print(f"error: migrate failed ({type(exc).__name__})")
        return 1
    for version in applied:
        print(f"applied: {version}")
    print(f"migrations applied: {len(applied)}")
    return 0


def _check_landing(landing: Path) -> int:
    """Consent check: counts only, never folder names, IDs or paths inside the landing dir.

    Fails if there are no export roots, no thread folders, or none of the allowlisted IDs found.
    An empty allowlist is reported as 0 configured (and so fails), not as an error.
    """
    try:
        allowlist = load_allowlist()
    except AllowlistError as exc:
        print(f"error: {exc}")
        return 1
    report = check_landing(landing, allowlist)
    if not report.roots:
        print(_NO_ROOTS)
        return 1
    for line in report.lines():
        print(line)
    if report.thread_folders == 0:
        print("error: no thread folders found in the landing directory")
        return 1
    if not report.found:
        print("error: none of the allowlisted IDs were found (check GCW_THREAD_ALLOWLIST)")
        return 1
    return 0


def _last_run() -> int:
    """Print counts from the latest load run. Never prints anything but numbers and status."""
    try:
        url = database_url()
    except ValidationError:
        print(_NO_DB_CONFIG)
        return 1
    print(f"target database: {database_name(url)}")
    try:
        run = latest_run(url)
    except psycopg.Error as exc:
        print(f"error: could not read load runs ({type(exc).__name__})")
        return 1
    if run is None:
        print("error: no load runs recorded")
        return 1
    print(f"load id: {run.load_id}")
    print(f"status: {run.status}")
    print(f"rows parsed: {run.rows_parsed}")
    print(f"rows inserted: {run.rows_inserted}")
    elapsed = "n/a" if run.elapsed_seconds is None else f"{run.elapsed_seconds:.2f}"
    print(f"elapsed seconds: {elapsed}")
    return 0


def _ingest(paths: Sequence[Path]) -> int:
    """Parse and load allowlisted threads. Counts only. Errors name the exception type only."""
    try:
        allowlist = require_allowlist()  # deny by default: no allowlist, no ingest
    except AllowlistError as exc:
        print(f"error: {exc}")
        return 1
    if not any(fmt.select_threads(path, allowlist).threads for path in paths):
        print(_NO_ALLOWLISTED_THREADS)  # checked first so a wrong path leaves no run row behind
        return 1
    try:
        url = database_url()
    except ValidationError:
        print(_NO_DB_CONFIG)
        return 1
    print(f"target database: {database_name(url)}")
    stats = ParseStats()
    start = time.perf_counter()
    try:
        result = load_messages(
            url, parse_export_consented(paths, allowlist, stats), roots_count=len(paths)
        )
    except LoadError as exc:  # class name, row index, field name only
        print(f"error: ingest failed ({exc.summary})")
        return 1
    except psycopg.errors.UndefinedTable:
        print("error: raw tables missing; run `gcw migrate` first")
        return 1
    except Exception as exc:  # psycopg messages can echo row data, so print the type only
        print(f"error: ingest failed ({type(exc).__name__})")
        return 1
    elapsed = time.perf_counter() - start
    print(f"threads allowlisted: {len(stats.thread_ids)}")
    print(f"folders skipped (not allowlisted): {stats.folders_not_allowlisted}")
    print(f"folders skipped (unrecognised shape): {stats.folders_other_shape}")
    print(f"rows parsed: {result.rows_parsed}")
    print(f"rows inserted: {result.rows_inserted}")
    print(f"duplicates skipped: {result.duplicates_skipped}")
    print(f"elapsed seconds: {elapsed:.2f}")
    return 0


def _run(argv: Sequence[str] | None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    if args.command == "parse":
        return _parse(args.paths)
    if args.command == "parse-check":
        return _parse_check(args.path)
    if args.command == "ingest":
        if bool(args.paths) == (args.landing is not None):
            build_parser().error("give export path(s) or --landing DIR, not both or neither")
        if args.landing is not None:
            roots = find_export_roots(args.landing)
            if not roots:
                print(_NO_ROOTS)
                return 1
            return _ingest(roots)
        return _ingest(args.paths)
    if args.command == "check-landing":
        return _check_landing(args.landing)
    if args.command == "real-ingest":
        return real_ingest.run(args.env_file)
    if args.command == "metrics":
        return metrics.run()
    if args.command == "redaction-audit":
        # Imported here: the redaction dependencies are dev-only, and the Airflow image's venv
        # (which runs every other command) does not have them.
        from gcwrapped import redaction_audit

        return redaction_audit.run()
    if args.command == "label-tags":
        return labeling.run_label_tags()
    if args.command == "label-questions":
        return labeling.run_label_questions()
    if args.command == "label-summary":
        return labeling.run_label_summary()
    if args.command == "consent-template":
        return ledger.run_template()
    if args.command == "consent-load":
        return ledger.run_load()
    if args.command == "last-run":
        return _last_run()
    if args.command == "migrate":
        return _migrate()
    return 2  # unreachable while subcommands are required


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. An unhandled Exception prints its type name only, then exits 1.

    Settings load before any command's own error handling, and pydantic or logging errors echo the
    raw input value, so str(exc) and tracebacks must never reach the terminal. SystemExit and
    KeyboardInterrupt are not Exceptions and pass through. GCW_TRACEBACK=1 re-raises instead; it is
    read from os.environ because Settings may be what failed. Dev only, synthetic data only.
    """
    try:
        return _run(argv)
    except Exception as exc:
        if os.environ.get(TRACEBACK_VAR) == "1":
            raise
        print(f"gcw: failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
