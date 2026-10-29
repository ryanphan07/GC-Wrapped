"""Canonical message types.

PROVISIONAL: these fields are placeholders until the source platform is chosen
(see CLAUDE.md). Expect `ParsedMessage` to change once the real export format is known.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AttachmentMeta:
    """Metadata about an attachment. Phase 1 never stores the media itself."""

    kind: str
    mime_type: str | None
    size_bytes: int | None
    sha256: str | None


@dataclass(frozen=True, slots=True)
class ParsedMessage:
    """One message as produced by a platform parser, before loading into `raw`."""

    platform: str
    chat_id: str
    source_message_id: str | None
    sent_at: datetime
    sender_raw: str
    body: str | None
    is_system: bool
    attachments: tuple[AttachmentMeta, ...]

    def __post_init__(self) -> None:
        if self.sent_at.tzinfo is None or self.sent_at.utcoffset() is None:
            raise ValueError("sent_at must be timezone-aware")
