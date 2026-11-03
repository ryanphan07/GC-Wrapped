"""Logging setup. Output is key=value so it greps cleanly.

Privacy: callers log counts, IDs, durations and hashes only, never message bodies,
sender names, phone numbers or emails.
"""

import json
import logging

from gcwrapped.config import get_settings


class KeyValueFormatter(logging.Formatter):
    """Format records as `ts=... level=... logger=... msg="..."`."""

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z")
        line = (
            f"ts={ts} level={record.levelname} logger={record.name} "
            f"msg={json.dumps(record.getMessage())}"
        )
        if record.exc_info:
            # Type name only: exception text can echo input data.
            line += f" exc={record.exc_info[0].__name__ if record.exc_info[0] else 'unknown'}"
        return line


def setup_logging(level: str | None = None) -> None:
    """Configure the root logger. Level defaults to LOG_LEVEL from settings."""
    handler = logging.StreamHandler()
    handler.setFormatter(KeyValueFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel((level or get_settings().log_level).upper())
