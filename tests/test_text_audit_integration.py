"""make text-audit against the real gcw_test database (D95).

The SQL, the shape checks and the output run for real; docker is replaced and the dbt view
analytics.fct_message_text is stood in for by tests/placeholder_support.py (the real seed).
"""

import hashlib
from collections.abc import Iterator

import psycopg
import pytest

from gcwrapped import text_audit as ta
from gcwrapped.config import get_database_settings
from placeholder_support import create_text_view, drop_text_view, seed_rows

pytestmark = pytest.mark.integration

SENTINEL = "SENTINELTEXT"
ROWS = [
    f"{SENTINEL} Ana liked a message you sent",  # a template followed by more words: user text
    "Ana Ruiz liked a message",  # placeholder
    "Reacted 😂 to your message",  # placeholder
    "Sam Lee left the group.",  # placeholder
    "lol",  # user text, 1-3
    "ok then",  # 4-10
    f"{SENTINEL} a longer real message",  # 11+
    "😂😂",  # emoji-only (and 1-3 characters)
    "https://example.com/x",  # URL-only (and 11+)
    "   ",  # blank
    None,  # no content
]


@pytest.fixture
def warehouse(migrated_db: str) -> Iterator[str]:
    get_database_settings.cache_clear()
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        drop_text_view(conn)
        run = conn.execute(
            "INSERT INTO raw.load_runs (roots_count) VALUES (1) RETURNING load_id"
        ).fetchone()
        assert run is not None
        for n, content in enumerate(ROWS):
            conn.execute(
                "INSERT INTO raw.messages (message_key, platform, thread_id, sender_name, sent_at, "
                "content, media_count, source_file, load_id) VALUES "
                "(%s, 'instagram', '111', 'X', now(), %s, 0, 'x', %s)",
                (hashlib.sha256(str(n).encode()).hexdigest(), content, run[0]),
            )
        create_text_view(conn)
    yield migrated_db
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        drop_text_view(conn)


def test_the_counts_come_from_the_real_tables(
    warehouse: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ta.run(lambda: None) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:4] == [
        "target database: gcw_test",
        "messages: 11",
        "non-blank content: 9",
        "placeholders total: 3",
    ]
    by_pattern = dict(line.removeprefix("placeholder ").split(": ") for line in lines[4:-7])
    assert list(by_pattern) == sorted(i for i, _ in seed_rows())  # one line per seed pattern
    assert {k: v for k, v in by_pattern.items() if v != "0"} == {
        "liked_message": "1",
        "reacted_message": "1",
        "group_member_left": "1",
    }
    assert lines[-7:] == [
        "user text messages: 6",
        "user text 1-3 characters: 2",  # lol, 😂😂
        "user text 4-10 characters: 1",
        "user text 11+ characters: 3",  # the sentinel sentence twice and the URL
        "emoji-only user text: 1",
        "URL-only user text: 1",
        "text-audit: ok",
    ]
    assert SENTINEL not in "\n".join(lines) and "Ana" not in "\n".join(lines)
