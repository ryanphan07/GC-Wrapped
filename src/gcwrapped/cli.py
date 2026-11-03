"""Command-line entry point (`gcw`)."""

import argparse
import logging
import time
from collections.abc import Sequence
from pathlib import Path

from gcwrapped.log import setup_logging
from gcwrapped.parsers.instagram import ParseError, ParseStats, parse_export

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gcw", description="GC Wrapped pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="parse an export and load it into Postgres")
    ingest.add_argument("file", type=Path, help="path to the export file")
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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    if args.command == "parse":
        return _parse(args.paths)
    if args.command == "ingest":
        logger.error("ingest not implemented")
        return 1
    return 2  # unreachable while subcommands are required
