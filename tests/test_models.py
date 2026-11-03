from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from gcwrapped.models import ParsedMessage, Reaction


def make_message(sent_at: datetime, content: str | None = "hello") -> ParsedMessage:
    return ParsedMessage(
        message_key="k" * 64,
        platform="instagram",
        thread_id="thread-1",
        thread_title=None,
        sender_name="sender-1",
        sent_at=sent_at,
        content=content,
        media_count=0,
        share_url=None,
        reactions=[Reaction(actor="a", emoji="👍")],
        source_file="m/message_1.json",
    )


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        make_message(datetime(2026, 1, 1, 12, 0))


def test_aware_datetime_accepted() -> None:
    msg = make_message(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    assert msg.sent_at.utcoffset() is not None


def test_message_is_frozen() -> None:
    msg = make_message(datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(ValidationError):
        msg.content = "changed"  # type: ignore[misc]


def test_reaction_is_frozen() -> None:
    with pytest.raises(ValidationError):
        Reaction(actor="a", emoji="👍").emoji = "😂"  # type: ignore[misc]


def test_repr_does_not_leak_content() -> None:
    msg = make_message(datetime(2026, 1, 1, tzinfo=UTC), content="SECRET_BODY")
    assert "SECRET_BODY" not in repr(msg)
    assert "SECRET_BODY" not in str(msg)
