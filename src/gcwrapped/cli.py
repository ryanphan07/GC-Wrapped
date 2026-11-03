"""Command-line entry point (`gcw`)."""

import argparse
import logging
import time
from collections.abc import Sequence
from pathlib import Path

import psycopg
from pydantic import ValidationError

from gcwrapped.db import database_name, database_url
from gcwrapped.landing import find_export_roots
from gcwrapped.load import LoadError, load_messages
from gcwrapped.log import setup_logging
from gcwrapped.migrate import MigrationError, migrate
from gcwrapped.parsers import instagram_format as fmt
from gcwrapped.parsers.instagram import ParseError, ParseStats, parse_export
from gcwrapped.runs import latest_run

logger = logging.getLogger(__name__)

_NO_THREADS = "error: no message threads found under the given path(s)"
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
    check = sub.add_parser("check-landing", help="count export roots in a landing directory")
    check.add_argument("landing", type=Path, metavar="DIR")
    sub.add_parser("last-run", help="print counts from the latest raw.load_runs row")
    parse = sub.add_parser("parse", help="parse export(s) and print counts (no DB needed)")
    parse.add_argument(
        "paths",
        type=Path,
        nargs="+",
        help="export root(s); several roots are unioned and deduplicated",
    )
    return parser


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
    """Print the export root count; fail if there are none. Never prints folder names."""
    roots = find_export_roots(landing)
    if not roots:
        print(_NO_ROOTS)
        return 1
    print(f"export roots: {len(roots)}")
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
    """Parse and load. Prints counts only. Errors name the exception type, never its text."""
    if not any(fmt.find_thread_dirs(path) for path in paths):
        print(_NO_THREADS)  # checked first so a wrong path leaves no run row behind
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
        result = load_messages(url, parse_export(paths, stats), roots_count=len(paths))
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
    print(f"rows parsed: {result.rows_parsed}")
    print(f"rows inserted: {result.rows_inserted}")
    print(f"duplicates skipped: {result.duplicates_skipped}")
    print(f"elapsed seconds: {elapsed:.2f}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    if args.command == "parse":
        return _parse(args.paths)
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
    if args.command == "last-run":
        return _last_run()
    if args.command == "migrate":
        return _migrate()
    return 2  # unreachable while subcommands are required
