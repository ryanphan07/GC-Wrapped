import dataclasses
from datetime import UTC, datetime

import pytest

from gcwrapped.models import AttachmentMeta, ParsedMessage


def make_message(sent_at: datetime) -> ParsedMessage:
    return ParsedMessage(
        platform="test",
        chat_id="chat-1",
        source_message_id=None,
        sent_at=sent_at,
        sender_raw="sender-1",
        body="hello",
        is_system=False,
        attachments=(),
    )


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        make_message(datetime(2026, 1, 1, 12, 0))


def test_aware_datetime_accepted() -> None:
    msg = make_message(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    assert msg.sent_at.tzinfo is UTC


def test_message_is_frozen() -> None:
    msg = make_message(datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(dataclasses.FrozenInstanceError):
        msg.body = "changed"  # type: ignore[misc]


def test_attachment_is_frozen() -> None:
    att = AttachmentMeta(kind="image", mime_type="image/jpeg", size_bytes=10, sha256=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        att.kind = "video"  # type: ignore[misc]
