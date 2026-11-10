"""Redaction (D70, D77): the only way message text may leave the machine.

Text goes through Presidio (spaCy NER for people who are not members; built-in recognizers for
phone numbers, emails, URLs, cards, SSNs, IPs and IBANs) plus three custom recognizers: member
names (score 1.0), @handles and US-style street addresses. Spans are replaced here, not by Presidio,
so overlap handling and numbering are ours and testable. No part of a word is left behind (D79):
overlapping spans merge into their union, span edges inside a word move out to the word's edge, and
a final pass replaces any member-dictionary token still present or raises RedactionError.

Guarantees the tests pin:
  - no network call (the model is loaded from the installed wheel; Presidio's download-on-miss path
    is bypassed and tldextract is pinned to its bundled suffix list),
  - no logging of text, names or mappings, and errors carry exception type names only,
  - the pseudonym mapping lives in a local variable of one redact_thread call and is never returned.

Known gaps (D77): nicknames and misspellings that are not in the member directory rely on NER and
can leak; the address pattern is US-centric; name tokens that are common words are over-redacted.
"""

import bisect
import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache

import tldextract
from presidio_analyzer import (
    AnalyzerEngine,
    BatchAnalyzerEngine,
    EntityRecognizer,
    Pattern,
    PatternRecognizer,
    RecognizerRegistry,
    RecognizerResult,
)
from presidio_analyzer.nlp_engine import NlpArtifacts, SpacyNlpEngine
from spacy.language import Language

# Presidio warns once per non-English predefined recognizer it skips; none of it is message text.
for _name in ("presidio-analyzer", "decision_process"):
    logging.getLogger(_name).setLevel(logging.ERROR)

SPACY_MODEL = "en_core_web_lg"
LANGUAGE = "en"
BATCH_SIZE = 64
_BACKSTOP_PASSES = 3  # D79.3

# Counts are keyed by these, in this order (the audit prints them in this order).
PLACEHOLDER_TYPES = (
    "person",
    "phone",
    "email",
    "url",
    "handle",
    "address",
    "card",
    "ssn",
    "ip",
    "iban",
)

_ENTITY_TO_TYPE = {
    "MEMBER_NAME": "person",
    "PERSON": "person",
    "PHONE_NUMBER": "phone",
    "EMAIL_ADDRESS": "email",
    "URL": "url",
    "HANDLE": "handle",
    "ADDRESS": "address",
    "CREDIT_CARD": "card",
    "US_SSN": "ssn",
    "IP_ADDRESS": "ip",
    "IBAN_CODE": "iban",
}
# LOCATION, ORGANIZATION, DATE_TIME and NRP are deliberately absent: they are kept (D77).
_ENTITIES = tuple(_ENTITY_TO_TYPE)
_PERSON_ENTITIES = frozenset({"MEMBER_NAME", "PERSON"})
_PLACEHOLDER = re.compile(r"<(?:PERSON(?:_\d+)?|PHONE|EMAIL|URL|HANDLE|ADDRESS|CARD|SSN|IP|IBAN)>")

_HANDLE_REGEX = r"(?<![\w.@])@[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?"
# Presidio's phone recognizer asks the phonenumbers library for a *valid* number and skips shapes it
# rejects (an unassigned exchange, say). A leaked number is worse than an over-redacted digit run,
# so this accepts any 10-digit North American shape, with or without a country code.
_PHONE_REGEX = r"(?<![\w+])(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)"
# "123 Main Street", "9 W 4th Ave", "4021 Oak Ridge Rd, Apt 5". Case-insensitive on purpose: chats
# are lowercase. At most three words between the number and the suffix keeps "3 more dogs on the
# street" from matching. Non-US formats are a documented gap.
_ADDRESS_REGEX = (
    r"\b\d{1,6}\s+(?:[A-Za-z0-9][A-Za-z0-9.'-]*\s+){1,3}"
    r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|court|ct|way|place|pl|"
    r"terrace|ter|highway|hwy|parkway|pkwy|circle|cir)\b\.?"
    r"(?:,?\s+(?:apt|apartment|unit|suite|ste|#)\.?\s*[A-Za-z0-9-]+)?"
)


class RedactionError(Exception):
    """Base class. Only the class name is ever shown."""


class RedactionModelError(RedactionError):
    """The pinned spaCy model is not installed or could not be loaded."""


# --- Words (D81) and folding -----------------------------------------------------------------


def _in_word(ch: str, in_word: bool) -> bool:
    """Whether `ch` continues or starts a word: a letter, a digit, or a combining mark after one."""
    return ch.isalnum() or (in_word and unicodedata.category(ch).startswith("M"))


def word_spans(text: str) -> list[tuple[int, int]]:
    """The words of `text` as (start, end) offsets, decided on the raw text (D81).

    A word is a maximal run of str.isalnum characters or combining marks that follow one. This is
    the only place a word boundary is decided: member matching, the residual check, the backstop and
    span extension all call it. Folding never creates or removes a boundary.
    """
    spans: list[tuple[int, int]] = []
    start = -1
    for i, ch in enumerate(text):
        if _in_word(ch, start >= 0):
            if start < 0:
                start = i
        elif start >= 0:
            spans.append((start, i))
            start = -1
    if start >= 0:
        spans.append((start, len(text)))
    return spans


@lru_cache(maxsize=8192)
def _fold_char(ch: str) -> str:
    stripped = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
    return stripped.casefold()


def fold(text: str) -> str:
    """Casefolded, accents stripped. Only ever applied inside a word or to a whole name."""
    if text.isascii():
        return text.lower()
    return "".join(_fold_char(ch) for ch in text)


def _folded_words(text: str) -> list[tuple[int, int, str]]:
    """Each word of `text` with its folded form, skipping words that fold to nothing."""
    return [(s, e, w) for s, e in word_spans(text) if (w := fold(text[s:e]))]


def _identity(name: str) -> str:
    """The key a display name is known by: folded, whitespace collapsed."""
    return " ".join(fold(name).split())


def _tokens(name: str) -> list[str]:
    """Name tokens: folded words of the name, at least 2 characters, one letter."""
    return [w for _, _, w in _folded_words(name) if len(w) >= 2 and any(c.isalpha() for c in w)]


# --- The member directory --------------------------------------------------------------------


@dataclass(frozen=True)
class _Match:
    start: int
    end: int
    identities: frozenset[str]


class MemberDirectory:
    """Every member display name (all threads), as a name dictionary plus per-thread membership.

    Built from (thread_id, display_name) pairs: in the audit, raw.messages senders and reaction
    actors, the facts analytics.dim_members is built from (D78). The dictionary covers
    everyone, so a "no" sender or a reactor is redacted too (D77). Thread membership only decides
    which pseudonym a match gets.
    """

    def __init__(self, members: Iterable[tuple[str, str]]) -> None:
        self._thread_members: dict[str, set[str]] = {}
        # token or single-word full name -> identities that contain it
        self._singles: dict[str, set[str]] = {}
        # first word of a multi-word full name -> (words, gaps between them, identity, length),
        # longest first
        self._multi: dict[str, list[tuple[tuple[str, ...], tuple[str, ...], str, int]]] = {}
        seen: set[str] = set()
        for thread_id, name in members:
            ident = _identity(name)
            self._thread_members.setdefault(thread_id, set()).add(ident)
            if name in seen:
                continue
            seen.add(name)
            self._add(name, ident)
        for entries in self._multi.values():
            entries.sort(key=lambda e: -e[3])

    def _add(self, name: str, ident: str) -> None:
        for token in _tokens(name):
            self._singles.setdefault(token, set()).add(ident)
        # Full name: words only, so an emoji at either end is not part of the name.
        words = _folded_words(name)
        if not words:
            return
        folded_name = fold(name[words[0][0] : words[-1][1]])
        if len(folded_name) < 2 or not any(c.isalpha() for c in folded_name):
            return
        if len(words) == 1:
            self._singles.setdefault(words[0][2], set()).add(ident)
            return
        gaps = tuple(fold(name[words[i][1] : words[i + 1][0]]) for i in range(len(words) - 1))
        self._multi.setdefault(words[0][2], []).append(
            (tuple(w for _, _, w in words), gaps, ident, len(folded_name))
        )

    @property
    def tokens(self) -> frozenset[str]:
        return frozenset(self._singles)

    def members_of(self, thread_id: str) -> frozenset[str]:
        return frozenset(self._thread_members.get(thread_id, ()))

    def find(self, text: str) -> list[_Match]:
        """Whole-word, case- and accent-insensitive matches, longest full name first.

        Offsets are into `text`. A match's identities are every member the matched word could be.
        """
        words = _folded_words(text)
        matches: list[_Match] = []
        i = 0
        while i < len(words):
            start, end, word = words[i]
            hit: _Match | None = None
            used = 1
            for parts, gaps, ident, _length in self._multi.get(word, ()):
                if self._full_name_at(text, words, i, parts, gaps):
                    used = len(parts)
                    hit = _Match(start, words[i + used - 1][1], frozenset({ident}))
                    break
            if hit is None and word in self._singles:
                hit = _Match(start, end, frozenset(self._singles[word]))
            if hit is not None:
                matches.append(hit)
            i += used
        return matches

    @staticmethod
    def _full_name_at(
        text: str,
        words: Sequence[tuple[int, int, str]],
        i: int,
        parts: tuple[str, ...],
        gaps: tuple[str, ...],
    ) -> bool:
        if i + len(parts) > len(words):
            return False
        for k, part in enumerate(parts):
            if words[i + k][2] != part:
                return False
            if k:
                gap = text[words[i + k - 1][1] : words[i + k][0]]
                # Whitespace in the name matches any whitespace run; other glue must be the same.
                if gaps[k - 1].isspace():
                    if not gap.isspace():
                        return False
                elif fold(gap) != gaps[k - 1]:
                    return False
        return True


def residual_member_tokens(text: str, directory: MemberDirectory) -> int:
    """How many dictionary tokens are still in `text` as whole words (casefolded, accents removed).

    Placeholders are ignored. Used by the tests and by `gcw redaction-audit`; a non-zero count on
    redacted output means a member name leaked.
    """
    text = _PLACEHOLDER.sub(" ", text)
    tokens = directory.tokens
    return sum(1 for _, _, word in _folded_words(text) if word in tokens)


# --- Presidio wiring -------------------------------------------------------------------------


class _MemberNameRecognizer(EntityRecognizer):
    def __init__(self, directory: MemberDirectory) -> None:
        super().__init__(
            supported_entities=["MEMBER_NAME"],
            name="MemberNameRecognizer",
            supported_language=LANGUAGE,
        )
        self._directory = directory

    def load(self) -> None:
        return None

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
    ) -> list[RecognizerResult]:
        return [
            RecognizerResult(entity_type="MEMBER_NAME", start=m.start, end=m.end, score=1.0)
            for m in self._directory.find(text)
        ]


class _PreloadedSpacyEngine(SpacyNlpEngine):
    """Presidio's spaCy engine around a model we loaded, so it never tries to download one."""

    def __init__(self, nlp: Language) -> None:
        super().__init__(models=[{"lang_code": LANGUAGE, "model_name": SPACY_MODEL}])
        self.nlp = {LANGUAGE: nlp}  # type: ignore[assignment]

    def load(self) -> None:
        return None


def load_model() -> Language:
    """Load the pinned spaCy model once. Raises RedactionModelError if it is not installed."""
    import spacy

    try:
        return spacy.load(SPACY_MODEL)
    except Exception:  # OSError for a missing model; the message could name a path, so drop it
        raise RedactionModelError from None


def _offline_tldextract() -> None:
    """Presidio's email check calls tldextract.extract, which fetches the public suffix list over
    the network by default. Use the snapshot bundled with the package instead."""
    tldextract.tldextract.TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


@dataclass(frozen=True)
class RedactedMessage:
    """One redacted message. Every field is an integer count or placeholder text: no offsets, no
    names, no mapping (D77.6)."""

    sender_label: str
    text: str | None
    counts: Mapping[str, int]  # placeholder type -> how many in `text`; all PLACEHOLDER_TYPES keys
    overlap_merges: int = 0  # spans absorbed into an overlapping, higher-ranked span (D79.1)
    boundary_extensions: int = 0  # span edges moved out to the edge of a word (D79.2)
    backstop_adjacent: int = 0  # backstop replacements touching a placeholder (D79.3)
    backstop_standalone: int = 0  # backstop replacements not touching one (D79.3)


@dataclass
class _Span:
    start: int
    end: int
    entity: str
    score: float
    identities: frozenset[str] | None = None  # members a MEMBER_NAME span could be


@dataclass(frozen=True)
class _Chosen:
    spans: list[_Span]
    merges: int
    extensions: int


def _rank(span: _Span) -> tuple[float, int, int]:
    """Best first: higher score, then longer, then earlier."""
    return (-span.score, -(span.end - span.start), span.start)


def _merge_overlaps(spans: Sequence[_Span]) -> tuple[list[_Span], int]:
    """Replace every group of overlapping spans by its union (D79.1).

    The union takes the entity, score and identities of the span ranked first in the group. Returns
    the merged spans in order and how many spans were absorbed into another.
    """
    merged: list[_Span] = []
    absorbed = 0
    group: list[_Span] = []
    group_end = -1
    for span in [*sorted(spans, key=lambda s: s.start), None]:
        if span is not None and group and span.start < group_end:
            group.append(span)
            group_end = max(group_end, span.end)
            continue
        if group:
            lead = min(group, key=_rank)
            start = min(g.start for g in group)
            merged.append(_Span(start, group_end, lead.entity, lead.score, lead.identities))
            absorbed += len(group) - 1
        if span is not None:
            group, group_end = [span], span.end
    return merged, absorbed


def _extend_to_words(text: str, spans: Sequence[_Span]) -> tuple[list[_Span], int]:
    """Move each span edge that falls inside a word out to the word's edge (D79.2, D81).

    Without this, a pattern that stops mid-word (a handle capped at 30 characters, a URL ending at
    a non-ASCII letter) leaves the rest of the word standing alone after replacement. Returns the
    spans and how many edges moved.
    """
    words = word_spans(text)
    starts = [s for s, _ in words]

    def containing(pos: int) -> tuple[int, int] | None:
        """The word with start < pos < end, i.e. an edge at `pos` cuts it in two."""
        k = bisect.bisect_left(starts, pos) - 1
        if k >= 0 and words[k][0] < pos < words[k][1]:
            return words[k]
        return None

    out: list[_Span] = []
    moved = 0
    for span in spans:
        start, end = span.start, span.end
        if (w := containing(start)) is not None:
            start = w[0]
        if (w := containing(end)) is not None:
            end = w[1]
        moved += (start != span.start) + (end != span.end)
        out.append(_Span(start, end, span.entity, span.score, span.identities))
    return out, moved


def _mask_placeholders(text: str) -> str:
    """`text` with each placeholder replaced by spaces of equal length: same offsets, no words."""
    return _PLACEHOLDER.sub(lambda m: " " * len(m.group()), text)


class _Pseudonyms:
    """<PERSON_n> numbers for one thread, in order of first appearance. Never leaves this module."""

    def __init__(self) -> None:
        self._numbers: dict[str, int] = {}

    def label(self, key: str) -> str:
        number = self._numbers.setdefault(key, len(self._numbers) + 1)
        return f"<PERSON_{number}>"


class Redactor:
    """Build once per process: loads Presidio around the already-loaded model."""

    def __init__(self, directory: MemberDirectory, nlp: Language) -> None:
        _offline_tldextract()
        self._directory = directory
        engine = _PreloadedSpacyEngine(nlp)
        registry = RecognizerRegistry(supported_languages=[LANGUAGE])
        registry.load_predefined_recognizers(nlp_engine=engine, languages=[LANGUAGE])
        registry.add_recognizer(_MemberNameRecognizer(directory))
        registry.add_recognizer(
            PatternRecognizer(
                supported_entity="HANDLE",
                patterns=[Pattern("handle", _HANDLE_REGEX, 0.9)],
                supported_language=LANGUAGE,
            )
        )
        registry.add_recognizer(
            PatternRecognizer(
                supported_entity="PHONE_NUMBER",
                patterns=[Pattern("na_phone", _PHONE_REGEX, 0.6)],
                supported_language=LANGUAGE,
                name="LenientPhoneRecognizer",
            )
        )
        registry.add_recognizer(
            PatternRecognizer(
                supported_entity="ADDRESS",
                patterns=[Pattern("us_address", _ADDRESS_REGEX, 0.8)],
                supported_language=LANGUAGE,
            )
        )
        analyzer = AnalyzerEngine(
            registry=registry, nlp_engine=engine, supported_languages=[LANGUAGE]
        )
        self._batch = BatchAnalyzerEngine(analyzer)

    def redact_thread(
        self, thread_id: str, messages: Sequence[tuple[str, str | None]]
    ) -> list[RedactedMessage]:
        """Redact one thread's messages, in order. Pseudonyms are per call, so per thread.

        Returns the sender label, the redacted text and placeholder counts per message. None or
        empty text passes through unchanged. The mapping from names to numbers is never returned.
        """
        indexes = [i for i, (_, text) in enumerate(messages) if text]
        texts = [text for _, text in messages if text]
        found = self._batch.analyze_iterator(
            texts,
            language=LANGUAGE,
            batch_size=BATCH_SIZE,
            entities=list(_ENTITIES),
        )
        analyzed = dict(zip(indexes, found, strict=True))
        members = self._directory.members_of(thread_id)
        pseudonyms = _Pseudonyms()
        out: list[RedactedMessage] = []
        for i, (sender, text) in enumerate(messages):
            label = pseudonyms.label(_identity(sender))
            if not text:
                out.append(RedactedMessage(label, text, dict.fromkeys(PLACEHOLDER_TYPES, 0)))
                continue
            chosen = self._choose(text, analyzed[i])
            redacted, counts = self._replace(text, chosen.spans, members, pseudonyms)
            redacted, adjacent, standalone = self._backstop(redacted, members, pseudonyms)
            counts["person"] += adjacent + standalone
            out.append(
                RedactedMessage(
                    label,
                    redacted,
                    counts,
                    chosen.merges,
                    chosen.extensions,
                    adjacent,
                    standalone,
                )
            )
        return out

    def _choose(self, text: str, results: Sequence[RecognizerResult]) -> _Chosen:
        """Turn recognizer results into the non-overlapping spans to replace (D79.1, D79.2).

        Scores: a pattern result (handle, URL, email, address, ...) that fully contains any person
        result, member or NER, counts as 1.0, so "@priya_x" is replaced as a handle and not left as
        "@<PERSON_1>_x". Overlapping spans then merge into their union, which takes the type of the
        span ranked first (score, then length); nothing is dropped, so no part of a name is left
        outside the winner. Finally every edge inside a word moves out to the word's edge; that can
        make neighbours overlap, so merging and extending repeat until neither changes anything.
        """
        persons = [r for r in results if r.entity_type in _PERSON_ENTITIES]
        spans: list[_Span] = []
        for r in results:
            if r.entity_type not in _ENTITY_TO_TYPE:
                continue
            if r.entity_type == "MEMBER_NAME":
                hit = self._directory.find(text[r.start : r.end])
                spans.append(
                    _Span(r.start, r.end, r.entity_type, 1.0, hit[0].identities if hit else None)
                )
                continue
            score = float(r.score)
            if r.entity_type not in _PERSON_ENTITIES and any(
                r.start <= p.start and p.end <= r.end for p in persons
            ):
                score = 1.0
            spans.append(_Span(r.start, r.end, r.entity_type, score))
        merges = extensions = 0
        while True:
            spans, absorbed = _merge_overlaps(spans)
            spans, moved = _extend_to_words(text, spans)
            merges += absorbed
            extensions += moved
            if moved == 0:
                return _Chosen(spans, merges, extensions)

    def _backstop(
        self, text: str, members: frozenset[str], pseudonyms: _Pseudonyms
    ) -> tuple[str, int, int]:
        """Replace any member-dictionary token still in `text` (D79.3). Fails closed.

        Placeholders are masked by spaces first, so a word inside one ("person") is never matched.
        Returns the text, how many replacements touched a placeholder and how many did not. Raises
        RedactionError if a token is still there after _BACKSTOP_PASSES passes.
        """
        adjacent = standalone = 0
        for _ in range(_BACKSTOP_PASSES):
            matches = self._directory.find(_mask_placeholders(text))
            if not matches:
                return text, adjacent, standalone
            held = [m.span() for m in _PLACEHOLDER.finditer(text)]
            ends = {end for _, end in held}
            starts = {start for start, _ in held}
            pieces: list[str] = []
            cursor = 0
            for match in matches:
                if match.start in ends or match.end in starts:
                    adjacent += 1
                else:
                    standalone += 1
                span = _Span(match.start, match.end, "MEMBER_NAME", 1.0, match.identities)
                pieces.append(text[cursor : match.start])
                pieces.append(_person_placeholder(span, text, members, pseudonyms))
                cursor = match.end
            pieces.append(text[cursor:])
            text = "".join(pieces)
        if self._directory.find(_mask_placeholders(text)):
            raise RedactionError
        return text, adjacent, standalone

    @staticmethod
    def _replace(
        text: str, spans: Sequence[_Span], members: frozenset[str], pseudonyms: _Pseudonyms
    ) -> tuple[str, dict[str, int]]:
        counts = Counter[str]()
        pieces: list[str] = []
        cursor = 0
        for span in spans:
            kind = _ENTITY_TO_TYPE[span.entity]
            counts[kind] += 1
            pieces.append(text[cursor : span.start])
            if kind == "person":
                pieces.append(_person_placeholder(span, text, members, pseudonyms))
            else:
                pieces.append(f"<{kind.upper()}>")
            cursor = span.end
        pieces.append(text[cursor:])
        return "".join(pieces), {t: counts[t] for t in PLACEHOLDER_TYPES}


def _person_placeholder(
    span: _Span, text: str, members: frozenset[str], pseudonyms: _Pseudonyms
) -> str:
    if span.entity == "MEMBER_NAME" and span.identities is not None:
        here = span.identities & members
        if len(here) == 1:
            return pseudonyms.label(next(iter(here)))
        if len(here) > 1:
            return "<PERSON>"  # a token that could be more than one member of this thread
        if len(span.identities) > 1:
            return "<PERSON>"
        return pseudonyms.label("~" + next(iter(span.identities)))  # a member of another thread
    # A person only NER found: numbered by its casefolded text.
    return pseudonyms.label("~" + " ".join(fold(text[span.start : span.end]).split()))
