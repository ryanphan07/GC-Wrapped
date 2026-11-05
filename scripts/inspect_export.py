"""Content-free format report for an Instagram DM export.

Usage: uv run python scripts/inspect_export.py <export_root>      (or: make inspect EXPORT_DIR=...)

Prints Markdown with aggregates only: counts, histograms, key frequencies and masked URI shapes.
Never a participant name, folder name, message text or full URI, so the report is safe to commit.

Privacy: every string that reaches the output is either a fixed label, a number, or a name that
matched a strict allowlist regex (otherwise it is replaced by <x>). tests/test_inspect_export.py
runs this on a sentinel fixture and fails if any sentinel string appears in the output.
"""

from __future__ import annotations

import itertools
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MESSAGES_DIR = Path("your_instagram_activity/messages/inbox")  # mirrors parsers.instagram_format

SAFE_KEY = re.compile(r"^[a-z_]+$")
SAFE_ENTRY = re.compile(r"^[a-z_]+(\.json)?$")
SAFE_EXT = re.compile(r"^[a-z0-9]{1,5}$")
MESSAGE_FILE = re.compile(r"^message_\d+\.json$")
THREAD_NAME_DIGITS = re.compile(r"^.+_(\d+)$", re.DOTALL)
THREAD_DIGITS = re.compile(r"^\d+$")
# UTF-8 lead byte (0xC2-0xF4) followed by a continuation byte (0x80-0xBF), read as latin-1.
MOJIBAKE = re.compile(r"[Â-ô][\u0080-¿]")
OTHER_KEY = "<x>"

SHAPE_NAME_DIGITS = "<name>_<digits>"
SHAPE_DIGITS = "<digits>"
SHAPE_OTHER = "other"
ORDERS = ("newest-first", "oldest-first", "mixed", "indeterminate")


def safe_key(key: str) -> str:
    return key if SAFE_KEY.match(key) else OTHER_KEY


def mask_uri(uri: str, thread_folder: str) -> str:
    """Reduce an attachment URI to a shape that carries no names.

    WRITE IT YOURSELF. Rules (from the session brief):
      - a segment equal to `thread_folder` becomes `<thread>` (check this BEFORE the next rule:
        a folder name made only of [a-z_] would otherwise survive unmasked);
      - the last segment becomes `<file>.<ext>`, keeping the extension only if it is safe;
      - any other segment not matching [a-z_]+ becomes `<x>`.
    Think about: segments that are another thread's folder name, an empty extension, no slashes.
    """
    raise NotImplementedError


def thread_shape(name: str) -> tuple[str, int | None]:
    """(shape, digit-run length) of a thread folder name. Never returns the name itself."""
    if THREAD_DIGITS.match(name):
        return SHAPE_DIGITS, len(name)
    m = THREAD_NAME_DIGITS.match(name)
    if m:
        return SHAPE_NAME_DIGITS, len(m.group(1))
    return SHAPE_OTHER, None


def has_mojibake(text: str) -> bool:
    return bool(MOJIBAKE.search(text))


def message_order(timestamps: list[int]) -> str:
    """newest-first / oldest-first / mixed; indeterminate if fewer than two distinct values."""
    if len(set(timestamps)) < 2:
        return "indeterminate"
    pairs = list(itertools.pairwise(timestamps))
    if all(a >= b for a, b in pairs):
        return "newest-first"
    if all(a <= b for a, b in pairs):
        return "oldest-first"
    return "mixed"


@dataclass
class KeyStats:
    types: set[str] = field(default_factory=set)
    present: int = 0  # parent objects containing this key
    objects: int = 0  # times this path held an object (denominator for its children)
    parent: str | None = None
    arr_min: int | None = None
    arr_max: int | None = None


@dataclass
class Report:
    inbox_exists: bool = False
    siblings: Counter[str] = field(default_factory=Counter)
    thread_shapes: Counter[str] = field(default_factory=Counter)
    digit_lengths: Counter[int] = field(default_factory=Counter)
    files_per_thread: Counter[int] = field(default_factory=Counter)
    message_files: int = 0
    unparseable_files: int = 0
    messages: int = 0
    keys: dict[str, KeyStats] = field(default_factory=dict)
    timestamp_digits: Counter[int] = field(default_factory=Counter)
    order: Counter[str] = field(default_factory=Counter)
    text_messages: int = 0
    mojibake_messages: int = 0
    # Raw URIs are held in memory only to be masked at render time; they are never printed.
    uris: Counter[tuple[str, str, str]] = field(default_factory=Counter)  # (key, thread, uri)
    duplicate_basenames: int = 0


def _walk(value: Any, path: str, keys: dict[str, KeyStats]) -> None:
    st = keys.setdefault(path, KeyStats())
    if isinstance(value, dict):
        st.types.add("object")
        st.objects += 1
        for k in {safe_key(k) for k in value}:
            child = keys.setdefault(f"{path}.{k}", KeyStats())
            child.parent = path
            child.present += 1
        for k, v in value.items():
            _walk(v, f"{path}.{safe_key(k)}", keys)
    elif isinstance(value, list):
        st.types.add("array")
        n = len(value)
        st.arr_min = n if st.arr_min is None else min(st.arr_min, n)
        st.arr_max = n if st.arr_max is None else max(st.arr_max, n)
        for item in value:
            _walk(item, f"{path}[]", keys)
    elif value is None:
        st.types.add("null")
    else:
        st.types.add(type(value).__name__)


def _attachment_uris(msg: dict[str, Any]) -> list[tuple[str, str]]:
    """(attachment key, uri) for every list-of-objects or object value that carries a `uri`."""
    found: list[tuple[str, str]] = []
    for key, value in msg.items():
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("uri"), str):
                found.append((safe_key(key), item["uri"]))
    return found


def _collect_file(rep: Report, thread: str, data: Any) -> None:
    _walk(data, "$", rep.keys)
    if not isinstance(data, dict):
        return
    messages = data.get("messages")
    if not isinstance(messages, list):
        return
    stamps: list[int] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        rep.messages += 1
        ts = msg.get("timestamp_ms")
        if isinstance(ts, int) and not isinstance(ts, bool):
            rep.timestamp_digits[len(str(abs(ts)))] += 1
            stamps.append(ts)
        content = msg.get("content")
        if isinstance(content, str):
            rep.text_messages += 1
            rep.mojibake_messages += has_mojibake(content)
        for key, uri in _attachment_uris(msg):
            rep.uris[(key, thread, uri)] += 1
    rep.order[message_order(stamps)] += 1


def collect(root: Path) -> Report:
    rep = Report()
    inbox = root / MESSAGES_DIR
    rep.inbox_exists = inbox.is_dir()
    if not rep.inbox_exists:
        return rep
    for p in inbox.parent.iterdir():
        rep.siblings[p.name if SAFE_ENTRY.match(p.name) else OTHER_KEY] += 1
    for thread in sorted(p for p in inbox.iterdir() if p.is_dir() and not p.is_symlink()):
        shape, digits = thread_shape(thread.name)
        rep.thread_shapes[shape] += 1
        if digits is not None:
            rep.digit_lengths[digits] += 1
        files = sorted(f for f in thread.iterdir() if f.is_file() and MESSAGE_FILE.match(f.name))
        rep.files_per_thread[len(files)] += 1
        rep.message_files += len(files)
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                rep.unparseable_files += 1
                continue
            _collect_file(rep, thread.name, data)
    rep.duplicate_basenames = _duplicate_basenames(rep.uris)
    return rep


def _duplicate_basenames(uris: Counter[tuple[str, str, str]]) -> int:
    """Distinct URIs in a thread beyond the first that share a basename with another."""
    by_thread: dict[str, dict[str, set[str]]] = {}
    for _key, thread, uri in uris:
        by_thread.setdefault(thread, {}).setdefault(uri.rsplit("/", 1)[-1], set()).add(uri)
    return sum(len(v) - 1 for names in by_thread.values() for v in names.values() if len(v) > 1)


def masked_uri_counts(rep: Report) -> Counter[tuple[str, str]]:
    out: Counter[tuple[str, str]] = Counter()
    for (key, thread, uri), n in rep.uris.items():
        out[(key, mask_uri(uri, thread))] += n
    return out


def _histogram(title: str, counter: Counter[Any]) -> list[str]:
    rows = [f"### {title}", "", "| value | count |", "|---|---|"]
    rows += [f"| {k} | {counter[k]} |" for k in sorted(counter)]
    return [*rows, ""]


def _key_table(keys: dict[str, KeyStats]) -> list[str]:
    rows = ["| path | types | present | array len |", "|---|---|---|---|"]
    for path in sorted(keys):
        st = keys[path]
        present = f"{st.present} of {keys[st.parent].objects}" if st.parent else "-"
        arr = f"{st.arr_min}-{st.arr_max}" if st.arr_min is not None else "-"
        rows.append(f"| `{path}` | {', '.join(sorted(st.types))} | {present} | {arr} |")
    return [*rows, ""]


def render(rep: Report) -> str:
    out = ["# Instagram export format report", "", "Aggregates only. No names, text or URIs.", ""]
    out += [f"- `{MESSAGES_DIR.as_posix()}` exists under the root: {rep.inbox_exists}", ""]
    if not rep.inbox_exists:
        return "\n".join(out)
    out += ["## Entries next to inbox", "", "| entry | count |", "|---|---|"]
    out += [f"| `{k}` | {v} |" for k, v in sorted(rep.siblings.items())] + [""]
    out += ["## Threads", "", f"Thread folders: {sum(rep.thread_shapes.values())}", ""]
    out += _histogram("Folder shape", rep.thread_shapes)
    out += _histogram("Digit-run length (<name>_<digits> and <digits>)", rep.digit_lengths)
    out += _histogram("Message files per thread", rep.files_per_thread)
    out += [f"Message files: {rep.message_files} (unparseable: {rep.unparseable_files})", ""]
    out += ["## Messages", "", f"Total messages: {rep.messages}", ""]
    out += _histogram("Timestamp digit length", rep.timestamp_digits)
    out += _histogram("Message order within files", rep.order)
    out += [
        f"Text messages matching the latin-1 mojibake pattern: "
        f"{rep.mojibake_messages} of {rep.text_messages}",
        "",
    ]
    out += ["## Key frequencies", "", *_key_table(rep.keys)]
    out += ["## Attachments", "", "| attachment key | masked URI shape | count |", "|---|---|---|"]
    for (key, shape), n in sorted(masked_uri_counts(rep).items()):
        out.append(f"| `{key}` | `{shape}` | {n} |")
    out += ["", f"Duplicate attachment basenames within a thread: {rep.duplicate_basenames}", ""]
    return "\n".join(out)


def describe(root: Path) -> str:
    return render(collect(root))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: inspect_export.py <export_root>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        print("error: not a directory", file=sys.stderr)
        return 2
    print(describe(root))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
