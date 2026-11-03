"""Synthetic Instagram DM exports for tests and demos. No real data is ever involved.

Usage: uv run python tests/synthetic/instagram.py [--out DIR] [--seed N]

Writes two overlapping exports of the same threads (`export_a`, `export_b`) with different file
chunking. Layout and key names come from gcwrapped.parsers.instagram_format, so the generator and
the parser can't drift apart.
"""

import argparse
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gcwrapped.parsers import instagram_format as fmt

Message = dict[str, Any]

_BASE_MS = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)

# Timing model: bursts of rapid back-and-forth separated by multi-hour gaps. Lulls inside a burst
# stay under 60 minutes (the default session gap), so burst boundaries are the session boundaries.
_MINUTE_MS = 60_000
_HOUR_MS = 60 * _MINUTE_MS
_BURST_GAP_MIN_MS = 3 * _HOUR_MS
_BURST_GAP_MAX_MS = 20 * _HOUR_MS
_BURST_LEN_MIN, _BURST_LEN_MAX = 3, 12
_RAPID_MIN_MS, _RAPID_MAX_MS = 5_000, 3 * _MINUTE_MS
_LULL_CHANCE = 0.1
_LULL_MIN_MS, _LULL_MAX_MS = 10 * _MINUTE_MS, 45 * _MINUTE_MS
_SAME_SENDER_CHANCE = 0.4

# (folder name, title, participants, message count)
THREADS: list[tuple[str, str, list[str], int]] = [
    (
        "weekendcrew_a1b2c3",
        "Weekend Crew ☕",
        ["José Núñez", "Zoë Müller", "Sam Lee", "Priya"],
        100,
    ),
    ("sam_d4e5f6", "Sam Lee", ["Ana Ruiz", "Sam Lee"], 40),
]

_TEXTS = [
    "see you at 7",
    "café run? ☕",
    "haha 😂😂",
    "that was so good",
    "où est la réunion?",
    "ok",
    "sending the photos later",
    "naïve question: who's driving?",
    "🔥🔥🔥",
    "lol",
]
_EMOJIS = ["❤️", "😂", "👍", "😮"]
_IDENTICAL_TEXT = "same message 👍"


def to_mojibake(s: str) -> str:
    """What Meta does to text: write the UTF-8 bytes as latin-1 code points."""
    return s.encode("utf-8").decode(fmt.MOJIBAKE_CODEC)


def _uri(thread: str, kind: str, n: int, ext: str) -> str:
    return f"{fmt.MESSAGES_DIR.as_posix()}/{thread}/{kind}/{kind}_{n}{ext}"


def build_conversation(
    rng: random.Random, thread: str, participants: list[str], count: int
) -> list[Message]:
    """A thread's full history, oldest first, as clean (un-mojibaked) message dicts.

    Always contains: photos, shares, reactions, a message with no content key, a message with
    empty content, and two fully identical messages (same ms, sender, content) mid-history.
    """
    messages: list[Message] = []
    ts = _BASE_MS
    burst_left = 0
    prev_sender: str | None = None
    for i in range(count):
        if burst_left == 0:
            ts += rng.randint(_BURST_GAP_MIN_MS, _BURST_GAP_MAX_MS)
            burst_left = rng.randint(_BURST_LEN_MIN, _BURST_LEN_MAX)
        elif rng.random() < _LULL_CHANCE:
            ts += rng.randint(_LULL_MIN_MS, _LULL_MAX_MS)
        else:
            ts += rng.randint(_RAPID_MIN_MS, _RAPID_MAX_MS)
        burst_left -= 1
        # Sticky senders give multi-message turns, not strict alternation.
        if prev_sender is not None and rng.random() < _SAME_SENDER_CHANCE:
            sender = prev_sender
        else:
            sender = rng.choice(participants)
        prev_sender = sender
        msg: Message = {fmt.KEY_SENDER: sender, fmt.KEY_TIMESTAMP_MS: ts}
        kind = rng.choices(["text", "photo", "share", "empty", "nocontent"], [10, 2, 2, 1, 1])[0]
        if i == 3:
            kind = "photo"
        elif i == 4:
            kind = "share"
        elif i == 5:
            kind = "empty"
        elif i == 6:
            kind = "nocontent"
        if kind == "text":
            msg[fmt.KEY_CONTENT] = rng.choice(_TEXTS)
        elif kind == "photo":
            n = rng.randint(1, 3)
            msg[fmt.KEY_PHOTOS] = [
                {fmt.KEY_URI: _uri(thread, "photos", i * 10 + j, ".jpg")} for j in range(n)
            ]
        elif kind == "share":
            msg[fmt.KEY_CONTENT] = "look at this"
            msg[fmt.KEY_SHARE] = {
                fmt.KEY_SHARE_LINK: f"https://www.instagram.com/p/SYN{i:04d}/",
                fmt.KEY_SHARE_TEXT: rng.choice(_TEXTS),
            }
        elif kind == "empty":
            msg[fmt.KEY_CONTENT] = ""
        # "nocontent": no content key at all (e.g. an unsent message)
        if rng.random() < 0.25:
            reactors = rng.sample(participants, k=rng.randint(1, len(participants)))
            msg[fmt.KEY_REACTIONS] = [
                {fmt.KEY_REACTION_EMOJI: rng.choice(_EMOJIS), fmt.KEY_REACTION_ACTOR: r}
                for r in reactors
            ]
        messages.append(msg)

    mid = count // 2
    twin = {
        fmt.KEY_SENDER: participants[0],
        fmt.KEY_TIMESTAMP_MS: messages[mid][fmt.KEY_TIMESTAMP_MS],
        fmt.KEY_CONTENT: _IDENTICAL_TEXT,
    }
    messages[mid] = dict(twin)
    messages[mid + 1] = dict(twin)
    # Keep time order after pinning the twin timestamps.
    messages.sort(key=lambda m: m[fmt.KEY_TIMESTAMP_MS])
    return messages


def _encode_message(msg: Message) -> Message:
    """Apply mojibake to every text field a real export would corrupt."""
    out = dict(msg)
    out[fmt.KEY_SENDER] = to_mojibake(msg[fmt.KEY_SENDER])
    if fmt.KEY_CONTENT in msg:
        out[fmt.KEY_CONTENT] = to_mojibake(msg[fmt.KEY_CONTENT])
    if fmt.KEY_SHARE in msg:
        share = dict(msg[fmt.KEY_SHARE])
        share[fmt.KEY_SHARE_TEXT] = to_mojibake(share[fmt.KEY_SHARE_TEXT])
        out[fmt.KEY_SHARE] = share
    if fmt.KEY_REACTIONS in msg:
        out[fmt.KEY_REACTIONS] = [
            {
                fmt.KEY_REACTION_EMOJI: to_mojibake(r[fmt.KEY_REACTION_EMOJI]),
                fmt.KEY_REACTION_ACTOR: to_mojibake(r[fmt.KEY_REACTION_ACTOR]),
            }
            for r in msg[fmt.KEY_REACTIONS]
        ]
    return out


def write_thread(
    root: Path,
    thread: str,
    title: str,
    participants: list[str],
    messages: list[Message],
    chunk_size: int,
) -> list[Path]:
    """Write messages (oldest first in, newest first on disk) as message_N.json chunks."""
    thread_dir = root / fmt.MESSAGES_DIR / thread
    thread_dir.mkdir(parents=True, exist_ok=True)
    newest_first = [_encode_message(m) for m in reversed(messages)]
    paths: list[Path] = []
    for n, start in enumerate(range(0, len(newest_first), chunk_size), start=1):
        doc = {
            fmt.KEY_PARTICIPANTS: [
                {fmt.KEY_PARTICIPANT_NAME: to_mojibake(p)} for p in participants
            ],
            fmt.KEY_MESSAGES: newest_first[start : start + chunk_size],
            fmt.KEY_THREAD_TITLE: to_mojibake(title),
            fmt.KEY_THREAD_PATH: f"inbox/{thread}",
        }
        path = thread_dir / f"message_{n}.json"
        path.write_text(json.dumps(doc), encoding="utf-8")  # ASCII-escaped, like Meta's output
        paths.append(path)
    return paths


def generate_pair(out_dir: Path, seed: int = 1234) -> tuple[Path, Path]:
    """Write two overlapping exports of the same threads, chunked differently.

    export_a holds the first ~3/4 of each history in chunks of 30; export_b holds everything
    after the first ~1/3 in chunks of 17. The middle (including the identical pair) is in both.
    """
    rng = random.Random(seed)
    root_a, root_b = out_dir / "export_a", out_dir / "export_b"
    for thread, title, participants, count in THREADS:
        history = build_conversation(rng, thread, participants, count)
        write_thread(root_a, thread, title, participants, history[: count * 3 // 4], 30)
        write_thread(root_b, thread, title, participants, history[count // 3 :], 17)
    return root_a, root_b


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic Instagram DM exports")
    ap.add_argument("--out", type=Path, default=Path("tests/fixtures/synthetic"))
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args()
    a, b = generate_pair(args.out, args.seed)
    print(f"wrote {a} and {b}")


if __name__ == "__main__":
    main()
