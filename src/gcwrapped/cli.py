"""Command-line entry point (`gcw`)."""

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from gcwrapped.log import setup_logging

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gcw", description="GC Wrapped pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="parse an export and load it into Postgres")
    ingest.add_argument("file", type=Path, help="path to the export file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    if args.command == "ingest":
        logger.error("ingest not implemented")
        return 1
    return 2  # unreachable while subcommands are required
