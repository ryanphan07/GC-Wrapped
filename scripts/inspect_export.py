"""Describe the structure of a chat export without revealing any content.

Usage: uv run python scripts/inspect_export.py <export_dir>

Prints markdown: directory layout (thread folders renamed thread_001, ...), file-name patterns,
and, per JSON file type, every key path with the JSON types seen, presence rate, array lengths,
string empty/mojibake rates and integer digit counts.

Privacy: this script must NEVER print a string or number *value* from the export. Everything it
prints is a count, a rate, a type name, or a name that matched a strict allowlist regex. Names
that do not match are replaced by a placeholder. tests/test_inspect_export.py plants sentinel
strings in a fixture export and fails if any of them reaches the output.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SAFE_KEY = re.compile(r"^[a-z_]+$")
THREAD_FILE = re.compile(r"^message_\d+\.json$")
# UTF-8 bytes decoded as latin-1: "Ã©" for é, "ð\x9f" for emoji, "â\x80" for punctuation.
MOJIBAKE = re.compile(r"Ã[\x80-\xbf]|ð\x9f|â[\x80-\x9f]")
DIGITS = re.compile(r"\d+")
SAFE_STEM = re.compile(r"^[a-z_]+(_?N)?$")
SAFE_EXT = re.compile(r"^[a-z0-9]{1,5}$")
DYNAMIC_KEY = "<dynamic_key>"


def safe_key(key: str) -> str:
    return key if SAFE_KEY.match(key) else DYNAMIC_KEY


def file_pattern(name: str) -> str:
    """Shape of a file name: digit runs become N; unrecognised names are reduced to A/N shapes."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    shape = DIGITS.sub("N", stem)
    if not SAFE_STEM.match(shape):
        # Mask digits first so a literal "N" in the name can't survive as a letter.
        masked = DIGITS.sub("\0", stem)
        shape = re.sub(r"[^\W\d_]+", "A", masked).replace("\0", "N")
    ext_part = ""
    if dot:
        ext_part = "." + (ext if SAFE_EXT.match(ext) else "<ext>")
    return shape + ext_part


@dataclass
class PathStats:
    count: int = 0
    types: set[str] = field(default_factory=set)
    key_parent: str | None = None
    present: int = 0  # parent objects that contain this key
    objects: int = 0  # times this path held an object (denominator for child presence)
    arr_min: int | None = None
    arr_max: int | None = None
    str_total: int = 0
    str_empty: int = 0
    str_mojibake: int = 0
    int_min_digits: int | None = None
    int_max_digits: int | None = None


def _walk(value: Any, path: str, stats: dict[str, PathStats]) -> None:
    st = stats.setdefault(path, PathStats())
    st.count += 1
    if isinstance(value, dict):
        st.types.add("object")
        st.objects += 1
        keys = {safe_key(k) for k in value}
        for key in keys:
            child = stats.setdefault(f"{path}.{key}", PathStats())
            child.key_parent = path
            child.present += 1
        for k, v in value.items():
            _walk(v, f"{path}.{safe_key(k)}", stats)
    elif isinstance(value, list):
        st.types.add("array")
        n = len(value)
        st.arr_min = n if st.arr_min is None else min(st.arr_min, n)
        st.arr_max = n if st.arr_max is None else max(st.arr_max, n)
        for item in value:
            _walk(item, f"{path}[]", stats)
    elif isinstance(value, bool):
        st.types.add("boolean")
    elif isinstance(value, int):
        st.types.add("integer")
        digits = len(str(abs(value)))
        st.int_min_digits = digits if st.int_min_digits is None else min(st.int_min_digits, digits)
        st.int_max_digits = digits if st.int_max_digits is None else max(st.int_max_digits, digits)
    elif isinstance(value, float):
        st.types.add("float")
    elif isinstance(value, str):
        st.types.add("string")
        st.str_total += 1
        st.str_empty += value == ""
        st.str_mojibake += bool(MOJIBAKE.search(value))
    elif value is None:
        st.types.add("null")
    else:  # pragma: no cover - json.load cannot produce anything else
        st.types.add("other")


def _is_thread_dir(path: Path) -> bool:
    return any(THREAD_FILE.match(p.name) for p in path.iterdir() if p.is_file())


def _entries(path: Path) -> tuple[list[Path], list[Path]]:
    """Subdirectories and files of path, skipping symlinks, sorted by real name."""
    dirs: list[Path] = []
    files: list[Path] = []
    for p in sorted(path.iterdir(), key=lambda p: p.name):
        if p.is_symlink():
            continue
        (dirs if p.is_dir() else files).append(p)
    return dirs, files


def _thread_labels(root: Path) -> dict[Path, str]:
    threads = sorted(
        (p for p in _all_dirs(root) if _is_thread_dir(p)), key=lambda p: p.relative_to(root).parts
    )
    return {p: f"thread_{i:03d}" for i, p in enumerate(threads, start=1)}


def _all_dirs(root: Path) -> list[Path]:
    out = [root]
    for d in _entries(root)[0]:
        out.extend(_all_dirs(d))
    return out


def _layout(path: Path, labels: dict[Path, str], indent: int, lines: list[str]) -> None:
    dirs, files = _entries(path)
    pad = "  " * indent
    patterns = Counter(file_pattern(f.name) for f in files)
    for pattern, n in sorted(patterns.items()):
        lines.append(f"{pad}{pattern} x{n}")
    for d in dirs:
        name = labels.get(d) or (d.name if SAFE_KEY.match(d.name) else "<dir>")
        lines.append(f"{pad}{name}/")
        _layout(d, labels, indent + 1, lines)


def _fmt_rate(n: int, total: int) -> str:
    return f"{n}/{total} ({100 * n / total:.1f}%)" if total else "-"


def _schema_table(stats: dict[str, PathStats]) -> list[str]:
    rows = [
        "| path | types | presence | array len (min-max) | strings empty | strings mojibake "
        "| int digits (min-max) |",
        "|---|---|---|---|---|---|---|",
    ]
    for path in sorted(stats):
        st = stats[path]
        presence = "-"
        if st.key_parent is not None:
            presence = _fmt_rate(st.present, stats[st.key_parent].objects)
        arr = f"{st.arr_min}-{st.arr_max}" if st.arr_min is not None else "-"
        empty = _fmt_rate(st.str_empty, st.str_total) if st.str_total else "-"
        moji = _fmt_rate(st.str_mojibake, st.str_total) if st.str_total else "-"
        ints = f"{st.int_min_digits}-{st.int_max_digits}" if st.int_min_digits is not None else "-"
        types = ", ".join(sorted(st.types))
        rows.append(f"| `{path}` | {types} | {presence} | {arr} | {empty} | {moji} | {ints} |")
    return rows


def describe(root: Path) -> str:
    labels = _thread_labels(root)
    layout: list[str] = []
    _layout(root, labels, 0, layout)

    all_patterns: Counter[str] = Counter()
    json_stats: dict[str, dict[str, PathStats]] = {}
    json_files: Counter[str] = Counter()
    unparseable: Counter[str] = Counter()
    for d in _all_dirs(root):
        for f in _entries(d)[1]:
            pattern = file_pattern(f.name)
            all_patterns[pattern] += 1
            if not pattern.endswith(".json"):
                continue
            json_files[pattern] += 1
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                unparseable[pattern] += 1
                continue
            _walk(data, "$", json_stats.setdefault(pattern, {}))

    out = ["# Export structure", "", "## Directory layout", "", "```", ".", *layout, "```", ""]
    out += [f"Thread folders: {len(labels)}", ""]
    out += ["## File name patterns", "", "| pattern | files |", "|---|---|"]
    out += [f"| `{p}` | {n} |" for p, n in sorted(all_patterns.items())]
    out.append("")
    for pattern in sorted(json_files):
        out += [f"## JSON schema: `{pattern}`", ""]
        out += [f"Files: {json_files[pattern]} (unparseable: {unparseable[pattern]})", ""]
        out += _schema_table(json_stats.get(pattern, {}))
        out.append("")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: inspect_export.py <export_dir>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        print("error: not a directory", file=sys.stderr)
        return 2
    print(describe(root))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
