"""The retrieval unit (D103): the one place window text is built, and the embed version.

A message's window is the message plus up to BEFORE eligible messages sent just before it and AFTER
just after it, in the same thread and conversation session, one "sender: text" line each, in time
order. Eligible means what it means everywhere else: `local_ok` and `has_user_text` (D88, D95), for
the centre and for every neighbour, so an ineligible message is skipped and the next eligible one
of the session takes its place. The session is the dbt one (`analytics.fct_messages.session_id`),
computed over all messages, eligible or not.

Message text lives only in memory here and in the request to the local embedding server. The
window text itself is never stored: the warehouse keeps the member IDs, from which it can be
rebuilt, and its full-text lexemes (`window_tsv`, migration 006), which pruning deletes with the
row.
"""

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import groupby

import psycopg

from gcwrapped.tagging.model import EMBED_MODEL_ID, EMBED_REGISTRY

BEFORE = 2
AFTER = 2
# The cap on window text, in characters. The server takes the whole window in one batch of 8192
# tokens (docker-compose.yml); 2000 characters stays inside that even for scripts and emoji that
# cost several tokens per character, and is far above an ordinary five-message window.
MAX_WINDOW_CHARS = 2000
LINE_FORMAT = "{sender}: {text}"
SESSION_RULE = "analytics.fct_messages.session_id"
DIMENSIONS = 1024
# The model card's query prefix (D102). Stored windows get no prefix. The server appends the
# end-of-text token itself, for queries and windows alike.
QUERY_INSTRUCTION = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"
)

SOURCE_SQL = """
SELECT m.message_key, m.thread_id, f.session_id, m.sender_name, m.content
FROM raw.messages AS m
JOIN analytics.fct_message_eligibility AS e USING (message_key)
JOIN analytics.fct_message_text AS t USING (message_key)
JOIN analytics.fct_messages AS f USING (message_key)
WHERE e.local_ok AND t.has_user_text
ORDER BY m.thread_id, m.sent_at, m.message_key
"""


@dataclass(frozen=True)
class SourceMessage:
    """One eligible message, as read for window building."""

    message_id: str
    thread_id: str
    session_id: str
    sender: str
    text: str


@dataclass(frozen=True)
class Window:
    message_id: str  # the centre
    member_ids: tuple[str, ...]  # every message in the text, in time order; includes the centre
    text: str


def _line(message: SourceMessage) -> str:
    """One line per message: any line breaks inside the sender or the text become spaces."""
    return LINE_FORMAT.format(
        sender=" ".join(message.sender.split()), text=" ".join(message.text.split())
    )


def build_window(session: Sequence[SourceMessage], center: int) -> Window:
    """The window of `session[center]`. `session` holds one session's eligible messages in order.

    While the text is over MAX_WINDOW_CHARS the farthest neighbour is dropped, the later one first
    when two are equally far (a reply depends more on what came before it). A centre that is too
    long on its own is cut to the cap.
    """
    members = list(range(max(0, center - BEFORE), min(len(session), center + AFTER + 1)))
    lines = {i: _line(session[i]) for i in members}
    while len(members) > 1 and len("\n".join(lines[i] for i in members)) > MAX_WINDOW_CHARS:
        members.remove(max(members, key=lambda i: (abs(i - center), i > center)))
    return Window(
        message_id=session[center].message_id,
        member_ids=tuple(session[i].message_id for i in members),
        text="\n".join(lines[i] for i in members)[:MAX_WINDOW_CHARS],
    )


def build_windows(messages: Iterable[SourceMessage]) -> list[Window]:
    """A window per message. `messages` are eligible only, ordered by thread, then time."""
    windows: list[Window] = []
    for _, group in groupby(messages, key=lambda m: (m.thread_id, m.session_id)):
        session = list(group)
        windows += [build_window(session, i) for i in range(len(session))]
    return windows


def load_windows(conn: psycopg.Connection) -> list[Window]:
    """Every eligible message's window, read from the database the caller has checked."""
    rows = conn.execute(SOURCE_SQL).fetchall()
    return build_windows(SourceMessage(*(str(value) for value in row)) for row in rows)


def embed_version(model_id: str = EMBED_MODEL_ID) -> str:
    """A short hash of everything that shapes a vector or a query (D102, D103).

    A change to the model file, the window rule or the query instruction gives a new version, so
    `make embed` re-embeds and search never mixes vectors made two different ways.
    """
    material = {
        "model_sha256": EMBED_REGISTRY[model_id].sha256,
        "before": BEFORE,
        "after": AFTER,
        "max_window_chars": MAX_WINDOW_CHARS,
        "line_format": LINE_FORMAT,
        "session_rule": SESSION_RULE,
        "dimensions": DIMENSIONS,
        "query_instruction": QUERY_INSTRUCTION,
    }
    encoded = json.dumps(material, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]
