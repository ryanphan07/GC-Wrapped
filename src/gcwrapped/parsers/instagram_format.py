"""Every assumption about Instagram's DM export layout, in one place.

Matches the real-format report in docs/formats/instagram-schema.md (see also
docs/formats/reconciliation.md). The parser and the synthetic generator both import from here,
so a format change is a change to this file only.
"""

import posixpath
import re
from pathlib import Path
from typing import Any

PLATFORM = "instagram"

# Where thread folders live, relative to the export root. Only inbox/ is ingested.
MESSAGES_DIR = Path("your_instagram_activity/messages/inbox")
# Each thread folder holds message_1.json, message_2.json, ... (newest messages first).
MESSAGE_FILE_RE = re.compile(r"^message_(\d+)\.json$")
# Meta caps a message file at this many messages and starts the next message_N.json.
MAX_MESSAGES_PER_FILE = 10000

# Meta serializes UTF-8 bytes as latin-1 code points.
MOJIBAKE_CODEC = "latin-1"

# Attachment URIs inside the export start with this; anything else is external (e.g. a CDN URL).
EXPORT_URI_PREFIX = "your_instagram_activity/"

# File level
KEY_MESSAGES = "messages"
KEY_THREAD_TITLE = "title"
KEY_PARTICIPANTS = "participants"
KEY_PARTICIPANT_NAME = "name"

# Message level
KEY_SENDER = "sender_name"
KEY_TIMESTAMP_MS = "timestamp_ms"
KEY_CONTENT = "content"
KEY_PHOTOS = "photos"
KEY_VIDEOS = "videos"
KEY_AUDIO = "audio_files"
KEY_URI = "uri"
KEY_SHARE = "share"
KEY_SHARE_LINK = "link"
KEY_REACTIONS = "reactions"
KEY_REACTION_EMOJI = "reaction"
KEY_REACTION_ACTOR = "actor"

# Present in real exports but deliberately not mapped (docs/formats/reconciliation.md). The parser
# never reads them; the synthetic generator emits them so tests prove they are tolerated.
KEY_THREAD_PATH = "thread_path"
KEY_IMAGE = "image"
KEY_JOINABLE_MODE = "joinable_mode"
KEY_MAGIC_WORDS = "magic_words"
KEY_IS_STILL_PARTICIPANT = "is_still_participant"
KEY_CALL_DURATION = "call_duration"
KEY_IS_GEOBLOCKED = "is_geoblocked_for_viewer"
KEY_IS_UNSENT_IMAGE = "is_unsent_image_by_messenger_kid_parent"
KEY_SHARE_TEXT = "share_text"
KEY_SHARE_OWNER = "original_content_owner"
KEY_SHARE_PROFILE_NAME = "profile_share_name"
KEY_SHARE_PROFILE_USERNAME = "profile_share_username"
KEY_CREATION_TIMESTAMP = "creation_timestamp"
KEY_REACTION_TIMESTAMP = "timestamp"

ATTACHMENT_KEYS = (KEY_PHOTOS, KEY_VIDEOS, KEY_AUDIO)

# <name>_<digits> or bare <digits>; the capture is the thread id.
_THREAD_FOLDER_RE = re.compile(r"(?:^|_)(\d+)$")


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


def thread_folder_shape(thread_dir: Path) -> str:
    """Shape of a thread folder name: "<digits>", "<name>_<digits>" or "other". Never the name."""
    match = _THREAD_FOLDER_RE.search(thread_dir.name)
    if match is None:
        return "other"
    return "<digits>" if match.group(1) == thread_dir.name else "<name>_<digits>"


def thread_id(thread_dir: Path) -> str:
    """Thread identity: the trailing numeric ID of the folder name (<name>_<digits> or <digits>).

    The name part embeds a participant's display name and changes on rename; the digits do not.
    Raises ValueError for any other shape. The message never includes the folder name.
    """
    match = _THREAD_FOLDER_RE.search(thread_dir.name)
    if match is None:
        raise ValueError("thread folder name does not match <name>_<digits> or <digits>")
    return match.group(1)


def attachment_uris(raw: dict[str, Any]) -> list[str]:
    """URIs of every attachment on a raw message (photos, videos, audio), in file order."""
    uris: list[str] = []
    for key in ATTACHMENT_KEYS:
        for item in raw.get(key) or []:
            if isinstance(item, dict) and isinstance(item.get(KEY_URI), str):
                uris.append(item[KEY_URI])
    return uris


def key_basename(uri: str) -> str | None:
    """The basename that identifies an export attachment in message_key, or None if external.

    External URIs (not under EXPORT_URI_PREFIX) may carry per-export query parameters, so they
    are excluded from the key. They still count toward media_count.
    """
    if not uri.startswith(EXPORT_URI_PREFIX):
        return None
    return posixpath.basename(uri)


def share_link(raw: dict[str, Any]) -> str | None:
    """The shared-post link, if the message is a share."""
    share = raw.get(KEY_SHARE)
    if isinstance(share, dict) and isinstance(share.get(KEY_SHARE_LINK), str):
        return str(share[KEY_SHARE_LINK])
    return None
