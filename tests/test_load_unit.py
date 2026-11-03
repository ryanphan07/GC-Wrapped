import json
from datetime import UTC, datetime
from pathlib import Path

from gcwrapped.db import database_name
from gcwrapped.load import COLUMNS, LoadError, LoadResult, message_to_row, reactions_json
from gcwrapped.models import ParsedMessage, Reaction
from gcwrapped.parsers.instagram import parse_export
from synthetic.instagram import generate_pair


def _msg(**overrides: object) -> ParsedMessage:
    fields: dict[str, object] = {
        "message_key": "k" * 64,
        "platform": "instagram",
        "thread_id": "t1",
        "thread_title": "Title",
        "sender_name": "Sender",
        "sent_at": datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
        "content": "hello",
        "media_count": 2,
        "share_url": "https://example.com/x",
        "reactions": [Reaction(actor="A", emoji="👍")],
        "source_file": "messages/inbox/t1/message_1.json",
    }
    fields.update(overrides)
    return ParsedMessage.model_validate(fields)


def test_reactions_json_empty() -> None:
    assert reactions_json([]) == "[]"


def test_reactions_json_keeps_emoji_unescaped_and_round_trips() -> None:
    reactions = [Reaction(actor="A", emoji="👍"), Reaction(actor="B", emoji="❤️")]

    out = reactions_json(reactions)

    assert "👍" in out
    assert json.loads(out) == [
        {"actor": "A", "emoji": "👍"},
        {"actor": "B", "emoji": "❤️"},
    ]


def test_message_to_row_matches_column_order() -> None:
    msg = _msg()

    row = message_to_row(msg)

    assert len(row) == len(COLUMNS)
    by_name = dict(zip(COLUMNS, row, strict=True))
    assert by_name["message_key"] == msg.message_key
    assert by_name["thread_id"] == "t1"
    assert by_name["sender_name"] == "Sender"
    assert by_name["sent_at"] == msg.sent_at
    assert by_name["media_count"] == 2
    assert by_name["share_url"] == "https://example.com/x"
    assert json.loads(str(by_name["reactions"])) == [{"actor": "A", "emoji": "👍"}]
    assert by_name["source_file"] == "messages/inbox/t1/message_1.json"


def test_message_to_row_passes_nulls_through() -> None:
    row = message_to_row(_msg(thread_title=None, content=None, share_url=None, reactions=[]))

    by_name = dict(zip(COLUMNS, row, strict=True))
    assert by_name["thread_title"] is None
    assert by_name["content"] is None
    assert by_name["share_url"] is None
    assert by_name["reactions"] == "[]"


def test_every_synthetic_message_converts_to_a_full_row(tmp_path: Path) -> None:
    a, _ = generate_pair(tmp_path)

    rows = [message_to_row(m) for m in parse_export([a])]

    assert rows
    assert all(len(r) == len(COLUMNS) for r in rows)


def test_duplicates_skipped_is_parsed_minus_inserted() -> None:
    assert LoadResult(load_id=1, rows_parsed=10, rows_inserted=4).duplicates_skipped == 6


def test_database_name_from_url_hides_credentials() -> None:
    assert database_name("postgresql://user:secret@host:5432/gcw_dev") == "gcw_dev"
    assert database_name("postgresql://user:secret@host/other") == "other"


def test_load_error_summary_has_class_row_and_field_only() -> None:
    assert LoadError("DataError", 7, "content").summary == "DataError at row 7 in field content"
    assert LoadError("DataError", 7, None).summary == "DataError at row 7"
    assert LoadError("DataError", None, None).summary == "DataError"
    assert str(LoadError("DataError", 7, "content")) == "DataError at row 7 in field content"
