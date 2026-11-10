"""Redaction tests (D70, D76, D77). Synthetic names only: SENTINELNAME-style, so a leak is obvious.

Tests whose name or docstring says "model-dependent" rely on the pinned spaCy model's NER; the
scored NER set and its gate are P2-2b. Everything else is deterministic and must pass at 100%.
"""

import dataclasses
import itertools
import re
import socket
import string
import unicodedata
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import NoReturn

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from presidio_analyzer import RecognizerResult
from spacy.language import Language

from gcwrapped import redaction
from gcwrapped.redaction import (
    PLACEHOLDER_TYPES,
    MemberDirectory,
    RedactionError,
    RedactionModelError,
    Redactor,
    load_model,
    residual_member_tokens,
)
from reference_redaction_98df696 import ReferenceDirectory

T1 = "1000000000000001"
T2 = "1000000000000002"

PRIYA = "SENTINELNAMEA Priya"
JOSE = "SENTINELNAMEB, José"
DMITRI = "SENTINELNAMEC Dmitri 🔥"
SAM1 = "SENTINELNAMED Sam"
SAM2 = "SENTINELNAMEE Sam"
GWEN = "SENTINELNAMEG Gwen"  # in the directory, never a sender: a reactor or a "no" sender
HANA = "SENTINELNAMEH Hana"  # a member of another thread
WEN = "SENTINELNAMEW Wen Q"  # "Q" is under 2 characters
ZED = "Zed Nobody"  # the default sender in one(): takes <PERSON_1>, so mentions start at 2

MEMBERS = [
    (T1, PRIYA),
    (T1, JOSE),
    (T1, DMITRI),
    (T1, SAM1),
    (T1, SAM2),
    (T1, GWEN),
    (T1, WEN),
    (T2, HANA),
]


@pytest.fixture(scope="session")
def nlp() -> Language:
    return load_model()


@pytest.fixture(scope="module", autouse=True)
def _no_network() -> Iterator[None]:
    """Redaction makes no network calls: any attempt to connect or resolve a name fails the test."""

    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("network access attempted during redaction")

    patch = pytest.MonkeyPatch()
    patch.setattr(socket.socket, "connect", refuse)
    patch.setattr(socket, "getaddrinfo", refuse)
    yield
    patch.undo()


@pytest.fixture(scope="module")
def directory() -> MemberDirectory:
    return MemberDirectory(MEMBERS)


@pytest.fixture(scope="module")
def redactor(nlp: Language, directory: MemberDirectory, _no_network: None) -> Redactor:
    return Redactor(directory, nlp)


def one(redactor: Redactor, text: str, sender: str = ZED, thread: str = T1) -> str:
    result = redactor.redact_thread(thread, [(sender, text)])[0].text
    assert result is not None
    return result


# --- Member names ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Priya",
        "priya",
        "PRIYA",
        "SENTINELNAMEA",
        "sentinelnamea priya",
        "Priya's",
        "(Priya),",
        "priya!!",
        "priya... and",
        "hey 🔥Priya🔥 hi",
        "so yesterday I saw Priya at the shop and she said hi",
        "Jose",
        "JOSE",
        "josé",
        "José's",
        "SENTINELNAMEB, Jose",
        "sentinelnameb jose",
        "Dmitri",
        "dmitri",
        "SENTINELNAMEC Dmitri",
        "Gwen",
        "Priya\nPriya\tpriya",
    ],
)
def test_member_names_are_redacted_in_every_form(
    redactor: Redactor, directory: MemberDirectory, text: str
) -> None:
    out = one(redactor, text)
    assert residual_member_tokens(out, directory) == 0
    assert "<PERSON_" in out
    assert out.count("<PERSON_") >= 1


def test_possessive_and_punctuation_keep_surroundings(redactor: Redactor) -> None:
    assert one(redactor, "Priya's here, (Gwen).") == "<PERSON_2>'s here, (<PERSON_3>)."


def test_decomposed_accent_is_removed_with_its_letter(redactor: Redactor) -> None:
    out = one(redactor, "hi José bye")
    assert out == "hi <PERSON_2> bye"
    assert "́" not in out


def test_whole_words_only(directory: MemberDirectory) -> None:
    assert directory.find("Priyanka Gwendolyn Priyas xgwen") == []
    assert len(directory.find("Priya.Gwen")) == 2


def test_sender_label_matches_mentions_in_the_same_thread(redactor: Redactor) -> None:
    result = redactor.redact_thread(
        T1,
        [(PRIYA, "hello"), (JOSE, "hi Priya"), (PRIYA, "hi SENTINELNAMEB jose")],
    )
    assert [r.sender_label for r in result] == ["<PERSON_1>", "<PERSON_2>", "<PERSON_1>"]
    assert result[1].text == "hi <PERSON_1>"
    assert result[2].text == "hi <PERSON_2> <PERSON_2>"


def test_numbers_follow_first_appearance_and_reset_per_thread(redactor: Redactor) -> None:
    first = redactor.redact_thread(T1, [(JOSE, "x"), (PRIYA, "y")])
    assert [r.sender_label for r in first] == ["<PERSON_1>", "<PERSON_2>"]
    second = redactor.redact_thread(T1, [(PRIYA, "x"), (JOSE, "y")])
    assert [r.sender_label for r in second] == ["<PERSON_1>", "<PERSON_2>"]


def test_same_input_same_output(nlp: Language, redactor: Redactor) -> None:
    messages = [
        (PRIYA, "call Priya on +1 415 867 5309"),
        (JOSE, "my cousin Daniela and jose and @someone"),
        (DMITRI, None),
    ]
    again = Redactor(MemberDirectory(MEMBERS), nlp)
    assert redactor.redact_thread(T1, messages) == redactor.redact_thread(T1, messages)
    assert redactor.redact_thread(T1, messages) == again.redact_thread(T1, messages)


def test_ambiguous_token_becomes_unnumbered_person(redactor: Redactor) -> None:
    assert one(redactor, "Sam is late") == "<PERSON> is late"
    assert one(redactor, "SENTINELNAMED Sam and SENTINELNAMEE Sam") == "<PERSON_2> and <PERSON_3>"


def test_token_shorter_than_two_characters_is_not_redacted(
    redactor: Redactor, directory: MemberDirectory
) -> None:
    assert one(redactor, "q") == "q"
    assert one(redactor, "Q and Wen") == "Q and <PERSON_2>"
    assert "q" not in directory.tokens


def test_full_name_with_short_part_is_redacted_whole(redactor: Redactor) -> None:
    assert one(redactor, "was that SENTINELNAMEW Wen Q?") == "was that <PERSON_2>?"


def test_no_sender_and_reactor_are_redacted_when_mentioned(redactor: Redactor) -> None:
    # Gwen never sends in these messages: she stands for a "no" sender or a reactor (D77).
    result = redactor.redact_thread(T1, [(PRIYA, "tell gwen, sentinelnameg")])
    assert result[0].text == "tell <PERSON_2>, <PERSON_2>"


def test_member_of_another_thread_is_redacted_and_numbered_by_name(redactor: Redactor) -> None:
    result = redactor.redact_thread(T1, [(PRIYA, "Hana saw hana and Priya")])
    assert result[0].text == "<PERSON_2> saw <PERSON_2> and <PERSON_1>"


def test_unknown_thread_still_redacts_everyone(redactor: Redactor) -> None:
    assert one(redactor, "Priya and Hana", thread="no-such-thread") == "<PERSON_2> and <PERSON_3>"


def test_media_only_messages_pass_through(redactor: Redactor) -> None:
    result = redactor.redact_thread(T1, [(PRIYA, None), (JOSE, ""), (PRIYA, "hi")])
    assert result[0].text is None
    assert result[1].text == ""
    assert result[0].sender_label == result[2].sender_label == "<PERSON_1>"
    assert all(v == 0 for v in result[0].counts.values())


def test_result_exposes_no_mapping(redactor: Redactor) -> None:
    result = redactor.redact_thread(T1, [(PRIYA, "Priya")])[0]
    assert [f.name for f in dataclasses.fields(result)] == [
        "sender_label",
        "text",
        "counts",
        "overlap_merges",
        "boundary_extensions",
        "backstop_adjacent",
        "backstop_standalone",
    ]
    counters = ("overlap_merges", "boundary_extensions", "backstop_adjacent", "backstop_standalone")
    assert all(type(getattr(result, name)) is int for name in counters)

    assert list(result.counts) == list(PLACEHOLDER_TYPES)
    assert "SENTINEL" not in repr(result).upper()
    assert not hasattr(redactor, "mapping")


# --- Deterministic PII -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "placeholder", "kind"),
    [
        ("call +1 415 867 5309 now", "<PHONE>", "phone"),
        ("call (415) 867-5309 now", "<PHONE>", "phone"),
        ("mail jane.roe99@gmail.com now", "<EMAIL>", "email"),
        ("see https://www.example.com/a/b?c=d now", "<URL>", "url"),
        ("see www.example.org now", "<URL>", "url"),
        ("follow @some_handle.1 now", "<HANDLE>", "handle"),
        ("I live at 123 Main Street, Apt 5 now", "<ADDRESS>", "address"),
        ("at 4021 oak ridge rd now", "<ADDRESS>", "address"),
        ("card 4111 1111 1111 1111 now", "<CARD>", "card"),
        ("ssn 536-90-4399 now", "<SSN>", "ssn"),
        ("ip 203.0.113.42 now", "<IP>", "ip"),
        ("iban GB82 WEST 1234 5698 7654 32 now", "<IBAN>", "iban"),
    ],
)
def test_deterministic_types_become_placeholders(
    redactor: Redactor, text: str, placeholder: str, kind: str
) -> None:
    result = redactor.redact_thread(T1, [(GWEN, text)])[0]
    assert placeholder in (result.text or "")
    assert result.counts[kind] == 1
    assert sum(result.counts.values()) == 1


def test_pii_inside_a_member_context_is_replaced_whole(redactor: Redactor) -> None:
    assert one(redactor, "@priya_x") == "<HANDLE>"
    assert one(redactor, "https://instagram.com/priya.sentinelnamea") == "<URL>"
    assert one(redactor, "priya@gmail.com") == "<EMAIL>"


def test_cities_and_organizations_are_kept(redactor: Redactor) -> None:
    text = "moving from Berkeley to Sacramento, Google offered me a job"
    assert one(redactor, text) == text


def test_ordinary_numbers_and_text_are_kept(redactor: Redactor) -> None:
    text = "I have 3 more dogs on the street and it costs 25 bucks lol"
    assert one(redactor, text) == text


def test_model_dependent_ner_redacts_a_clear_non_member_name(redactor: Redactor) -> None:
    out = one(redactor, "my cousin Daniela is visiting this weekend")
    assert "Daniela" not in out
    assert "<PERSON_" in out


def test_model_dependent_ner_name_merges_with_member_match(redactor: Redactor) -> None:
    """Gwen is a member; the surname is not. The whole name goes, not just the member part."""
    out = one(redactor, "I met Gwen Hollingsworth yesterday")
    assert "Hollingsworth" not in out


def test_model_dependent_same_ner_name_gets_one_number(redactor: Redactor) -> None:
    out = one(redactor, "Daniela said hi and then daniela left")
    assert out.count("<PERSON_2>") == 2


# --- Residual-token regressions (D79) ---------------------------------------------------------


def test_m1_member_span_partly_overlapping_a_longer_handle_is_not_dropped(
    redactor: Redactor, directory: MemberDirectory
) -> None:
    """The handle holds the member token "gwen", so it scores 1.0 and is longer than the full name
    "SENTINELNAMEA Priya" that starts inside it. The name's tail must not survive as a word."""
    out = one(redactor, "@gwen_xx_sentinelnamea priya")
    assert residual_member_tokens(out, directory) == 0
    assert out == "<HANDLE>"


def test_m1_member_span_partly_overlapping_a_url_or_email(
    redactor: Redactor, directory: MemberDirectory
) -> None:
    for text in (
        "see gwen.example.com/sentinelnamea priya ok",
        "gwen_xx_sentinelnamea@mail.com priya",
    ):
        assert residual_member_tokens(one(redactor, text), directory) == 0, text


def test_m1_other_score_one_span_partly_overlapping_a_member_span(
    redactor: Redactor, directory: MemberDirectory
) -> None:
    """A checksum-validated card or IBAN scores 1.0 in Presidio; it must not drop a member span
    it only partly covers either. Spans are forced because no natural text overlaps both."""
    text = "pay 4111111111111111 sentinelnamea priya"
    results = [
        RecognizerResult("CREDIT_CARD", 4, 24, 1.0),
        RecognizerResult("MEMBER_NAME", 21, 40, 1.0),
    ]
    chosen = redactor._choose(text, results)
    assert [(s.start, s.end, s.entity) for s in chosen.spans] == [(4, 40, "CREDIT_CARD")]
    assert chosen.merges == 1


@pytest.mark.parametrize(
    "text",
    [
        "@priya\u011fwen",  # handle stops at the non-ASCII letter; "gwen" is left over
        "@" + "a" * 29 + "gwen",  # handle capped at 30 characters; "gwen" is left over
        "@" + "a" * 29 + "gwen's",
        "@priya\u00f1sentinelnameb",
        "see https://example.com/\u00e9priya",
    ],
)
def test_m2_pattern_span_ending_inside_a_word_leaves_no_member_token(
    redactor: Redactor, directory: MemberDirectory, text: str
) -> None:
    out = one(redactor, text)
    assert residual_member_tokens(out, directory) == 0


# --- Counters and the backstop (D79) -----------------------------------------------------------


class _DropsMemberSpans(Redactor):
    """Test double for an unknown bug: every member span is lost before replacement."""

    def _choose(self, text: str, results: Sequence[RecognizerResult]) -> redaction._Chosen:
        chosen = super()._choose(text, results)
        kept = [s for s in chosen.spans if s.entity != "MEMBER_NAME"]
        return redaction._Chosen(kept, chosen.merges, chosen.extensions)


def test_counters_report_merges_and_extensions(redactor: Redactor) -> None:
    merged = redactor.redact_thread(T1, [(ZED, "@gwen_xx_sentinelnamea priya")])[0]
    assert merged.overlap_merges >= 1
    extended = redactor.redact_thread(T1, [(ZED, "@priya\u011fwen")])[0]
    assert extended.boundary_extensions >= 1
    for message in (merged, extended):
        assert message.backstop_adjacent == message.backstop_standalone == 0


def test_backstop_replaces_a_forced_residual_and_counts_it(
    nlp: Language, directory: MemberDirectory
) -> None:
    double = _DropsMemberSpans(directory, nlp)
    result = double.redact_thread(T1, [(ZED, "hi Priya and gwen!")])[0]
    assert result.text is not None
    assert residual_member_tokens(result.text, directory) == 0
    assert result.text == "hi <PERSON_2> and <PERSON_3>!"
    assert (result.backstop_adjacent, result.backstop_standalone) == (0, 2)
    assert result.counts["person"] == 2


def test_backstop_tells_adjacent_from_standalone(
    redactor: Redactor, directory: MemberDirectory
) -> None:
    pseudonyms = redaction._Pseudonyms()
    text, adjacent, standalone = redactor._backstop(
        "<HANDLE>priya and <PERSON_1> priya, gwen<URL>", frozenset(), pseudonyms
    )
    assert residual_member_tokens(text, directory) == 0
    assert (adjacent, standalone) == (2, 1)


def test_backstop_raises_when_tokens_survive_three_passes(
    nlp: Language, directory: MemberDirectory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(redaction, "_person_placeholder", lambda *_args: "priya")
    double = _DropsMemberSpans(directory, nlp)
    with pytest.raises(RedactionError) as excinfo:
        double.redact_thread(T1, [(ZED, "hi Priya")])
    assert "priya" not in str(excinfo.value).lower()


def test_backstop_never_matches_words_inside_placeholders(nlp: Language) -> None:
    """Names that equal placeholder words must not be found inside <PERSON_1>, <HANDLE>, <URL>..."""
    names = MemberDirectory([(T1, "Person Handle"), (T1, "Url Ip"), (T1, "Email Phone")])
    redactor = Redactor(names, nlp)
    out = one(redactor, "@some_one_else http://example.com 203.0.113.9 a@b.com 415-867-5309")
    assert out == "<HANDLE> <URL> <IP> <EMAIL> <PHONE>"
    text, adjacent, standalone = redactor._backstop(
        "<PERSON_1> <PERSON> <HANDLE> <URL> <IP> <EMAIL> <PHONE>",
        frozenset(),
        redaction._Pseudonyms(),
    )
    assert text == "<PERSON_1> <PERSON> <HANDLE> <URL> <IP> <EMAIL> <PHONE>"
    assert (adjacent, standalone) == (0, 0)
    assert redactor._backstop("a person here", frozenset(), redaction._Pseudonyms())[0] == (
        "a <PERSON_1> here"
    )


# --- Plumbing --------------------------------------------------------------------------------


def test_missing_model_raises_redaction_model_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redaction, "SPACY_MODEL", "en_core_web_not_installed")
    with pytest.raises(RedactionModelError):
        load_model()


def test_residual_member_tokens_counts_whole_word_tokens(directory: MemberDirectory) -> None:
    assert residual_member_tokens("hi Priya and JOSE, sentinelnameb", directory) == 3
    assert residual_member_tokens("hi <PERSON_1> and <PERSON>", directory) == 0
    assert residual_member_tokens("Priyanka q", directory) == 0
    assert residual_member_tokens("José and Jose", directory) == 2


def test_placeholder_types_match_the_spec() -> None:
    assert PLACEHOLDER_TYPES == (
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


# --- Property test ---------------------------------------------------------------------------

_LETTERS = st.text(
    alphabet=st.characters(categories=["L"], exclude_characters="İ"), min_size=2, max_size=8
)
_SEPARATORS = st.sampled_from([" ", ", ", " 🔥 ", "-", " O'"])
_FILLER = st.sampled_from(["hey", "so", "lol", "ok", "then", "yesterday", "and", "the", "to"])


_ASCII_WORD = st.text(alphabet=string.ascii_letters, min_size=2, max_size=8)


@st.composite
def decorated_names(draw: st.DrawFn) -> tuple[str, str]:
    """(display name, how someone types it plainly): the name as written, and the same words typed
    with ordinary letters. Decorations (D83): all or one token in enclosed letters, a "™" suffix, a
    "⑵" suffix. Undecorated names are their own plain form."""
    if draw(st.integers(0, 2)) == 0:
        words = draw(st.lists(_ASCII_WORD, min_size=1, max_size=3))
        seps = [draw(st.sampled_from([" ", ", ", "-"])) for _ in words[1:]]

        def join(parts: list[str]) -> str:
            return parts[0] + "".join(sep + w for sep, w in zip(seps, parts[1:], strict=True))

        plain = join(words)
        kind = draw(st.sampled_from(["enclosed_all", "enclosed_one", "tm", "paren2"]))
        if kind == "enclosed_all":
            return _enclosed(plain), plain
        if kind == "enclosed_one":
            k = draw(st.integers(0, len(words) - 1))
            return join([_enclosed(w) if i == k else w for i, w in enumerate(words)]), plain
        return plain + ("\u2122" if kind == "tm" else "\u2475"), plain
    words = draw(st.lists(_LETTERS, min_size=1, max_size=3))
    name = words[0]
    for word in words[1:]:
        name += draw(_SEPARATORS) + word
    name += draw(st.sampled_from(["", " 🔥", "!"]))
    return name, name


@st.composite
def display_names(draw: st.DrawFn) -> str:
    return draw(decorated_names())[0]


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _variant(draw: st.DrawFn, name: str) -> str:
    form = draw(st.sampled_from(["same", "lower", "upper", "stripped", "stripped_upper"]))
    if form == "lower":
        return name.lower()
    if form == "upper":
        return name.upper()
    if form == "stripped":
        return _strip_accents(name)
    if form == "stripped_upper":
        return _strip_accents(name).upper()
    return name


@st.composite
def phones(draw: st.DrawFn) -> str:
    area = draw(st.sampled_from(["212", "415", "510", "916", "617", "202", "305", "206", "718"]))
    exchange = draw(st.integers(2, 9)).__str__() + f"{draw(st.integers(10, 99))}"
    line = f"{draw(st.integers(1000, 9999))}"
    form = draw(st.sampled_from(["{a}-{b}-{c}", "({a}) {b}-{c}", "{a} {b} {c}", "+1 {a} {b} {c}"]))
    return form.format(a=area, b=exchange, c=line)


@st.composite
def emails(draw: st.DrawFn) -> str:
    parts = draw(
        st.lists(
            st.text(alphabet=string.ascii_lowercase + string.digits, min_size=1, max_size=5),
            min_size=1,
            max_size=3,
        )
    )
    local = draw(st.sampled_from([".", "_"])).join(parts)
    host = draw(st.text(alphabet=string.ascii_lowercase, min_size=3, max_size=10))
    tld = draw(st.sampled_from(["com", "org", "net", "edu"]))
    return f"{local}@{host}.{tld}"


@st.composite
def scenario(draw: st.DrawFn) -> tuple[list[str], str, str, str]:
    names = draw(st.lists(display_names(), min_size=1, max_size=4, unique=True))
    pieces: list[str] = []
    for _ in range(draw(st.integers(1, 6))):
        pieces.append(_variant(draw, draw(st.sampled_from(names))))
    phone, email = draw(phones()), draw(emails())
    pieces += [phone, email]
    filler = draw(st.lists(_FILLER, min_size=2, max_size=6))
    for word in filler:
        pieces.insert(draw(st.integers(0, len(pieces))), word)
    return names, " ".join(pieces), phone, email


@settings(
    max_examples=150,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.differing_executors],
)
@given(data=scenario())
def test_property_no_member_token_phone_or_email_survives(
    nlp: Language, data: tuple[list[str], str, str, str]
) -> None:
    names, text, phone, email = data
    directory = MemberDirectory([(T1, n) for n in names])
    out = Redactor(directory, nlp).redact_thread(T1, [(names[0], text)])[0]
    assert out.text is not None
    assert residual_member_tokens(out.text, directory) == 0
    assert out.backstop_adjacent == out.backstop_standalone == 0
    assert phone not in out.text
    assert email not in out.text
    # An NER span may swallow a number or address into <PERSON_n> instead of <PHONE>; either way
    # no digit run of the number and no part of the address is left.
    assert re.sub(r"\D", "", phone)[-7:] not in re.sub(r"\D", "", out.text)
    bare = redaction._PLACEHOLDER.sub(" ", out.text)  # "1@x.com" must not match "<PERSON_1>"
    assert email.split("@")[0] not in bare.split()


# --- Property test: names inside and next to patterns (D79) ---------------------------------------

# "™ ℠ ℡ ㎏" are not alphanumeric but fold to letters (D81): glue that used to hide a name.
_GLUE = st.sampled_from(["", " ", ",", "/", ".", "-", "\u2122", "\u2120", "\u2121", "\u338f"])
_NON_ASCII = st.sampled_from(["\u00f1", "\u00e9", "\u011f", "\u00fc", "\u0131"])


@st.composite
def pattern_contexts(draw: st.DrawFn, names: list[str]) -> tuple[str, list[str]]:
    """One piece of text that puts a member name token inside or beside a handle, URL or email.

    Returns the text and any extra member names it needs in the directory; `names` is not changed.
    """
    word = _variant(draw, draw(st.sampled_from(names)))
    tokens = [t for t in re.findall(r"[^\W_]+", word) if len(t) >= 2] or [word]
    token = str(draw(st.sampled_from(tokens)))
    ascii_token = _strip_accents(token)
    kind = draw(
        st.sampled_from(["handle", "long", "url", "email", "after", "unicode", "symbol", "overlap"])
    )
    if kind == "handle":
        return ("@" + ascii_token + draw(st.sampled_from(["", "_x", ".9", "_" + ascii_token])), [])
    if kind == "long":
        return ("@" + "a" * draw(st.integers(24, 40)) + ascii_token, [])
    if kind == "url":
        path = draw(
            st.sampled_from(["https://example.com/{}/p", "www.site.org/u/{}?x=1", "http://a.io/{}"])
        )
        return (path.format(ascii_token), [])
    if kind == "email":
        return (f"{ascii_token}.{draw(st.sampled_from(['x', 'y9']))}@mail.com", [])
    if kind == "after":
        before = draw(st.sampled_from(["@bob", "https://e.com/a", "x@y.org", "(415) 867-5309"]))
        return (str(before) + str(draw(_GLUE)) + token, [])
    if kind == "unicode":
        mark = draw(_NON_ASCII)
        pattern = draw(st.sampled_from(["{t}{m}", "{m}{t}", "@b{m}{t}", "@{t}{m}b", "{t}{m} {t}"]))
        return pattern.format(t=token, m=mark), []
    if kind == "symbol":
        symbol = draw(_GLUE.filter(bool))
        return draw(st.sampled_from(["{t}{g}", "{g}{t}", "x {t}{g}x", "{t}{g}{t}"])).format(
            t=token, g=symbol
        ), []
    first, second = draw(_LETTERS), draw(_LETTERS)  # a two-word name partly overlapping a handle
    return f"@pre_{draw(st.integers(10, 99))}_{first} {second}", [f"{first} {second}"]


@st.composite
def context_scenario(draw: st.DrawFn) -> tuple[list[str], str, str, str]:
    names = draw(st.lists(display_names(), min_size=1, max_size=3, unique=True))
    pieces: list[str] = []
    for _ in range(draw(st.integers(1, 3))):
        piece, extra = draw(pattern_contexts(names))
        pieces.append(piece)
        names = [*names, *extra]
    phone, email = draw(phones()), draw(emails())
    pieces += [phone, email]
    for word in draw(st.lists(_FILLER, min_size=1, max_size=4)):
        pieces.insert(draw(st.integers(0, len(pieces))), word)
    return names, " ".join(pieces), phone, email


@settings(
    max_examples=300,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.differing_executors],
)
@given(data=context_scenario())
def test_property_names_in_and_beside_patterns_need_no_backstop(
    nlp: Language, data: tuple[list[str], str, str, str]
) -> None:
    names, text, phone, email = data
    directory = MemberDirectory([(T1, n) for n in names])
    out = Redactor(directory, nlp).redact_thread(T1, [(names[0], text)])[0]
    assert out.text is not None
    assert residual_member_tokens(out.text, directory) == 0
    assert (
        out.backstop_adjacent == out.backstop_standalone == 0
    )  # a non-zero count: a fix is missing
    assert phone not in out.text
    assert email not in out.text
    assert re.sub(r"\D", "", phone)[-7:] not in re.sub(r"\D", "", out.text)


# --- One definition of a word (D81) ----------------------------------------------------------


@pytest.mark.parametrize("text", ["hi Priya™", "™priya", "Priya℠ and Jose㎏", "℡José"])
def test_d81_name_beside_a_symbol_that_folds_to_letters(
    redactor: Redactor, directory: MemberDirectory, text: str
) -> None:
    assert residual_member_tokens(text, directory) >= 1  # the audit counts it before redaction
    out = redactor.redact_thread(T1, [(ZED, text)])[0]
    assert out.text is not None
    assert residual_member_tokens(out.text, directory) == 0
    assert "priya" not in unicodedata.normalize("NFKD", out.text).casefold().replace("™", "")
    assert out.backstop_adjacent == out.backstop_standalone == 0


@pytest.mark.parametrize(
    "text",
    [
        "\uff30\uff52\uff49\uff59\uff41",  # full-width "Priya"
        "hi \uff30\uff32\uff29\uff39\uff21!",
        "\uff2a\uff4f\uff53\u00e9",
    ],
)
def test_d81_full_width_letters_are_part_of_the_word(
    redactor: Redactor, directory: MemberDirectory, text: str
) -> None:
    assert residual_member_tokens(text, directory) >= 1
    out = one(redactor, text)
    assert residual_member_tokens(out, directory) == 0
    assert "<PERSON_" in out


def test_d81_ligature_inside_a_name_stays_one_word(nlp: Language) -> None:
    names = MemberDirectory([(T1, "Fiona"), (T1, "Zoë Fisher")])
    assert residual_member_tokens("ﬁona and ﬁona™", names) == 2
    redactor = Redactor(names, nlp)
    out = one(redactor, "hi ﬁona and Zoe ﬁsher and ﬁona™")
    assert residual_member_tokens(out, names) == 0
    assert "ﬁ" not in out


def test_d81_word_without_a_break_is_still_one_word(directory: MemberDirectory) -> None:
    """The documented gap: a name glued to letters or digits is one word and is not matched."""
    assert directory.find("priya123 xpriya") == []


def _symbol_breaks() -> list[str]:
    """BMP characters that break a word under D81 but fold to something with a letter or digit."""
    out = []
    for code in range(0x10000):
        ch = chr(code)
        if ch.isalnum() or unicodedata.category(ch).startswith(("M", "C")):
            continue
        folded = "".join(
            c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)
        ).casefold()
        if any(c.isalnum() for c in folded):
            out.append(ch)
    return out


def test_d81_every_word_break_that_folds_to_alphanumerics(nlp: Language) -> None:
    breaks = _symbol_breaks()
    assert "™" in breaks and "℠" in breaks and "㎏" in breaks  # the list is not empty
    names = MemberDirectory([(T1, "SENTINELNAMEA Priya")])
    redactor = Redactor(names, nlp)
    texts = [f"priya{ch}" for ch in breaks] + [f"{ch}priya" for ch in breaks]
    for text in texts:
        assert len(names.find(text)) == 1, repr(text)
    outs = redactor.redact_thread(T1, [(ZED, t) for t in texts])
    for text, out in zip(texts, outs, strict=True):
        assert out.text is not None
        assert residual_member_tokens(out.text, names) == 0, repr(text)
        assert "<PERSON" in out.text, repr(text)
        assert out.backstop_adjacent == out.backstop_standalone == 0, repr(text)


# --- Two segmentations, used as a union (D83) -------------------------------------------------


def _enclosed(text: str) -> str:
    """ASCII letters as enclosed letters (U+24B6-24CF / U+24D0-24E9): symbols, not alphanumerics."""
    return "".join(
        chr(0x24B6 + ord(c) - 65)
        if "A" <= c <= "Z"
        else chr(0x24D0 + ord(c) - 97)
        if "a" <= c <= "z"
        else c
        for c in text
    )


def _redact(
    nlp: Language, names: list[str], text: str
) -> tuple[MemberDirectory, redaction.RedactedMessage]:
    directory = MemberDirectory([(T1, n) for n in names])
    out = Redactor(directory, nlp).redact_thread(T1, [(ZED, text)])[0]
    assert out.text is not None
    return directory, out


def test_d83_enclosed_letter_name_is_matched_by_a_plain_mention(nlp: Language) -> None:
    name = _enclosed("SENTINELNAMEJ")
    directory, out = _redact(nlp, [name], "hey sentinelnamej and SENTINELNAMEJ")
    assert out.text is not None and "sentinelnamej" not in out.text.casefold()
    assert residual_member_tokens(out.text, directory) == 0
    assert out.backstop_adjacent == out.backstop_standalone == 0


def test_d83_name_ending_in_a_raw_alphanumeric_symbol_matches_a_plain_mention(
    nlp: Language,
) -> None:
    _, out = _redact(nlp, ["SENTINELNAMEK⑵"], "hi sentinelnamek there")
    assert out.text is not None and "sentinelnamek" not in out.text.casefold()
    assert out.backstop_adjacent == out.backstop_standalone == 0


def test_d83_name_followed_by_a_symbol_that_is_alphanumeric_but_folds_to_a_break(
    nlp: Language,
) -> None:
    directory, out = _redact(nlp, ["SENTINELNAMEL"], "ping SENTINELNAMEL⑴ now")
    assert out.text is not None and "SENTINELNAMEL" not in out.text
    assert residual_member_tokens(out.text, directory) == 0
    assert out.backstop_adjacent == out.backstop_standalone == 0
    assert "⑴" not in out.text  # the whole raw word is replaced, no fragment is left


def test_d83_mention_written_in_enclosed_letters_is_redacted(nlp: Language) -> None:
    directory, out = _redact(nlp, ["SENTINELNAMEM"], f"hi {_enclosed('sentinelnamem')} ok")
    assert out.text is not None and "ⓢ" not in out.text
    assert residual_member_tokens(out.text, directory) == 0
    assert out.backstop_adjacent == out.backstop_standalone == 0


@settings(
    max_examples=200,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.differing_executors],
)
@given(
    pairs=st.lists(decorated_names(), min_size=1, max_size=3, unique_by=lambda p: p[0]),
    glue=st.sampled_from([" ", ", ", " - ", "\u2122 ", " \u2475 "]),
    form=st.sampled_from(["same", "lower", "upper"]),
)
def test_d83_property_plain_mentions_of_decorated_names_need_no_backstop(
    nlp: Language, pairs: list[tuple[str, str]], glue: str, form: str
) -> None:
    names = [name for name, _ in pairs]
    mentions = [
        plain.lower() if form == "lower" else plain.upper() if form == "upper" else plain
        for _, plain in pairs
    ]
    text = "hey " + glue.join(mentions) + " ok"
    directory, out = _redact(nlp, names, text)
    assert out.text is not None
    assert residual_member_tokens(out.text, directory) == 0
    assert out.backstop_adjacent == out.backstop_standalone == 0
    # every plain mention was found: none of its words is left in the output
    bare = redaction._PLACEHOLDER.sub(" ", out.text).casefold()
    for _, plain in pairs:
        for word in re.findall(r"[A-Za-z]{2,}", plain):
            assert word.casefold() not in re.findall(r"[a-z]+", bare), (word, out.text)


def _mirror_symbol_breaks() -> list[str]:
    """BMP characters that are alphanumeric on their own (so a raw word keeps them) but whose fold
    contains a character that is not alphanumeric: "⑴" folds to "(1)", "⒈" to "1.", "½" to "1", a
    fraction slash and "2"."""
    out = []
    for code in range(0x10000):
        ch = chr(code)
        if ch.isalnum() and not all(c.isalnum() for c in redaction._fold_char(ch)):
            out.append(ch)
    return out


def test_d83_mirror_sweep_name_beside_an_alphanumeric_that_folds_to_a_break(
    nlp: Language,
) -> None:
    """Both sides of every such character. A name is told apart from its neighbour only on a side
    where the fold starts (after the name) or ends (before it) with a non-alphanumeric. "priya" +
    one-half folds to "priya1" plus a slash plus "2": a name glued to a digit, the documented gap
    (D81, D83)."""
    breaks = _mirror_symbol_breaks()
    assert {"\u2474", "\u2488", "\u00bd"} <= set(breaks)  # the sweep is not empty
    names = MemberDirectory([(T1, "SENTINELNAMEA Priya")])
    redactor = Redactor(names, nlp)
    texts = []
    for ch in breaks:
        fold = redaction._fold_char(ch)
        if not fold[0].isalnum():
            texts.append(f"priya{ch}")
        if not fold[-1].isalnum():
            texts.append(f"{ch}priya")
    assert len(texts) > 100
    for text in texts:
        assert len(names.find(text)) == 1, repr(text)
    outs = redactor.redact_thread(T1, [(ZED, t) for t in texts])
    for text, out in zip(texts, outs, strict=True):
        assert out.text is not None
        assert residual_member_tokens(out.text, names) == 0, repr(text)
        assert "<PERSON" in out.text and "riya" not in out.text, repr(text)
        assert out.backstop_adjacent == out.backstop_standalone == 0, repr(text)


# --- Coverage never drops against the pre-D81 matcher (D83) ------------------------------------

_SYMBOL_GLUE = st.sampled_from(
    ["", " ", "\u2122", "\u2120", "\u338f", "\u2474", "\u2488", "\u00bd", "\u24d9", ", "]
)


@st.composite
def reference_texts(draw: st.DrawFn, pairs: list[tuple[str, str]]) -> str:
    pieces = []
    for _ in range(draw(st.integers(1, 6))):
        name, plain = draw(st.sampled_from(pairs))
        shown = draw(st.sampled_from([name, plain, _enclosed(plain), plain.upper(), "x" + plain]))
        pieces.append(shown + draw(_SYMBOL_GLUE))
    return draw(_SYMBOL_GLUE).join(pieces)


@settings(max_examples=500, deadline=None, derandomize=True)
@given(
    pairs=st.lists(decorated_names(), min_size=1, max_size=4, unique_by=lambda p: p[0]),
    data=st.data(),
)
def test_d83_every_character_the_reference_matcher_covered_is_still_covered(
    pairs: list[tuple[str, str]], data: st.DataObject
) -> None:
    members = [(T1, name) for name, _ in pairs]
    text = data.draw(reference_texts(pairs))
    reference = ReferenceDirectory(members).find(text)
    now = MemberDirectory(members).find(text)
    covered = {i for m in now for i in range(m.start, m.end)}
    for ref in reference:
        assert set(range(ref.start, ref.end)) <= covered, (text, members)
    starts = [m.start for m in now]
    assert starts == sorted(starts)
    assert all(a.end <= b.start for a, b in itertools.pairwise(now))  # non-overlapping


def test_d83_src_never_imports_the_frozen_reference() -> None:
    root = Path(__file__).resolve().parent.parent / "src"
    for path in root.rglob("*.py"):
        assert "reference_redaction" not in path.read_text(encoding="utf-8"), path


# --- Two names glued by a character that is alphanumeric raw but folds to a break --------------


class _RecordsSpans(Redactor):
    """Keeps the spans _replace receives, to check they never overlap."""

    def __init__(self, directory: MemberDirectory, nlp: Language) -> None:
        super().__init__(directory, nlp)
        self.handed: list[list[tuple[int, int]]] = []

    def _replace(  # type: ignore[override]
        self,
        text: str,
        spans: Sequence[redaction._Span],
        members: frozenset[str],
        pseudonyms: redaction._Pseudonyms,
    ) -> tuple[str, dict[str, int]]:
        self.handed.append([(s.start, s.end) for s in spans])
        return Redactor._replace(text, spans, members, pseudonyms)


def _check_glued(
    nlp: Language, glues: Sequence[str], a: str = "SENTINELNAMEX", b: str = "SENTINELNAMEY"
) -> None:
    names = MemberDirectory([(T1, a), (T1, b)])
    redactor = _RecordsSpans(names, nlp)
    texts = [f"{a}{g}{b}" for g in glues]
    outs = redactor.redact_thread(T1, [(ZED, t) for t in texts])  # no exception
    assert len(redactor.handed) == 1 or len(redactor.handed) == len(texts)
    for text, out, spans in zip(texts, outs, redactor.handed, strict=True):
        assert out.text is not None
        assert "<PERSON" in out.text, repr(text)
        assert "sentinelname" not in out.text.casefold(), repr(text)
        assert residual_member_tokens(out.text, names) == 0, repr(text)
        assert out.backstop_adjacent == out.backstop_standalone == 0, repr(text)
        ordered = sorted(spans)
        assert all(x[1] <= y[0] for x, y in itertools.pairwise(ordered)), (repr(text), spans)


@pytest.mark.parametrize("glue", ["⑴", "⒈", "™"])
def test_two_names_glued_by_a_symbol_are_both_redacted(nlp: Language, glue: str) -> None:
    """⑴ and ⒈ are alphanumeric raw and fold to a break; ™ is a break both ways (the guard)."""
    _check_glued(nlp, [glue])


def test_two_names_glued_by_every_alphanumeric_that_folds_to_a_break(nlp: Language) -> None:
    glues = []
    for ch in _mirror_symbol_breaks():
        fold = redaction._fold_char(ch)
        if not fold[0].isalnum() and not fold[-1].isalnum():
            glues.append(ch)
    # "⒈" folds to "1." (starts alphanumeric), so only the parametrized test covers it
    assert "⑴" in glues and "⒈" not in glues and len(glues) > 30
    _check_glued(nlp, glues)
