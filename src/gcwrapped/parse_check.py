"""Count-only parser check for a full export: `make parse-check EXPORT_DIR=<path>`.

Runs the real parser functions over every thread, file and message with no database, catching
errors per message so one bad message does not hide the rest. It prints counts and exception
class names only. It never prints exception messages (pydantic and others echo input values),
folder names, or any content. The production path (`parse_export`) is unchanged and still stops
at the first error.
"""

import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gcwrapped.models import ParsedMessage
from gcwrapped.parsers import instagram_format as fmt
from gcwrapped.parsers.instagram import (
    ParseError,
    _Identity,
    _identity,
    _read_json,
    fix_mojibake,
    parse_message,
)

_FOLDER_SHAPES = ("<name>_<digits>", "<digits>", "other")


@dataclass
class CheckReport:
    """Everything parse-check prints. Only counts and exception class names."""

    folder_shapes: Counter[str] = field(default_factory=Counter)
    messages: int = 0
    duplicates_dropped: int = 0
    mojibake_fields: Counter[str] = field(default_factory=Counter)
    attachments: Counter[str] = field(default_factory=Counter)
    external_uris: int = 0
    errors: Counter[str] = field(default_factory=Counter)
    elapsed_seconds: float = 0.0

    @property
    def threads(self) -> int:
        return sum(self.folder_shapes.values())


def _changed(text: object) -> bool:
    return isinstance(text, str) and fix_mojibake(text) != text


def _count_mojibake(raw: dict[str, Any], report: CheckReport) -> None:
    """Count mapped text fields that fix_mojibake changes (counts per field name only)."""
    for key in (fmt.KEY_SENDER, fmt.KEY_CONTENT):
        if _changed(raw.get(key)):
            report.mojibake_fields[key] += 1
    for item in raw.get(fmt.KEY_REACTIONS) or []:
        if isinstance(item, dict):
            for key in (fmt.KEY_REACTION_ACTOR, fmt.KEY_REACTION_EMOJI):
                if _changed(item.get(key)):
                    report.mojibake_fields[f"{fmt.KEY_REACTIONS}.{key}"] += 1


def _count_attachments(raw: dict[str, Any], report: CheckReport) -> None:
    for kind in fmt.ATTACHMENT_KEYS:
        for item in raw.get(kind) or []:
            if isinstance(item, dict) and isinstance(item.get(fmt.KEY_URI), str):
                report.attachments[kind] += 1
                if fmt.key_basename(item[fmt.KEY_URI]) is None:
                    report.external_uris += 1


def _check_message(
    raw: object,
    *,
    tid: str,
    title: str | None,
    source_file: str,
    seen: Counter[_Identity],
    report: CheckReport,
) -> ParsedMessage:
    """Same steps as the production loop (identity, occurrence, parse_message), plus counting."""
    if not isinstance(raw, dict):
        raise ParseError("message entry is not an object")
    _count_mojibake(raw, report)
    _count_attachments(raw, report)
    ident = _identity(raw)
    occurrence = seen[ident]
    seen[ident] += 1
    return parse_message(
        raw, thread_id=tid, thread_title=title, source_file=source_file, occurrence=occurrence
    )


def check_export(root: Path) -> CheckReport:
    """Parse one export root, counting instead of stopping on errors."""
    report = CheckReport()
    start = time.perf_counter()
    yielded: set[str] = set()
    for thread_dir in fmt.find_thread_dirs(root):
        report.folder_shapes[fmt.thread_folder_shape(thread_dir)] += 1
        try:
            tid = fmt.thread_id(thread_dir)
        except ValueError as exc:
            report.errors[type(exc).__name__] += 1
            continue
        seen: Counter[_Identity] = Counter()
        for path in fmt.message_files(thread_dir):
            try:
                data = _read_json(path)
            except ParseError as exc:
                report.errors[type(exc).__name__] += 1
                continue
            title = data.get(fmt.KEY_THREAD_TITLE)
            if _changed(title):
                report.mojibake_fields[fmt.KEY_THREAD_TITLE] += 1
            for raw in data[fmt.KEY_MESSAGES]:
                try:
                    msg = _check_message(
                        raw,
                        tid=tid,
                        title=title if isinstance(title, str) else None,
                        source_file=path.relative_to(root).as_posix(),
                        seen=seen,
                        report=report,
                    )
                except Exception as exc:  # class name only, never the message
                    report.errors[type(exc).__name__] += 1
                    continue
                if msg.message_key in yielded:
                    report.duplicates_dropped += 1
                else:
                    yielded.add(msg.message_key)
                    report.messages += 1
    report.elapsed_seconds = time.perf_counter() - start
    return report


def render(report: CheckReport) -> list[str]:
    """Report lines: counts and exception class names only."""
    lines = [f"threads: {report.threads}"]
    lines += [f"  {shape}: {report.folder_shapes[shape]}" for shape in _FOLDER_SHAPES]
    lines += [
        f"messages parsed: {report.messages}",
        f"duplicates dropped: {report.duplicates_dropped}",
        f"text fields changed by fix_mojibake: {sum(report.mojibake_fields.values())}",
    ]
    lines += [f"  {name}: {n}" for name, n in sorted(report.mojibake_fields.items())]
    lines.append("attachments by kind:")
    lines += [f"  {kind}: {report.attachments[kind]}" for kind in fmt.ATTACHMENT_KEYS]
    lines.append(f"external attachment URIs: {report.external_uris}")
    lines.append(f"errors: {sum(report.errors.values())}")
    lines += [f"  {name}: {n}" for name, n in sorted(report.errors.items())]
    lines.append(f"elapsed seconds: {report.elapsed_seconds:.2f}")
    return lines
