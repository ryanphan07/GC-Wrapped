"""Sentinel export for the inspector's leak test. Synthetic; no real data is ever involved.

Usage: uv run python tests/synthetic/sentinel.py [--out DIR]

Every name, content field and folder name carries SENTINELNAME or SENTINELTEXT, so any appearance
of either string in the inspector's output is a leak. The shapes (bare-digit folders, 16-digit
suffixes, mojibake, duplicate basenames, mixed message order) have known counts, in EXPECTED.
"""

import argparse
import json
from pathlib import Path
from typing import Any

SENTINELS = ("SENTINELNAME", "SENTINELTEXT")
INBOX = Path("your_instagram_activity/messages/inbox")

MOJIBAKE_TEXT = "SENTINELTEXT café \U0001f600".encode().decode("latin-1")
T0 = 1_700_000_000_000  # 13 digits

THREAD_A = "SENTINELNAME_1234567890123456"  # <name>_<16 digits>
THREAD_B = "SENTINELNAME_alpha_12345678901234567"  # <name>_<17 digits>
THREAD_C = "123456789012345"  # bare, 15 digits
THREAD_D = "12345678901234"  # bare, 14 digits
THREAD_E = "SENTINELNAME-no-suffix"  # other
THREADS = (THREAD_A, THREAD_B, THREAD_C, THREAD_D, THREAD_E)

# Known values the tests assert against.
EXPECTED: dict[str, Any] = {
    "thread_shapes": {"<name>_<digits>": 2, "<digits>": 2, "other": 1},
    "digit_lengths": {14: 1, 15: 1, 16: 1, 17: 1},
    "files_per_thread": {1: 3, 2: 1, 3: 1},
    "message_files": 8,
    "messages": 17,
    "mojibake_messages": 2,
    "timestamp_digit_lengths": {13: 17},
    "order": {"newest-first": 2, "oldest-first": 2, "mixed": 1, "indeterminate": 3},
    "attachments_per_key": {"photos": 4, "videos": 1, "audio_files": 1},
    "duplicate_basenames": 1,
    "masked_uris": {
        ("photos", "your_instagram_activity/messages/inbox/<thread>/photos/<file>.jpg"): 4,
        ("videos", "your_instagram_activity/messages/inbox/<thread>/videos/<file>.jpg"): 1,
        ("audio_files", "your_instagram_activity/messages/inbox/<thread>/<x>/<file>.mp4"): 1,
    },
}


def _msg(ts: int, **extra: Any) -> dict[str, Any]:
    return {"sender_name": "SENTINELNAME sender", "timestamp_ms": ts, **extra}


def _uri(thread: str, kind: str, name: str) -> str:
    return f"{INBOX.as_posix()}/{thread}/{kind}/{name}"


def _write(path: Path, messages: list[dict[str, Any]], thread: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "participants": [{"name": "SENTINELNAME one"}, {"name": "SENTINELNAME two"}],
        "title": "SENTINELNAME title",
        "thread_path": f"inbox/{thread}",
        "messages": messages,
    }
    path.write_text(json.dumps(body), encoding="utf-8")


def build(root: Path) -> None:
    inbox = root / INBOX
    # Thread A: three files. newest-first, oldest-first, mixed. Mojibake, share, reactions,
    # attachments with a duplicate basename (same name in photos/ and videos/ of one thread).
    a = inbox / THREAD_A
    _write(
        a / "message_1.json",
        [
            _msg(T0 + 30, content="SENTINELTEXT newest"),
            _msg(
                T0 + 20,
                content=MOJIBAKE_TEXT,
                reactions=[{"reaction": "x", "actor": "SENTINELNAME r"}],
            ),
            _msg(
                T0 + 10,
                share={"link": "https://SENTINELTEXT.example/p/1", "share_text": "SENTINELTEXT"},
            ),
        ],
        THREAD_A,
    )
    _write(
        a / "message_2.json",
        [
            _msg(T0 + 1, content="SENTINELTEXT old"),
            _msg(T0 + 2, photos=[{"uri": _uri(THREAD_A, "photos", "SENTINELTEXT_1.jpg")}]),
            _msg(T0 + 3, videos=[{"uri": _uri(THREAD_A, "videos", "SENTINELTEXT_1.jpg")}]),
        ],
        THREAD_A,
    )
    _write(
        a / "message_3.json",
        [
            _msg(T0 + 5, content=MOJIBAKE_TEXT),
            _msg(T0 + 9, content="SENTINELTEXT mixed"),
            _msg(T0 + 7, photos=[{"uri": _uri(THREAD_A, "photos", "SENTINELTEXT_2.jpg")}]),
        ],
        THREAD_A,
    )
    # Thread B: two files, one newest-first, one oldest-first; audio and a SENTINEL path segment.
    b = inbox / THREAD_B
    _write(
        b / "message_1.json",
        [
            _msg(T0 + 4, content="SENTINELTEXT"),
            _msg(T0 + 3, audio_files=[{"uri": _uri(THREAD_B, "SENTINELNAME_audio", "a.mp4")}]),
        ],
        THREAD_B,
    )
    _write(
        b / "message_2.json",
        [
            _msg(T0 + 1, photos=[{"uri": _uri(THREAD_B, "photos", "SENTINELTEXT_3.jpg")}]),
            _msg(T0 + 2, content="SENTINELTEXT"),
        ],
        THREAD_B,
    )
    # Threads C, D, E: one file each, single message or equal timestamps (order indeterminate).
    _write(inbox / THREAD_C / "message_1.json", [_msg(T0, content="SENTINELTEXT")], THREAD_C)
    _write(
        inbox / THREAD_D / "message_1.json",
        [_msg(T0, content="SENTINELTEXT"), _msg(T0, content="SENTINELTEXT")],
        THREAD_D,
    )
    _write(
        inbox / THREAD_E / "message_1.json",
        [_msg(T0, photos=[{"uri": _uri(THREAD_E, "photos", "SENTINELTEXT_4.jpg")}])],
        THREAD_E,
    )
    # Siblings of inbox: two safe names, one that must print as <x>.
    (root / INBOX.parent / "message_requests").mkdir(parents=True, exist_ok=True)
    (root / INBOX.parent / "photos").mkdir(parents=True, exist_ok=True)
    (root / INBOX.parent / "SENTINELNAME.json").write_text("{}", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("tests/fixtures/sentinel"))
    args = parser.parse_args()
    build(args.out)
    print(f"wrote sentinel export to {args.out}")


if __name__ == "__main__":
    main()
