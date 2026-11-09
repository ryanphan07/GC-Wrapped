"""Redaction (D70, D77): the only way message text may leave the machine.

Text goes through Presidio (spaCy NER for people who are not members; built-in recognizers for
phone numbers, emails, URLs, cards, SSNs, IPs and IBANs) plus three custom recognizers: member
names (score 1.0, so they win overlaps), @handles and US-style street addresses. Spans are replaced
here, not by Presidio, so overlap handling and numbering are ours and testable.

Guarantees the tests pin:
  - no network call (the model is loaded from the installed wheel; Presidio's download-on-miss path
    is bypassed and tldextract is pinned to its bundled suffix list),
  - no logging of text, names or mappings, and errors carry exception type names only,
  - the pseudonym mapping lives in a local variable of one redact_thread call and is never returned.

Known gaps (D77): nicknames and misspellings that are not in the member directory rely on NER and
can leak; the address pattern is US-centric; name tokens that are common words are over-redacted.
"""

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

# Alphanumeric runs: the unit names are split into and matched on (underscore separates words).
_RUN = re.compile(r"[^\W_]+")
_NOT_RUN_CHAR = r"(?![^\W_])"

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


# --- Folding: case- and accent-insensitive matching, with offsets into the original ---------


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
    """The key a display name is known by: folded, whitespace collapsed."""
    return " ".join(fold(name).split())


def _tokens(name: str) -> list[str]:
    """Name tokens: alphanumeric runs of the folded name, at least 2 characters, one letter."""
    return [r for r in _RUN.findall(fold(name)) if len(r) >= 2 and any(c.isalpha() for c in r)]


# --- The member directory --------------------------------------------------------------------


@dataclass(frozen=True)
class _Match:
    start: int
    end: int
    identities: frozenset[str]


class MemberDirectory:
    """Every member display name (all threads), as a name dictionary plus per-thread membership.

    Built from (thread_id, display_name) pairs, i.e. analytics.dim_members. The dictionary covers
    everyone, so a "no" sender or a reactor is redacted too (D77). Thread membership only decides
    which pseudonym a match gets.
    """

    def __init__(self, members: Iterable[tuple[str, str]]) -> None:
        self._thread_members: dict[str, set[str]] = {}
        # token or single-word full name -> identities that contain it
        self._singles: dict[str, set[str]] = {}
        # first run of a multi-run full name -> (pattern, identity), longest first
        self._multi: dict[str, list[tuple[re.Pattern[str], str, int]]] = {}
        seen: set[str] = set()
        for thread_id, name in members:
            ident = _identity(name)
            self._thread_members.setdefault(thread_id, set()).add(ident)
            if name in seen:
                continue
            seen.add(name)
            self._add(name, ident)
        for entries in self._multi.values():
            entries.sort(key=lambda e: -e[2])

    def _add(self, name: str, ident: str) -> None:
        for token in _tokens(name):
            self._singles.setdefault(token, set()).add(ident)
        # Full name: trim non-alphanumerics at both ends (an emoji is not part of the name).
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

    @property
    def tokens(self) -> frozenset[str]:
        return frozenset(self._singles)

    def members_of(self, thread_id: str) -> frozenset[str]:
        return frozenset(self._thread_members.get(thread_id, ()))

    def find(self, text: str) -> list[_Match]:
        """Whole-word, case- and accent-insensitive matches, longest full name first.

        Offsets are into `text`. A match's identities are every member the matched word could be.
        """
        folded = _Folded(text)
        matches: list[_Match] = []
        covered_until = 0
        for run in _RUN.finditer(folded.text):
            if run.start() < covered_until:
                continue
            word = run.group()
            hit: _Match | None = None
            for pattern, ident, _length in self._multi.get(word, ()):
                m = pattern.match(folded.text, run.start())
                if m:
                    start, end = folded.original_span(m.start(), m.end())
                    hit = _Match(start, end, frozenset({ident}))
                    covered_until = m.end()
                    break
            if hit is None and word in self._singles:
                start, end = folded.original_span(run.start(), run.end())
                hit = _Match(start, end, frozenset(self._singles[word]))
            if hit is not None:
                matches.append(hit)
        return matches


def residual_member_tokens(text: str, directory: MemberDirectory) -> int:
    """How many dictionary tokens are still in `text` as whole words (casefolded, accents removed).

    Placeholders are ignored. Used by the tests and by `gcw redaction-audit`; a non-zero count on
    redacted output means a member name leaked.
    """
    folded = fold(_PLACEHOLDER.sub(" ", text))
    tokens = directory.tokens
    return sum(1 for run in _RUN.finditer(folded) if run.group() in tokens)


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
    sender_label: str
    text: str | None
    counts: Mapping[str, int]  # placeholder type -> how many in `text`; all PLACEHOLDER_TYPES keys


@dataclass
class _Span:
    start: int
    end: int
    entity: str
    score: float
    identities: frozenset[str] | None = None  # members a person span could be
    name_key: str = ""  # folded text, for people only NER found


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
            spans = self._choose(text, analyzed[i])
            redacted, counts = self._replace(text, spans, members, pseudonyms)
            out.append(RedactedMessage(label, redacted, counts))
        return out

    def _choose(self, text: str, results: Sequence[RecognizerResult]) -> list[_Span]:
        """Merge overlapping person spans, then keep the best non-overlapping spans.

        Best means higher score, then longer; a pattern span that contains a member name counts as
        score 1.0, so it wins over the name by being longer. A person span that overlaps a member
        match becomes one span (so "Priya Sharma" is not left as "<PERSON_1> Sharma") with the
        member's score.
        """
        people = sorted(
            (r for r in results if r.entity_type in _PERSON_ENTITIES), key=lambda r: r.start
        )
        spans: list[_Span] = []
        group: list[RecognizerResult] = []
        group_end = -1
        for r in [*people, None]:
            if r is not None and group and r.start < group_end:
                group.append(r)
                group_end = max(group_end, r.end)
                continue
            if group:
                spans.append(self._person_span(text, group))
            if r is not None:
                group, group_end = [r], r.end
        person_spans = list(spans)
        for r in results:
            if r.entity_type not in _ENTITY_TO_TYPE or r.entity_type in _PERSON_ENTITIES:
                continue
            # A handle, URL, email or address that contains a member name is replaced whole:
            # "@priya_x" must not become "@<PERSON_1>_x", which leaves part of the handle behind.
            holds_name = any(r.start <= p.start and p.end <= r.end for p in person_spans)
            spans.append(
                _Span(r.start, r.end, r.entity_type, 1.0 if holds_name else float(r.score))
            )
        spans.sort(key=lambda s: (-s.score, -(s.end - s.start), s.start))
        chosen: list[_Span] = []
        for span in spans:
            if all(span.end <= c.start or span.start >= c.end for c in chosen):
                chosen.append(span)
        chosen.sort(key=lambda s: s.start)
        return chosen

    def _person_span(self, text: str, group: Sequence[RecognizerResult]) -> _Span:
        start = min(r.start for r in group)
        end = max(r.end for r in group)
        member = next((r for r in group if r.entity_type == "MEMBER_NAME"), None)
        if member is not None:
            hit = self._directory.find(text[member.start : member.end])
            return _Span(start, end, "MEMBER_NAME", 1.0, hit[0].identities if hit else None)
        score = max(float(r.score) for r in group)
        return _Span(start, end, "PERSON", score, None, " ".join(fold(text[start:end]).split()))

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
                pieces.append(_person_placeholder(span, members, pseudonyms))
            else:
                pieces.append(f"<{kind.upper()}>")
            cursor = span.end
        pieces.append(text[cursor:])
        return "".join(pieces), {t: counts[t] for t in PLACEHOLDER_TYPES}


def _person_placeholder(span: _Span, members: frozenset[str], pseudonyms: _Pseudonyms) -> str:
    if span.entity == "MEMBER_NAME" and span.identities is not None:
        here = span.identities & members
        if len(here) == 1:
            return pseudonyms.label(next(iter(here)))
        if len(here) > 1:
            return "<PERSON>"  # a token that could be more than one member of this thread
        if len(span.identities) > 1:
            return "<PERSON>"
        return pseudonyms.label("~" + next(iter(span.identities)))  # a member of another thread
    return pseudonyms.label("~" + span.name_key)
