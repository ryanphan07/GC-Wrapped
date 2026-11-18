"""The real seed patterns against Postgres' regex engine (D95).

Each template matches exactly its own pattern (names and emoji as wildcards), a near-miss real
sentence does not match, and an emoji-only or short message stays user text. The expression is the
one fct_message_text.sql uses: the whole trimmed content, case-insensitive.
"""

import psycopg
import pytest

from placeholder_support import seed_rows

pytestmark = pytest.mark.integration

# (text, the one pattern id that must match). Names are made up; no real data.
TEMPLATES = [
    ("Ana Ruiz liked a message", "liked_message"),
    ("Liked a message", "liked_message"),
    ("Reacted 😂 to your message", "reacted_message"),
    ("Sam Lee reacted ❤️ to a message", "reacted_message"),
    ("Ana Ruiz sent an attachment.", "sent_attachment"),
    ("Ana Ruiz shared a story.", "shared_story"),
    ("Ana Ruiz mentioned you in their story.", "mentioned_story"),
    ("Ana Ruiz started a video chat.", "video_chat_started"),
    ("The video chat ended.", "video_chat_ended"),
    ("Ana Ruiz missed your video chat.", "video_chat_missed"),
    ("You missed a video chat with Ana Ruiz.", "video_chat_missed"),
    ("Ana Ruiz started an audio call.", "audio_call_started"),
    ("The audio call ended.", "audio_call_ended"),
    ("Ana Ruiz missed your audio call.", "audio_call_missed"),
    ("Ana Ruiz changed the group photo.", "group_photo_changed"),
    ("Ana Ruiz named the group Weekend Crew ☕.", "group_named"),
    ("Ana Ruiz added Sam Lee to the group.", "group_member_added"),
    ("Ana Ruiz removed Sam Lee from the group.", "group_member_removed"),
    ("Sam Lee left the group.", "group_member_left"),
    ("Ana Ruiz unsent a message", "message_unsent"),
    ("Ana Ruiz created a poll: Pizza or tacos?", "poll_created"),
    ("Ana Ruiz voted in a poll.", "poll_voted"),
    ("ANA RUIZ LIKED A MESSAGE", "liked_message"),  # case-insensitive
]
NEAR_MISSES = [
    "i liked a message you sent yesterday",  # a template inside a longer sentence
    "she reacted badly to my message",
    "can you send an attachment later",
    "we should start a video chat",
    "the group photo is great",
    "he left the group chat last week lol",  # not the full template (extra words after "group")
    "who named the group",
    "😂",
    "lol",
    "ok",
    "https://example.com/a",
    "I voted in the poll yesterday",  # a template followed by more words
]


def _matches(conn: psycopg.Connection[tuple[object, ...]], text: str) -> set[str]:
    return {
        pattern_id
        for pattern_id, pattern in seed_rows()
        if conn.execute("SELECT btrim(%s) ~* ('^(?:' || %s || ')$')", (text, pattern)).fetchone()
        == (True,)
    }


def test_every_seed_pattern_has_a_template_here() -> None:
    assert {i for _, i in TEMPLATES} == {i for i, _ in seed_rows()}


@pytest.mark.parametrize(("text", "pattern_id"), TEMPLATES)
def test_a_template_matches_its_own_pattern_only(
    migrated_db: str, text: str, pattern_id: str
) -> None:
    with psycopg.connect(migrated_db) as conn:
        assert _matches(conn, text) == {pattern_id}


@pytest.mark.parametrize("text", NEAR_MISSES)
def test_real_text_is_not_a_placeholder(migrated_db: str, text: str) -> None:
    with psycopg.connect(migrated_db) as conn:
        assert _matches(conn, text) == set()
