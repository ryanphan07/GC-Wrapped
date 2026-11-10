"""Label files: fixed-schema CSV writers and a validating loader (D86).

The same functions write Ryan's private labels and read both those and the committed synthetic
sets (tests/fixtures/eval/), so the eval harness has one loader. A file is rewritten whole through
a temp file and an atomic rename, so a crash never leaves half a file, and an undo is just a rewrite
without the last row.

Privacy: a validation error carries a kind and a line number only, never a value. A question is
Ryan's own text about a real chat; it must not be echoed into an exception message.
"""

import csv
import os
import re
import tempfile
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

from gcwrapped.labeling.schema import (
    GOLD_SEPARATOR,
    QUESTION_HEADER,
    SENTIMENTS,
    SKIP_HEADER,
    TAG_HEADER,
    TOPICS,
    QuestionLabel,
    Skip,
    TagLabel,
)

MESSAGE_ID = re.compile(r"[0-9a-f]{64}")  # message_key is a sha256 hex digest
QUESTION_ID = re.compile(r"q[0-9]{3,}")


class LabelFileError(Exception):
    """A label file failed validation. `kind` is a fixed word; `line` is 1-based."""

    def __init__(self, kind: str, line: int) -> None:
        super().__init__(kind, line)
        self.kind = kind
        self.line = line

    def __str__(self) -> str:
        return f"label file error: {self.kind} at line {self.line}"


def _timestamp_ok(value: str) -> bool:
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _rows(path: Path, header: Sequence[str]) -> list[tuple[int, dict[str, str]]]:
    """(line number, row) for each data row. Raises LabelFileError on a bad header or row width."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        first = next(reader, None)
        if first is None or tuple(first) != tuple(header):
            raise LabelFileError("bad_header", 1)
        rows: list[tuple[int, dict[str, str]]] = []
        for cells in reader:
            line = reader.line_num
            if len(cells) != len(header):
                raise LabelFileError("bad_row", line)
            rows.append((line, dict(zip(header, cells, strict=True))))
        return rows


def _write(path: Path, header: Sequence[str], rows: Iterable[Sequence[str]]) -> None:
    """Replace `path` atomically with header + rows, mode 0600 (labels are private)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".labels-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(rows)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _check_tag(label: TagLabel, line: int) -> None:
    if not MESSAGE_ID.fullmatch(label.message_id):
        raise LabelFileError("bad_message_id", line)
    if label.topic not in TOPICS:
        raise LabelFileError("bad_topic", line)
    if label.sentiment not in SENTIMENTS:
        raise LabelFileError("bad_sentiment", line)
    if not _timestamp_ok(label.labeled_at):
        raise LabelFileError("bad_timestamp", line)


def _check_question(label: QuestionLabel, line: int) -> None:
    if not QUESTION_ID.fullmatch(label.question_id):
        raise LabelFileError("bad_question_id", line)
    if not label.question.strip():
        raise LabelFileError("empty_question", line)
    gold = label.gold_message_ids
    if not gold or len(set(gold)) != len(gold):
        raise LabelFileError("bad_gold", line)
    if not all(MESSAGE_ID.fullmatch(g) for g in gold):
        raise LabelFileError("bad_gold", line)
    if label.source_message_id not in label.gold_message_ids:
        raise LabelFileError("source_not_in_gold", line)
    if not _timestamp_ok(label.labeled_at):
        raise LabelFileError("bad_timestamp", line)


def read_tags(path: Path) -> list[TagLabel]:
    """Tag labels in file order. A missing file is an empty list. One label per message."""
    if not path.exists():
        return []
    labels: list[TagLabel] = []
    seen: set[str] = set()
    for line, row in _rows(path, TAG_HEADER):
        label = TagLabel(row["message_id"], row["topic"], row["sentiment"], row["labeled_at"])
        _check_tag(label, line)
        if label.message_id in seen:
            raise LabelFileError("duplicate_message_id", line)
        seen.add(label.message_id)
        labels.append(label)
    return labels


def write_tags(path: Path, labels: Sequence[TagLabel]) -> None:
    seen: set[str] = set()
    for number, label in enumerate(labels, start=2):
        _check_tag(label, number)
        if label.message_id in seen:
            raise LabelFileError("duplicate_message_id", number)
        seen.add(label.message_id)
    _write(path, TAG_HEADER, ([t.message_id, t.topic, t.sentiment, t.labeled_at] for t in labels))


def read_questions(path: Path) -> list[QuestionLabel]:
    """Question labels in file order. A missing file is an empty list. Question IDs are unique."""
    if not path.exists():
        return []
    labels: list[QuestionLabel] = []
    seen: set[str] = set()
    for line, row in _rows(path, QUESTION_HEADER):
        gold = tuple(g for g in row["gold_message_ids"].split(GOLD_SEPARATOR) if g)
        label = QuestionLabel(
            row["question_id"], row["question"], gold, row["source_message_id"], row["labeled_at"]
        )
        _check_question(label, line)
        if label.question_id in seen:
            raise LabelFileError("duplicate_question_id", line)
        seen.add(label.question_id)
        labels.append(label)
    return labels


def write_questions(path: Path, labels: Sequence[QuestionLabel]) -> None:
    seen: set[str] = set()
    for number, label in enumerate(labels, start=2):
        _check_question(label, number)
        if label.question_id in seen:
            raise LabelFileError("duplicate_question_id", number)
        seen.add(label.question_id)
    _write(
        path,
        QUESTION_HEADER,
        (
            [
                q.question_id,
                q.question,
                GOLD_SEPARATOR.join(q.gold_message_ids),
                q.source_message_id,
                q.labeled_at,
            ]
            for q in labels
        ),
    )


def read_skips(path: Path) -> list[Skip]:
    if not path.exists():
        return []
    skips: list[Skip] = []
    for line, row in _rows(path, SKIP_HEADER):
        if not MESSAGE_ID.fullmatch(row["message_id"]):
            raise LabelFileError("bad_message_id", line)
        if not _timestamp_ok(row["skipped_at"]):
            raise LabelFileError("bad_timestamp", line)
        skips.append(Skip(row["message_id"], row["skipped_at"]))
    return skips


def write_skips(path: Path, skips: Sequence[Skip]) -> None:
    _write(path, SKIP_HEADER, ([s.message_id, s.skipped_at] for s in skips))
