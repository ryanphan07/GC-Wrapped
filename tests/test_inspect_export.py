import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from synthetic import sentinel

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inspect_export.py"

# The two write-it-yourself pieces raise NotImplementedError until Ryan implements them.
# Tests that need them are xfail (non-strict): they turn green on their own once implemented.
needs_ryan = pytest.mark.xfail(raises=NotImplementedError, reason="write-it-yourself: not done")


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
    """WRITE IT YOURSELF: the core assertions of the leak test.

    Should fail if any of sentinel.SENTINELS appears in `output` (consider case-insensitivity),
    and also if a raw folder name, a full URI or the mojibake text appears.
    """
    raise NotImplementedError


@needs_ryan
def test_no_sentinel_in_output(root: Path) -> None:
    assert_no_leak(inspect_export.describe(root))


def test_inbox_and_siblings(report: Any) -> None:
    assert report.inbox_exists
    assert dict(report.siblings) == {"inbox": 1, "message_requests": 1, "photos": 1, "<x>": 1}


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


@needs_ryan
def test_uri_masking_counts(report: Any) -> None:
    assert dict(inspect_export.masked_uri_counts(report)) == EXP["masked_uris"]


@needs_ryan
def test_mask_uri_cases() -> None:
    thread = sentinel.THREAD_A
    uri = f"{sentinel.INBOX.as_posix()}/{thread}/photos/SENTINELTEXT_1.jpg"
    assert inspect_export.mask_uri(uri, thread) == (
        "your_instagram_activity/messages/inbox/<thread>/photos/<file>.jpg"
    )
    assert inspect_export.mask_uri("a/lowercase/x.jpg", "lowercase") == "a/<thread>/<file>.jpg"


def test_report_without_inbox(tmp_path: Path) -> None:
    out = inspect_export.describe(tmp_path)
    assert "exists under the root: False" in out


def test_main_rejects_bad_args(tmp_path: Path) -> None:
    assert inspect_export.main(["inspect_export.py", str(tmp_path / "nope")]) == 2
    assert inspect_export.main(["inspect_export.py"]) == 2
