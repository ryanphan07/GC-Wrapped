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

import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
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


# --- Words under two segmentations (D83) and folding -----------------------------------------

_RUN = re.compile(r"[^\W_]+")  # alphanumeric runs of folded text; underscore separates words


@dataclass(frozen=True)
class Word:
    """A word of a text: raw offsets and its folded form."""

    start: int
    end: int
    folded: str
    # The folded text between the previous word of the same segmentation and this one. Under the
    # folded segmentation the glue can sit inside one raw character ("⑵" folds to "(2)"), so it is
    # read from the folded text, as the pre-D81 matcher did. Not part of a word's identity.
    gap: str = field(default="", compare=False)


@lru_cache(maxsize=8192)
def _fold_char(ch: str) -> str:
    stripped = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
    return stripped.casefold()


def fold(text: str) -> str:
    """Casefolded, accents stripped."""
    if text.isascii():
        return text.lower()
    return "".join(_fold_char(ch) for ch in text)


def _in_word(ch: str, in_word: bool) -> bool:
    """Whether `ch` continues or starts a raw word: alphanumeric, or a combining mark after one."""
    return ch.isalnum() or (in_word and unicodedata.category(ch).startswith("M"))


def raw_word_spans(text: str) -> list[tuple[int, int]]:
    """Raw words (D81): maximal runs of str.isalnum characters plus combining marks after one."""
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


def _folded_word_spans(text: str) -> list[Word]:
    """Folded words: alphanumeric runs of the per-character fold, mapped back to raw offsets.

    A character that folds to nothing (a combining mark) belongs to the character before it.
    """
    if text.isascii():
        folded_text = text.lower()
        starts: list[int] | None = None
        ends: list[int] | None = None
    else:
        pieces: list[str] = []
        starts, ends = [], []
        for i, ch in enumerate(text):
            folded = _fold_char(ch)
            if not folded:
                if ends:
                    ends[-1] = i + 1
                continue
            pieces.append(folded)
            starts.extend([i] * len(folded))
            ends.extend([i + 1] * len(folded))
        folded_text = "".join(pieces)
    out: list[Word] = []
    previous_end = 0
    for m in _RUN.finditer(folded_text):
        start, end = m.start(), m.end()
        gap = folded_text[previous_end:start]
        previous_end = end
        if starts is not None and ends is not None:
            start, end = starts[start], ends[end - 1]
        out.append(Word(start, end, m.group(), gap))
    return out


def words(text: str) -> tuple[list[Word], list[Word]]:
    """The words of `text` under both segmentations: (raw, folded), each in text order (D83).

    This is the only place a word boundary is decided. Raw words are decided on the raw text and
    folded per word; folded words are alphanumeric runs of the folded text. Every member check
    (matching, the residual count, the backstop, span extension) uses both.
    """
    raw: list[Word] = []
    previous_end = 0
    for s, e in raw_word_spans(text):
        if w := fold(text[s:e]):
            raw.append(Word(s, e, w, fold(text[previous_end:s])))
        previous_end = e
    return raw, _folded_word_spans(text)


def all_words(text: str) -> list[Word]:
    """The union of both segmentations, deduplicated on (start, end, folded), in text order."""
    raw, folded = words(text)
    return sorted({*raw, *folded}, key=lambda w: (w.start, w.end, w.folded))


def _identity(name: str) -> str:
    """The key a display name is known by: folded, whitespace collapsed."""
    return " ".join(fold(name).split())


def _token_ok(word: str) -> bool:
    return len(word) >= 2 and any(c.isalpha() for c in word)


def _tokens(name: str) -> list[str]:
    """Name tokens: folded words of the name under either segmentation, 2+ characters, a letter."""
    return sorted({w.folded for w in all_words(name) if _token_ok(w.folded)})


def _is_symbol(ch: str) -> bool:
    """Not alphanumeric on its own, but folds to something that is (for example "™" or "ⓙ")."""
    return not ch.isalnum() and any(c.isalnum() for c in _fold_char(ch))


# Categories of a token only the folded segmentation produces, judged on the raw characters it came
# from (the audit reports them, D83). Order is the priority when one token has several sources.
FOLDED_ONLY_CATEGORIES = ("one_symbol", "several_symbols", "mixed", "folded_break")


def _folded_only_category(raw: str) -> str:
    symbols = sum(_is_symbol(c) for c in raw)
    regular = sum(c.isalnum() for c in raw)
    if symbols == 0:
        return "folded_break"
    if regular:
        return "mixed"
    return "one_symbol" if symbols == 1 else "several_symbols"


# --- The member directory --------------------------------------------------------------------


@dataclass(frozen=True)
class _Match:
    start: int
    end: int
    identities: frozenset[str]
    words: frozenset[str] = frozenset()  # single-word matches only: the folded words matched


def _gap_pattern(gap: str) -> re.Pattern[str]:
    """Whitespace in a name's gap matches any whitespace run; other glue must match literally."""
    chunks = gap.split()
    body = r"\s+".join(re.escape(c) for c in chunks)
    if not chunks:
        return re.compile(r"\s+")
    lead = r"\s+" if gap[0].isspace() else ""
    trail = r"\s+" if gap[-1].isspace() else ""
    return re.compile(lead + body + trail)


@dataclass(frozen=True)
class _Full:
    """A multi-word full name: its folded words, a pattern per gap, its identity and its length."""

    parts: tuple[str, ...]
    gaps: tuple[re.Pattern[str], ...]
    identity: str
    length: int


class MemberDirectory:
    """Every member display name (all threads), as a name dictionary plus per-thread membership.

    Built from (thread_id, display_name) pairs: in the audit, raw.messages senders and reaction
    actors, the facts analytics.dim_members is built from (D78). The dictionary covers
    everyone, so a "no" sender or a reactor is redacted too (D77). Thread membership only decides
    which pseudonym a match gets. Names and texts are both read under two word segmentations (D83).
    """

    def __init__(self, members: Iterable[tuple[str, str]]) -> None:
        self._thread_members: dict[str, set[str]] = {}
        # token or single-word full name -> identities that contain it
        self._singles: dict[str, set[str]] = {}
        # which segmentation produced each single (a token may come from both)
        self._raw_singles: set[str] = set()
        self._folded_singles: set[str] = set()
        # for tokens only the folded segmentation produced: category of the raw source characters
        self._source_category: dict[str, str] = {}
        # first word of a multi-word full name -> full names starting with it, longest first
        self._multi: dict[str, list[_Full]] = {}
        seen: set[str] = set()
        for thread_id, name in members:
            ident = _identity(name)
            self._thread_members.setdefault(thread_id, set()).add(ident)
            if name in seen:
                continue
            seen.add(name)
            self._add(name, ident)
        for entries in self._multi.values():
            entries.sort(key=lambda e: -e.length)

    def _add(self, name: str, ident: str) -> None:
        raw, folded = words(name)
        for segmentation, is_raw in ((raw, True), (folded, False)):
            for word in segmentation:
                if _token_ok(word.folded):
                    self._register(word.folded, ident, is_raw)
                    if not is_raw:
                        category = _folded_only_category(name[word.start : word.end])
                        known = self._source_category.get(word.folded)
                        if known is None or FOLDED_ONLY_CATEGORIES.index(
                            category
                        ) < FOLDED_ONLY_CATEGORIES.index(known):
                            self._source_category[word.folded] = category
            self._add_full_name(name, segmentation, ident, is_raw)

    def _register(self, word: str, ident: str, is_raw: bool) -> None:
        self._singles.setdefault(word, set()).add(ident)
        (self._raw_singles if is_raw else self._folded_singles).add(word)

    def _add_full_name(self, name: str, ws: Sequence[Word], ident: str, is_raw: bool) -> None:
        # Full name: words only, so an emoji at either end is not part of the name.
        if not ws:
            return
        folded_name = fold(name[ws[0].start : ws[-1].end])
        if len(folded_name) < 2 or not any(c.isalpha() for c in folded_name):
            return
        if len(ws) == 1:
            self._register(ws[0].folded, ident, is_raw)
            return
        gaps = tuple(_gap_pattern(w.gap) for w in ws[1:])
        full = _Full(tuple(w.folded for w in ws), gaps, ident, len(folded_name))
        entries = self._multi.setdefault(ws[0].folded, [])
        if full not in entries:
            entries.append(full)

    @property
    def tokens(self) -> frozenset[str]:
        return frozenset(self._singles)

    @property
    def raw_only_tokens(self) -> frozenset[str]:
        """Tokens only the raw segmentation produces."""
        return frozenset(self._raw_singles - self._folded_singles)

    @property
    def folded_only_tokens(self) -> Mapping[str, str]:
        """Tokens only the folded segmentation produces -> their source category (D83)."""
        return {
            t: self._source_category[t]
            for t in self._folded_singles - self._raw_singles
            if t in self._source_category
        }

    def members_of(self, thread_id: str) -> frozenset[str]:
        return frozenset(self._thread_members.get(thread_id, ()))

    def find(self, text: str) -> list[_Match]:
        """Whole-word, case- and accent-insensitive matches under both segmentations (D83).

        Offsets are into `text`. Matches are non-overlapping: overlapping ones merge into their
        union, which takes the identities of the longest match (a tie: the union of the tied
        matches' identities). A match's identities are every member the matched word could be.
        """
        raw, folded = words(text)
        found = self._scan(raw) + self._scan(folded)
        found.sort(key=lambda m: (m.start, m.end))
        merged: list[_Match] = []
        group: list[_Match] = []
        group_end = -1
        for match in [*found, None]:
            if match is not None and group and match.start < group_end:
                group.append(match)
                group_end = max(group_end, match.end)
                continue
            if group:
                longest = max(m.end - m.start for m in group)
                tied = [m for m in group if m.end - m.start == longest]
                merged.append(
                    _Match(
                        group[0].start,
                        group_end,
                        frozenset().union(*(m.identities for m in tied)),
                        frozenset().union(*(m.words for m in group)),
                    )
                )
            if match is not None:
                group, group_end = [match], match.end
        return merged

    def _scan(self, ws: Sequence[Word]) -> list[_Match]:
        """Matches in one segmentation, longest full name first at each word."""
        matches: list[_Match] = []
        i = 0
        while i < len(ws):
            word = ws[i]
            hit: _Match | None = None
            used = 1
            for full in self._multi.get(word.folded, ()):
                if self._full_name_at(ws, i, full):
                    used = len(full.parts)
                    hit = _Match(word.start, ws[i + used - 1].end, frozenset({full.identity}))
                    break
            if hit is None and word.folded in self._singles:
                hit = _Match(
                    word.start,
                    word.end,
                    frozenset(self._singles[word.folded]),
                    frozenset({word.folded}),
                )
            if hit is not None:
                matches.append(hit)
            i += used
        return matches

    @staticmethod
    def _full_name_at(ws: Sequence[Word], i: int, full: _Full) -> bool:
        if i + len(full.parts) > len(ws):
            return False
        for k, part in enumerate(full.parts):
            if ws[i + k].folded != part:
                return False
            if k and full.gaps[k - 1].fullmatch(ws[i + k].gap) is None:
                return False
        return True


def residual_member_tokens(text: str, directory: MemberDirectory) -> int:
    """How many dictionary tokens are still in `text` as whole words under either segmentation.

    Counts distinct (start, end) spans, so a word both segmentations see counts once. Placeholders
    are ignored. Used by the tests and by `gcw redaction-audit`; a non-zero count on redacted output
    means a member name leaked.
    """
    text = _PLACEHOLDER.sub(" ", text)
    tokens = directory.tokens
    return len({(w.start, w.end) for w in all_words(text) if w.folded in tokens})


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
    """Move each span edge that falls inside a word of either segmentation out to that word's edge
    (D79.2, D83), repeating until no edge is inside a word.

    Without this, a pattern that stops mid-word (a handle capped at 30 characters, a URL ending at
    a non-ASCII letter) leaves the rest of the word standing alone after replacement. Returns the
    spans and how many edges moved.
    """
    ws = sorted({(w.start, w.end) for w in all_words(text)})

    out: list[_Span] = []
    moved = 0
    for span in spans:
        start, end = span.start, span.end
        while cut := [w[0] for w in ws if w[0] < start < w[1]]:
            start = min(cut)
        while cut := [w[1] for w in ws if w[0] < end < w[1]]:
            end = max(cut)
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
