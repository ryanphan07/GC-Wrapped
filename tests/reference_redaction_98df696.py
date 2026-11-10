"""Frozen, test-only copy of the folding and member matching of commit 98df696 (before D81).

It is the reference the D83 superset test compares against: every character the old `find` covered
must still be covered by the new one. Never import this from src, and never edit it: it exists to
stay as it was. Only the folding (`_Folded`, `fold`, `_tokens`, `_RUN`) and `MemberDirectory`
(`_add`, `find`) are kept; the Presidio wiring is not needed.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

_RUN = re.compile(r"[^\W_]+")
_NOT_RUN_CHAR = r"(?![^\W_])"


@lru_cache(maxsize=8192)
def _fold_char(ch: str) -> str:
    stripped = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
    return stripped.casefold()


class _Folded:
    """`text` casefolded with accents stripped, and a way back to offsets in the original."""

    def __init__(self, original: str) -> None:
        self.original = original
        self._starts: list[int] | None = None
        self._ends: list[int] | None = None
        if original.isascii():
            self.text = original.lower()  # one output char per input char for ASCII
            return
        pieces: list[str] = []
        starts: list[int] = []
        ends: list[int] = []
        for i, ch in enumerate(original):
            folded = _fold_char(ch)
            if not folded:
                if ends:  # a combining mark belongs to the character before it
                    ends[-1] = i + 1
                continue
            pieces.append(folded)
            starts.extend([i] * len(folded))
            ends.extend([i + 1] * len(folded))
        self.text = "".join(pieces)
        self._starts, self._ends = starts, ends

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        if self._starts is None or self._ends is None:
            return start, end
        return self._starts[start], self._ends[end - 1]


def fold(text: str) -> str:
    return _Folded(text).text


def _identity(name: str) -> str:
    return " ".join(fold(name).split())


def _tokens(name: str) -> list[str]:
    return [r for r in _RUN.findall(fold(name)) if len(r) >= 2 and any(c.isalpha() for c in r)]


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    identities: frozenset[str]


class ReferenceDirectory:
    def __init__(self, members: Iterable[tuple[str, str]]) -> None:
        self._singles: dict[str, set[str]] = {}
        self._multi: dict[str, list[tuple[re.Pattern[str], str, int]]] = {}
        seen: set[str] = set()
        for _thread_id, name in members:
            ident = _identity(name)
            if name in seen:
                continue
            seen.add(name)
            self._add(name, ident)
        for entries in self._multi.values():
            entries.sort(key=lambda e: -e[2])

    def _add(self, name: str, ident: str) -> None:
        for token in _tokens(name):
            self._singles.setdefault(token, set()).add(ident)
        runs = list(_RUN.finditer(fold(name)))
        if not runs:
            return
        folded = fold(name)
        trimmed = folded[runs[0].start() : runs[-1].end()]
        if len(trimmed) < 2 or not any(c.isalpha() for c in trimmed):
            return
        if len(runs) == 1:
            self._singles.setdefault(trimmed, set()).add(ident)
            return
        pattern = r"\s+".join(re.escape(part) for part in trimmed.split()) + _NOT_RUN_CHAR
        self._multi.setdefault(runs[0].group(), []).append(
            (re.compile(pattern), ident, len(trimmed))
        )

    def find(self, text: str) -> list[Match]:
        folded = _Folded(text)
        matches: list[Match] = []
        covered_until = 0
        for run in _RUN.finditer(folded.text):
            if run.start() < covered_until:
                continue
            word = run.group()
            hit: Match | None = None
            for pattern, ident, _length in self._multi.get(word, ()):
                m = pattern.match(folded.text, run.start())
                if m:
                    start, end = folded.original_span(m.start(), m.end())
                    hit = Match(start, end, frozenset({ident}))
                    covered_until = m.end()
                    break
            if hit is None and word in self._singles:
                start, end = folded.original_span(run.start(), run.end())
                hit = Match(start, end, frozenset(self._singles[word]))
            if hit is not None:
                matches.append(hit)
        return matches
