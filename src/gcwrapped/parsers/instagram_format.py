"""Every assumption about Instagram's DM export layout, in one place.

PROVISIONAL: derived from public descriptions of the "Download your information" JSON export,
not verified against a real export (see docs/formats/instagram-schema.md). The parser and the
synthetic generator both import from here, so reconciling with the real schema is a change to
this file only.
"""

import re
from pathlib import Path
from typing import Any

PLATFORM = "instagram"

# Where thread folders live, relative to the export root.
MESSAGES_DIR = Path("your_instagram_activity/messages/inbox")
# Each thread folder holds message_1.json, message_2.json, ... (newest messages in message_1).
MESSAGE_FILE_RE = re.compile(r"^message_(\d+)\.json$")

# Meta serializes UTF-8 bytes as latin-1 code points.
MOJIBAKE_CODEC = "latin-1"

# File level
KEY_MESSAGES = "messages"
KEY_THREAD_TITLE = "title"
KEY_THREAD_PATH = "thread_path"
KEY_PARTICIPANTS = "participants"
KEY_PARTICIPANT_NAME = "name"

# Message level
KEY_SENDER = "sender_name"
KEY_TIMESTAMP_MS = "timestamp_ms"
KEY_CONTENT = "content"
KEY_PHOTOS = "photos"
KEY_VIDEOS = "videos"
KEY_AUDIO = "audio_files"
KEY_STICKER = "sticker"  # a single object, not a list
KEY_URI = "uri"
KEY_SHARE = "share"
KEY_SHARE_LINK = "link"
KEY_SHARE_TEXT = "share_text"
KEY_REACTIONS = "reactions"
KEY_REACTION_EMOJI = "reaction"
KEY_REACTION_ACTOR = "actor"

_LIST_ATTACHMENT_KEYS = (KEY_PHOTOS, KEY_VIDEOS, KEY_AUDIO)


def find_thread_dirs(root: Path) -> list[Path]:
    """Thread folders under the export root that contain at least one message file, sorted."""
    inbox = root / MESSAGES_DIR
    if not inbox.is_dir():
        return []
    return sorted(d for d in inbox.iterdir() if d.is_dir() and message_files(d))


def message_files(thread_dir: Path) -> list[Path]:
    """A thread's message_N.json files in numeric order (message_2 before message_10)."""
    numbered = [
        (int(m.group(1)), f)
        for f in thread_dir.iterdir()
        if f.is_file() and (m := MESSAGE_FILE_RE.match(f.name))
    ]
    return [f for _, f in sorted(numbered)]


def thread_id(thread_dir: Path) -> str:
    """Thread identity: the folder name, which is stable however the files are chunked."""
    return thread_dir.name


def attachment_uris(raw: dict[str, Any]) -> list[str]:
    """URIs of every attachment on a raw message (photos, videos, audio, sticker), in file order."""
    uris: list[str] = []
    for key in _LIST_ATTACHMENT_KEYS:
        for item in raw.get(key) or []:
            if isinstance(item, dict) and isinstance(item.get(KEY_URI), str):
                uris.append(item[KEY_URI])
    sticker = raw.get(KEY_STICKER)
    if isinstance(sticker, dict) and isinstance(sticker.get(KEY_URI), str):
        uris.append(sticker[KEY_URI])
    return uris


def share_link(raw: dict[str, Any]) -> str | None:
    """The shared-post link, if the message is a share."""
    share = raw.get(KEY_SHARE)
    if isinstance(share, dict) and isinstance(share.get(KEY_SHARE_LINK), str):
        return str(share[KEY_SHARE_LINK])
    return None
