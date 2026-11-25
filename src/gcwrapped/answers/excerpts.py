"""The messages one answer is allowed to cite (D111): the members of the retrieved windows, read
again from raw.messages for this request, grouped into excerpts and numbered.

Consent applies here as everywhere (D88, D95): a message is read only if it is `local_ok` and user
text now, whatever the stored window says. Text lives in memory for the request and is stored
nowhere.

The excerpt rule: the windows' messages, each once; an excerpt is a run of messages of one thread
and session with no other eligible message of that session between them; excerpts are ordered by
the best retrieval rank among their messages' windows, messages inside one by time; numbers run
1..N across all of them.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import psycopg

TOP_K = 5  # retrieval results an answer is built from
EXCERPT_RULE = (
    "window members once each; excerpt = run of consecutive eligible messages of one thread and "
    "session; excerpts by best retrieval rank, messages by time; numbered across excerpts"
)

WINDOWS_SQL = """
SELECT message_key, window_message_keys
FROM enrich.message_embeddings
WHERE embed_version = %(version)s AND message_key = ANY(%(keys)s)
"""
# `position` counts the session's eligible messages in the window builder's order (D103), so two
# messages are consecutive when no other eligible one lies between them.
MESSAGES_SQL = """
WITH eligible AS (
    SELECT m.message_key, m.thread_id, f.session_id, m.sender_name, f.sent_at_local, m.content,
           row_number() OVER (
               PARTITION BY m.thread_id, f.session_id ORDER BY m.sent_at, m.message_key
           ) AS position
    FROM raw.messages AS m
    JOIN analytics.fct_message_eligibility AS e USING (message_key)
    JOIN analytics.fct_message_text AS t USING (message_key)
    JOIN analytics.fct_messages AS f USING (message_key)
    WHERE e.local_ok AND t.has_user_text
)
SELECT message_key, thread_id, session_id, position, sender_name, sent_at_local, content
FROM eligible
WHERE message_key = ANY(%(keys)s)
"""


@dataclass(frozen=True)
class SourceMessage:
    """One eligible message, as read for one request."""

    message_id: str
    thread_id: str
    session_id: str
    position: int  # among the session's eligible messages
    sender: str
    sent_local: datetime
    text: str


@dataclass(frozen=True)
class Source:
    number: int  # what the model cites
    message: SourceMessage


def build_excerpts(
    windows: Sequence[Sequence[str]], messages: Mapping[str, SourceMessage]
) -> list[list[Source]]:
    """Numbered excerpts from the retrieved windows' member IDs, best retrieval rank first.
    A member with no entry in `messages` (not eligible) is left out."""
    rank: dict[str, int] = {}
    for position, members in enumerate(windows):
        for message_id in members:
            if message_id in messages:
                rank.setdefault(message_id, position)
    ordered = sorted(
        (messages[m] for m in rank), key=lambda m: (m.thread_id, m.session_id, m.position)
    )
    runs: list[list[SourceMessage]] = []
    for message in ordered:
        last = runs[-1][-1] if runs else None
        if (
            last is not None
            and (last.thread_id, last.session_id) == (message.thread_id, message.session_id)
            and message.position == last.position + 1
        ):
            runs[-1].append(message)
        else:
            runs.append([message])
    # A stable sort: runs with the same rank keep their thread, session and time order.
    runs.sort(key=lambda run: min(rank[m.message_id] for m in run))
    excerpts: list[list[Source]] = []
    number = 0
    for run in runs:
        excerpts.append([Source(number + n, m) for n, m in enumerate(run, start=1)])
        number += len(run)
    return excerpts


def load_excerpts(
    conn: psycopg.Connection, version: str, hit_ids: Sequence[str]
) -> list[list[Source]]:
    """The excerpts of the search results `hit_ids` (window centres, best first)."""
    if not hit_ids:
        return []
    stored = {
        str(key): [str(m) for m in members]
        for key, members in conn.execute(WINDOWS_SQL, {"version": version, "keys": list(hit_ids)})
    }
    windows = [stored.get(message_id, []) for message_id in hit_ids]
    keys = sorted({m for members in windows for m in members})
    messages = {
        str(key): SourceMessage(
            str(key), str(thread), str(session), int(position), str(sender), sent, str(text)
        )
        for key, thread, session, position, sender, sent, text in conn.execute(
            MESSAGES_SQL, {"keys": keys}
        )
    }
    return build_excerpts(windows, messages)
