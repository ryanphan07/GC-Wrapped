"""Synthetic Instagram DM exports for tests and demos. No real data is ever involved.

Usage: uv run python tests/synthetic/instagram.py [--out DIR] [--seed N]

The output directory is emptied first, so nothing from an older generator survives.

Writes two overlapping exports of the same threads (`export_a`, `export_b`) in the real format
(docs/formats/instagram-schema.md): <name>_<digits> and bare <digits> thread folders, newest-first
message files chunked at fmt.MAX_MESSAGES_PER_FILE, mojibake text, extensionless photos, external
video URLs, and every real key the parser deliberately ignores. Export b uses different chunking
and a different export root, renames one thread folder (same digits) and gives external URLs a
different query string. Layout and key names come from gcwrapped.parsers.instagram_format.
"""

import argparse
import json
import random
import re
import shutil
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

# (folder name, title, participants, message count). Digit runs are 15-17 long, as in the real
# export; the third folder is bare digits.
THREADS: list[tuple[str, str, list[str], int]] = [
    (
        "weekendcrew_1784563920183746",
        "Weekend Crew ☕",
        ["José Núñez", "Zoë Müller", "Sam Lee", "Priya", "Nguyễn Thị Hương"],
        100,
    ),
    ("sam_293847561029384", "Sam Lee", ["Ana Ruiz", "Sam Lee"], 40),
    ("48271936502817364", "Priya Raman", ["Ana Ruiz", "Priya Raman"], 30),
]

# The numeric thread IDs of THREADS: what GCW_THREAD_ALLOWLIST must list for synthetic data.
# .env.example and CI list the same IDs (tests/test_consent.py keeps them in sync).
SYNTHETIC_THREAD_IDS: list[str] = [m.group(1) for t in THREADS if (m := re.search(r"(\d+)$", t[0]))]

# Folder renames applied in export b: the name part changes, the digits (the thread_id) do not.
RENAMED_IN_B = {THREADS[1][0]: "samantha_293847561029384"}

# Placeholders in the in-memory history, filled in when an export is written, because a real
# export embeds the thread folder in every URI and CDN URLs carry per-export query strings.
_FOLDER = "@@FOLDER@@"
_QUERY = "@@QUERY@@"
_QUERY_BY_EXPORT = {"a": "?oh=aaaa1111&oe=65A1B2C3", "b": "?oh=bbbb2222&oe=66D4E5F6"}

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
    "xin chào bạn ơi, hôm nay Hương đến muộn nhé",
    "Nguyễn nói tối nay ăn phở ở Đà Nẵng 🍜",
]
_EMOJIS = ["❤️", "😂", "👍", "😮"]
_IDENTICAL_TEXT = "same message 👍"


def to_mojibake(s: str) -> str:
    """What Meta does to text: write the UTF-8 bytes as latin-1 code points."""
    return s.encode("utf-8").decode(fmt.MOJIBAKE_CODEC)


def _export_uri(kind: str, name: str) -> str:
    return f"{fmt.MESSAGES_DIR.as_posix()}/{_FOLDER}/{kind}/{name}"


def _attachment(kind: str, name: str) -> Message:
    return {fmt.KEY_URI: _export_uri(kind, name)}


def _creation_timestamp(
    rng: random.Random, ts: int, *, always: bool = False, never: bool = False
) -> Message:
    """Real attachments sometimes lack creation_timestamp."""
    if never or (not always and rng.random() > 0.9):
        return {}
    return {fmt.KEY_CREATION_TIMESTAMP: ts // 1000 - rng.randint(0, 3600)}


def _reaction(rng: random.Random, actor: str, ts: int) -> Message:
    """Real reactions carry a timestamp only about one time in ten."""
    reaction: Message = {fmt.KEY_REACTION_EMOJI: rng.choice(_EMOJIS), fmt.KEY_REACTION_ACTOR: actor}
    if rng.random() < 0.1:
        reaction[fmt.KEY_REACTION_TIMESTAMP] = ts + rng.randint(1_000, 600_000)
    return reaction


def _external_video_uri(i: int) -> str:
    return f"https://scontent.cdninstagram.com/v/t66.30100-16/syn{i:04d}_n.mp4{_QUERY}"


def build_conversation(rng: random.Random, participants: list[str], count: int) -> list[Message]:
    """A thread's full history, oldest first, as clean (un-mojibaked) message dicts.

    Always contains: photos (some extensionless, some without creation_timestamp), shares (one
    without a link, one profile share), an export video, an external video, audio, a call,
    reactions (some with timestamps), a message with no content key, a message with empty
    content, and two fully identical messages (same ms, sender, content) mid-history.
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
        kinds = ["text", "photo", "share", "empty", "nocontent", "video", "audio", "call"]
        kind = rng.choices(kinds, [10, 2, 2, 1, 1, 1, 1, 1])[0]
        forced = {3: "photo", 4: "share", 5: "empty", 6: "nocontent", 7: "video"}
        forced |= {8: "extvideo", 9: "audio", 10: "call", 11: "sharenolink", 12: "profileshare"}
        forced[count // 2 + 3] = "extvideo"  # inside both exports, so their keys must match
        kind = forced.get(i, kind)
        # Real exports carry this flag on all but a handful of messages.
        msg[fmt.KEY_IS_GEOBLOCKED] = False
        if rng.random() < 0.95:
            msg[fmt.KEY_IS_UNSENT_IMAGE] = False
        if kind == "text":
            msg[fmt.KEY_CONTENT] = rng.choice(_TEXTS)
        elif kind == "photo":
            n = 2 if i == 3 else rng.randint(1, 3)
            msg[fmt.KEY_PHOTOS] = [
                _attachment("photos", f"syn{i:04d}_{j}{rng.choice(['.jpg', '.png', '.webp', ''])}")
                | _creation_timestamp(
                    rng, ts, always=(i == 3 and j == 0), never=(i == 3 and j == 1)
                )
                for j in range(n)
            ]
        elif kind == "video":
            msg[fmt.KEY_VIDEOS] = [_attachment("videos", f"syn{i:04d}_0.mp4")]
        elif kind == "extvideo":
            msg[fmt.KEY_VIDEOS] = [{fmt.KEY_URI: _external_video_uri(i)}]
        elif kind == "audio":
            msg[fmt.KEY_AUDIO] = [
                _attachment("audio", f"syn{i:04d}_0.mp4") | _creation_timestamp(rng, ts)
            ]
        elif kind == "call":
            msg[fmt.KEY_CONTENT] = "call ended"
            msg[fmt.KEY_CALL_DURATION] = rng.randint(5, 3600)
        elif kind in ("share", "sharenolink", "profileshare"):
            msg[fmt.KEY_CONTENT] = "look at this"
            share: dict[str, Any] = {fmt.KEY_SHARE_TEXT: rng.choice(_TEXTS)}
            if kind != "sharenolink":
                share[fmt.KEY_SHARE_LINK] = f"https://www.instagram.com/p/SYN{i:04d}/"
            if kind == "profileshare":
                share[fmt.KEY_SHARE_PROFILE_NAME] = "Zoë Núñez"
                share[fmt.KEY_SHARE_PROFILE_USERNAME] = "zoe_nunez_syn"
            elif rng.random() < 0.9:
                share[fmt.KEY_SHARE_OWNER] = rng.choice(participants)
            msg[fmt.KEY_SHARE] = share
        elif kind == "empty":
            msg[fmt.KEY_CONTENT] = ""
        # "nocontent": no content key at all (e.g. an unsent message)
        if rng.random() < 0.25:
            reactors = rng.sample(participants, k=rng.randint(1, len(participants)))
            msg[fmt.KEY_REACTIONS] = [_reaction(rng, r, ts) for r in reactors]
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


def _mojibake_values(node: Any) -> Any:
    """Apply mojibake to every string value, as Meta does to every string in an export."""
    if isinstance(node, str):
        return to_mojibake(node)
    if isinstance(node, list):
        return [_mojibake_values(x) for x in node]
    if isinstance(node, dict):
        return {k: _mojibake_values(v) for k, v in node.items()}
    return node


def _materialize(msg: Message, folder: str, query: str) -> Message:
    """Fill the URI placeholders for one export, then mojibake the text."""
    filled = json.loads(json.dumps(msg).replace(_FOLDER, folder).replace(_QUERY, query))
    encoded: Message = _mojibake_values(filled)
    return encoded


def write_thread(
    root: Path,
    folder: str,
    title: str,
    participants: list[str],
    messages: list[Message],
    chunk_size: int,
    query: str,
) -> list[Path]:
    """Write messages (oldest first in, newest first on disk) as message_N.json chunks."""
    thread_dir = root / fmt.MESSAGES_DIR / folder
    thread_dir.mkdir(parents=True, exist_ok=True)
    newest_first = [_materialize(m, folder, query) for m in reversed(messages)]
    group = len(participants) > 2
    paths: list[Path] = []
    for n, start in enumerate(range(0, len(newest_first), chunk_size), start=1):
        doc: dict[str, Any] = {
            fmt.KEY_PARTICIPANTS: [
                {fmt.KEY_PARTICIPANT_NAME: to_mojibake(p)} for p in participants
            ],
            fmt.KEY_MESSAGES: newest_first[start : start + chunk_size],
            fmt.KEY_THREAD_TITLE: to_mojibake(title),
            fmt.KEY_IS_STILL_PARTICIPANT: True,
            fmt.KEY_THREAD_PATH: f"inbox/{folder}",
            fmt.KEY_MAGIC_WORDS: [],
        }
        if group:  # group chats carry an image and an invite link
            doc[fmt.KEY_IMAGE] = {
                fmt.KEY_URI: _export_uri("photos", "group_image.jpg").replace(_FOLDER, folder),
                fmt.KEY_CREATION_TIMESTAMP: 1_700_000_000,
            }
            doc[fmt.KEY_JOINABLE_MODE] = {"link": "https://ig.me/j/SYNTHETICINVITE/", "mode": 1}
        path = thread_dir / f"message_{n}.json"
        path.write_text(json.dumps(doc), encoding="utf-8")  # ASCII-escaped, like Meta's output
        paths.append(path)
    return paths


def generate_pair(
    out_dir: Path, seed: int = 1234, max_per_file: int = fmt.MAX_MESSAGES_PER_FILE
) -> tuple[Path, Path]:
    """Write two overlapping exports of the same threads, chunked differently.

    export_a holds the first ~3/4 of each history, chunked at `max_per_file`; export_b holds
    everything after the first ~1/3, chunked at ~17/30 of that, with one thread folder renamed and
    external URLs carrying a different query string. The middle (including each identical pair) is
    in both. Pass a small `max_per_file` to get multi-file threads.
    """
    rng = random.Random(seed)
    root_a, root_b = out_dir / "export_a", out_dir / "export_b"
    chunk_a, chunk_b = max_per_file, max(1, max_per_file * 17 // 30)
    for folder, title, participants, count in THREADS:
        history = build_conversation(rng, participants, count)
        query_a, query_b = _QUERY_BY_EXPORT["a"], _QUERY_BY_EXPORT["b"]
        write_thread(
            root_a, folder, title, participants, history[: count * 3 // 4], chunk_a, query_a
        )
        folder_b = RENAMED_IN_B.get(folder, folder)
        write_thread(root_b, folder_b, title, participants, history[count // 3 :], chunk_b, query_b)
    return root_a, root_b


def clear_output_dir(out: Path, cwd: Path | None = None) -> None:
    """Remove everything inside `out`, so exports from older generators cannot survive.

    Empties the directory rather than deleting it: it may be a bind-mounted landing folder. It
    refuses a directory that looks like more than a scratch folder (a filesystem root, the home
    directory, a git checkout, or the working directory or one of its parents), since this is
    recursive deletion of a path given on the command line.
    """
    if not out.exists():
        return
    target = out.resolve()
    here = (cwd or Path.cwd()).resolve()
    if (
        target == Path(target.anchor)
        or target == Path.home().resolve()
        or (target / ".git").exists()
        or target == here
        or target in here.parents
    ):
        raise SystemExit(
            "refusing to clear this --out directory; choose a dedicated scratch folder"
        )
    for child in target.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic Instagram DM exports")
    ap.add_argument("--out", type=Path, default=Path("tests/fixtures/synthetic"))
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args()
    clear_output_dir(args.out)
    a, b = generate_pair(args.out, args.seed)
    print(f"wrote {a} and {b}")


if __name__ == "__main__":
    main()
