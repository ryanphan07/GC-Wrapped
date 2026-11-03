import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inspect_export.py"
TIMESTAMP = 1700000000123


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("inspect_export", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


inspect_export = _load_script()


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def export_dir(tmp_path: Path) -> Path:
    root = tmp_path / "export"
    inbox = root / "your_instagram_activity" / "messages" / "inbox"
    thread_a = inbox / "SENTINEL_SECRET_thread_a"
    sender = "SENTINEL_SECRET_sender"
    participants = [{"name": sender}, {"name": "SENTINEL_SECRET_other"}]
    _write(
        thread_a / "message_1.json",
        {
            "participants": participants,
            "title": "SENTINEL_SECRET_title",
            "messages": [
                {"sender_name": sender, "timestamp_ms": TIMESTAMP, "content": "SENTINEL_SECRET_hi"},
                {
                    "sender_name": sender,
                    "timestamp_ms": TIMESTAMP,
                    "content": "SENTINEL_SECRET_cafÃ©",
                },
            ],
        },
    )
    _write(
        thread_a / "message_2.json",
        {
            "participants": participants,
            "title": "SENTINEL_SECRET_title",
            "messages": [
                {"sender_name": sender, "timestamp_ms": TIMESTAMP, "content": ""},
                {
                    "sender_name": sender,
                    "timestamp_ms": TIMESTAMP,
                    "photos": [{"uri": "SENTINEL_SECRET_uri", "SENTINEL_SECRET_key": 5}],
                },
            ],
        },
    )
    (thread_a / "photos").mkdir()
    (thread_a / "photos" / "SENTINEL_SECRET_photo_123.jpg").write_bytes(b"SENTINEL_SECRET_bytes")
    # Lowercase thread name would pass the key regex, so only thread detection hides it.
    _write(inbox / "bob_smith_42" / "message_1.json", {"messages": []})
    _write(
        root / "SENTINEL_SECRET_dir" / "SENTINEL_SECRET_file.json",
        {"SENTINEL_SECRET_dynamic": "SENTINEL_SECRET_value", "count": TIMESTAMP},
    )
    return root


def test_no_value_reaches_output(export_dir: Path) -> None:
    out = inspect_export.describe(export_dir)
    assert "SENTINEL_SECRET" not in out
    for leaked in ("bob_smith", str(TIMESTAMP), "photo_123", "hi\n"):
        assert leaked not in out


def test_threads_are_renamed(export_dir: Path) -> None:
    out = inspect_export.describe(export_dir)
    assert "thread_001/" in out
    assert "thread_002/" in out
    assert "Thread folders: 2" in out
    assert "<dir>/" in out


def test_file_patterns(export_dir: Path) -> None:
    out = inspect_export.describe(export_dir)
    assert "| `message_N.json` | 3 |" in out
    assert "| `A_A_A_N.jpg` | 1 |" in out
    assert "| `A_A_A.json` | 1 |" in out


def test_key_paths_and_rates(export_dir: Path) -> None:
    out = inspect_export.describe(export_dir)
    section = out.split("## JSON schema: `message_N.json`")[1].split("## JSON schema:")[0]
    rows = {
        m.group(1): m.group(2) for m in re.finditer(r"^\| `([^`]+)` \| (.*) \|$", section, re.M)
    }
    # Columns after the path: types | presence | array len | empty | mojibake | int digits
    content = [c.strip() for c in rows["$.messages[].content"].split("|")]
    assert content == ["string", "3/4 (75.0%)", "-", "1/3 (33.3%)", "1/3 (33.3%)", "-"]
    timestamp = [c.strip() for c in rows["$.messages[].timestamp_ms"].split("|")]
    assert timestamp == ["integer", "4/4 (100.0%)", "-", "-", "-", "13-13"]
    messages = [c.strip() for c in rows["$.messages"].split("|")]
    assert messages[0] == "array"
    assert messages[2] == "0-2"
    assert "$.messages[].photos[].<dynamic_key>" in rows
    assert "$.messages[].photos[].uri" in rows
    assert "$.participants[].name" in rows


def test_main_rejects_missing_dir(tmp_path: Path) -> None:
    assert inspect_export.main(["inspect_export.py", str(tmp_path / "nope")]) == 2
    assert inspect_export.main(["inspect_export.py"]) == 2
