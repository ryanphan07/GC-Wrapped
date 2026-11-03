"""Command-line entry point (`gcw`)."""

import argparse
import logging
import time
from collections.abc import Sequence
from pathlib import Path

import psycopg
from pydantic import ValidationError

from gcwrapped.db import database_name, database_url
from gcwrapped.load import load_messages
from gcwrapped.log import setup_logging
from gcwrapped.migrate import MigrationError, migrate
from gcwrapped.parsers.instagram import ParseError, ParseStats, parse_export

logger = logging.getLogger(__name__)

_NO_DB_CONFIG = (
    "error: database not configured (set DATABASE_URL, POSTGRES_USER, POSTGRES_PASSWORD)"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gcw", description="GC Wrapped pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply unapplied SQL migrations to the target database")
    ingest = sub.add_parser("ingest", help="parse export(s) and load them into Postgres")
    ingest.add_argument(
        "paths",
        type=Path,
        nargs="+",
        help="export root(s); several roots are unioned and deduplicated",
    )
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
        print("error: no message threads found under the given path(s)")
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


def _ingest(paths: Sequence[Path]) -> int:
    """Parse and load. Prints counts only. Errors name the exception type, never its text."""
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
    except ParseError as exc:  # messages are value-free by design
        print(f"error: {exc}")
        return 1
    except psycopg.errors.UndefinedTable:
        print("error: raw tables missing; run `gcw migrate` first")
        return 1
    except Exception as exc:  # psycopg messages can echo row data, so print the type only
        print(f"error: ingest failed ({type(exc).__name__})")
        return 1
    elapsed = time.perf_counter() - start
    if not stats.thread_ids:
        print("error: no message threads found under the given path(s)")
        return 1
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
        return _ingest(args.paths)
    if args.command == "migrate":
        return _migrate()
    return 2  # unreachable while subcommands are required
