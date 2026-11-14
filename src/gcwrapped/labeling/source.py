"""The labelable messages and their context (D85).

One definition of "eligible": a message with text whose sender opted in ("yes" or "local") in the
consent ledger for that thread, i.e. `analytics.fct_message_eligibility.local_ok` (D69, D75, D88).
The labeler samples from it, shows context from it and the tagger (S3) will tag and embed from it,
so a revoked consent removes a message everywhere at once. Context is the neighbours in the same
thread that are themselves eligible, in sent order; a neighbour from a "no", blank or unlisted
sender is skipped, not shown.

Message text lives only in memory here: the index is built per run and never written anywhere.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import psycopg

# Sent order matches the redaction audit's (sent_at, message_key), so ties are broken the same way.
ELIGIBLE_SQL = """
SELECT m.thread_id, m.message_key, m.content
FROM raw.messages AS m
JOIN analytics.fct_message_eligibility AS e USING (message_key)
WHERE e.local_ok AND coalesce(m.content ~ '\\S', false)
ORDER BY m.thread_id, m.sent_at, m.message_key
"""


@dataclass(frozen=True)
class Message:
    message_id: str
    thread_id: str
    text: str


class EligibleIndex:
    """Eligible messages grouped by thread in sent order, with neighbour lookups."""

    def __init__(self, messages: Iterable[Message]) -> None:
        self._threads: dict[str, list[Message]] = {}
        self._position: dict[str, tuple[str, int]] = {}
        for message in messages:
            thread = self._threads.setdefault(message.thread_id, [])
            self._position[message.message_id] = (message.thread_id, len(thread))
            thread.append(message)

    def __contains__(self, message_id: object) -> bool:
        return message_id in self._position

    def __len__(self) -> int:
        return len(self._position)

    def get(self, message_id: str) -> Message:
        thread, index = self._position[message_id]
        return self._threads[thread][index]

    def ids_by_thread(self) -> Mapping[str, Sequence[str]]:
        return {t: [m.message_id for m in ms] for t, ms in self._threads.items()}

    def before(self, message_id: str, n: int) -> list[Message]:
        """Up to `n` eligible messages sent just before this one in its thread, oldest first."""
        thread, index = self._position[message_id]
        return self._threads[thread][max(0, index - n) : index]

    def after(self, message_id: str, n: int) -> list[Message]:
        """Up to `n` eligible messages sent just after this one in its thread, oldest first."""
        thread, index = self._position[message_id]
        return self._threads[thread][index + 1 : index + 1 + n]


def load_eligible(url: str) -> EligibleIndex:
    """Read every eligible message from the database `url` names (the caller has checked it)."""
    with psycopg.connect(url) as conn:
        rows = conn.execute(ELIGIBLE_SQL).fetchall()
    return EligibleIndex(Message(str(key), str(thread), str(text)) for thread, key, text in rows)
