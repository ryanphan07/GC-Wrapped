import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from synthetic import sentinel

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inspect_export.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("inspect_export", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


inspect_export = _load_script()
EXP = sentinel.EXPECTED


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("sentinel")
    sentinel.build(path)
    return path


@pytest.fixture(scope="module")
def report(root: Path) -> Any:
    return inspect_export.collect(root)


def assert_no_leak(output: str) -> None:
    """Fail if the output is empty, lacks the report title, or contains any needle (any case)."""
    assert output, "inspector output is empty"
    assert "Instagram export format report" in output, "report title missing"
    haystack = output.lower()
    needles = {
        *sentinel.SENTINELS,
        *sentinel.THREADS,
        str(sentinel.T0),
        sentinel.MOJIBAKE_TEXT,
    }
    for needle in sorted(needles):
        i = haystack.find(needle.lower())
        if i != -1:
            context = output[max(0, i - 40) : i + len(needle) + 40]
            pytest.fail(f"leak: {needle!r} found near {context!r}")


def test_no_sentinel_in_output(root: Path) -> None:
    assert_no_leak(inspect_export.describe(root))


def test_inbox_and_siblings(report: Any) -> None:
    assert report.inbox_exists
    assert dict(report.siblings) == {"inbox": 1, "message_requests": 1, "photos": 1, "<x>": 2}


def test_folder_shapes_and_digit_lengths(report: Any) -> None:
    assert dict(report.thread_shapes) == EXP["thread_shapes"]
    assert dict(report.digit_lengths) == EXP["digit_lengths"]
    assert dict(report.files_per_thread) == EXP["files_per_thread"]
    assert report.message_files == EXP["message_files"]


def test_message_totals_and_timestamps(report: Any) -> None:
    assert report.messages == EXP["messages"]
    assert dict(report.timestamp_digits) == EXP["timestamp_digit_lengths"]


def test_mojibake_count(report: Any) -> None:
    assert report.mojibake_messages == EXP["mojibake_messages"]
    assert inspect_export.has_mojibake("café".encode().decode("latin-1"))
    assert not inspect_export.has_mojibake("café")


def test_order_detection(report: Any) -> None:
    assert dict(report.order) == EXP["order"]
    assert inspect_export.message_order([3, 2, 1]) == "newest-first"
    assert inspect_export.message_order([1, 2, 3]) == "oldest-first"
    assert inspect_export.message_order([1, 3, 2]) == "mixed"
    assert inspect_export.message_order([5]) == "indeterminate"


def test_duplicate_basenames(report: Any) -> None:
    assert report.duplicate_basenames == EXP["duplicate_basenames"]


def test_key_frequencies(report: Any) -> None:
    keys = report.keys
    assert keys["$"].objects == EXP["message_files"]
    assert keys["$.messages[].share.link"].present == 1
    assert keys["$.messages[].reactions[].actor"].present == 1
    assert keys["$.messages[].timestamp_ms"].present == EXP["messages"]


def test_uri_masking_counts(report: Any) -> None:
    assert dict(inspect_export.masked_uri_counts(report)) == EXP["masked_uris"]


def test_mask_uri_cases() -> None:
    thread = sentinel.THREAD_A
    uri = f"{sentinel.INBOX.as_posix()}/{thread}/photos/SENTINELTEXT_1.jpg"
    assert inspect_export.mask_uri(uri, thread) == (
        "your_instagram_activity/messages/inbox/<thread>/photos/<file>.jpg"
    )
    other = f"{sentinel.INBOX.as_posix()}/{sentinel.LOWER}/photos/x.jpg"
    assert inspect_export.mask_uri(other, thread) == (
        "your_instagram_activity/messages/inbox/<x>/photos/<file>.jpg"
    )
    assert inspect_export.mask_uri("a/lowercase/x.jpg", "lowercase") == "<x>/<thread>/<file>.jpg"
    assert inspect_export.mask_uri("x.jpg", "t") == "<file>.jpg"
    assert inspect_export.mask_uri("x.SENTINELTEXT", "t") == "<file>"
    assert inspect_export.mask_uri("x", "t") == "<file>"
    assert inspect_export.mask_uri("x.JPG", "t") == "<file>.jpg"
    assert inspect_export.mask_uri("x.tar.gz", "t") == "<file>"
    assert inspect_export.mask_uri("https://cdn/x.jpg", "t") == "<x>/<x>/<x>/<file>.jpg"
    assert inspect_export.mask_uri("inbox/", "t") == "inbox/<file>"
    assert inspect_export.mask_uri("inbox/t", "t") == "inbox/<file>"


def test_report_without_inbox(tmp_path: Path) -> None:
    out = inspect_export.describe(tmp_path)
    assert "exists under the root: False" in out


def test_main_rejects_bad_args(tmp_path: Path) -> None:
    assert inspect_export.main(["inspect_export.py", str(tmp_path / "nope")]) == 2
    assert inspect_export.main(["inspect_export.py"]) == 2
