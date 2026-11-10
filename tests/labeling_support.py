"""Helpers for the labeling tests: a scripted terminal, a synthetic index and a stepping clock."""

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from gcwrapped.labeling.source import EligibleIndex, Message


def message_id(label: str) -> str:
    """A 64-hex ID like a real message_key, derived from a readable label."""
    return hashlib.sha256(label.encode()).hexdigest()


def make_index(threads: dict[str, list[str]]) -> EligibleIndex:
    """An index of synthetic messages: {thread_id: [text, ...]} in sent order.

    The ID of the n-th message of a thread is message_id(f"{thread_id}/{n}").
    """
    return EligibleIndex(
        Message(message_id(f"{thread}/{n}"), thread, text)
        for thread, texts in threads.items()
        for n, text in enumerate(texts)
    )


class Scripted:
    """A Terminal fed from strings. Keys come one character at a time; lines from a list.

    Running out of input is end of input (a quit), as with a closed stdin. Everything the flow
    printed or prompted is kept in `printed`, so a test can assert on what was (not) shown.
    """

    def __init__(self, keys: str = "", lines: Sequence[str] = ()) -> None:
        self._keys = iter(keys)
        self._lines = iter(lines)
        self.printed: list[str] = []

    def key(self) -> str:
        return next(self._keys, "")

    def line(self, prompt: str) -> str | None:
        self.printed.append(prompt)
        return next(self._lines, None)

    def out(self, text: str) -> None:
        self.printed.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self.printed)


class StepClock:
    """A clock that moves one second per call, so timestamps are ordered and reproducible."""

    def __init__(self) -> None:
        self._now = datetime(2026, 11, 10, tzinfo=UTC)

    def __call__(self) -> datetime:
        self._now += timedelta(seconds=1)
        return self._now
