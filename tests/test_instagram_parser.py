import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gcwrapped.parsers import instagram_format as fmt
from gcwrapped.parsers.instagram import (
    InstagramParser,
    ParseError,
    ParseStats,
    fix_mojibake,
    message_key,
    parse_export,
    parse_message,
)
from synthetic.instagram import THREADS, generate_pair, to_mojibake

IDENTICAL_TEXT = "same message 👍"


@pytest.fixture
def exports(tmp_path: Path) -> tuple[Path, Path]:
    return generate_pair(tmp_path)


def keys(*roots: Path) -> list[str]:
    return [m.message_key for m in parse_export(roots)]


def one(raw: dict[str, object], occurrence: int = 0) -> object:
    return parse_message(
        raw, thread_id="t", thread_title=None, source_file="f", occurrence=occurrence
    )


# --- mojibake -------------------------------------------------------------------------------


@pytest.mark.parametrize("clean", ["café", "😂", "plain ascii", "José ☕ naïve", "", "❤️"])
def test_mojibake_round_trip(clean: str) -> None:
    assert fix_mojibake(to_mojibake(clean)) == clean


@pytest.mark.parametrize("clean", ["café", "😂", "plain ascii", "日本語", ""])
def test_already_clean_strings_pass_through(clean: str) -> None:
    assert fix_mojibake(clean) == clean


def test_mojibake_is_fixed_before_hashing() -> None:
    kwargs = dict(thread_id="t", timestamp_ms=1, attachment_uris=[], share_url=None, occurrence=0)
    raw = {fmt.KEY_SENDER: to_mojibake("José"), fmt.KEY_TIMESTAMP_MS: 1}
    msg = parse_message(raw, thread_id="t", thread_title=None, source_file="f")
    assert msg.sender_name == "José"
    assert msg.message_key == message_key(sender_name="José", content=None, **kwargs)  # type: ignore[arg-type]


# --- field mapping --------------------------------------------------------------------------


def test_timestamp_to_utc() -> None:
    raw = {fmt.KEY_SENDER: "a", fmt.KEY_TIMESTAMP_MS: 1_704_067_200_123}
    msg = parse_message(raw, thread_id="t", thread_title=None, source_file="f")
    assert msg.sent_at == datetime(2024, 1, 1, 0, 0, 0, 123_000, tzinfo=UTC)
    assert msg.sent_at.utcoffset() == UTC.utcoffset(None)


def test_full_field_mapping() -> None:
    raw = {
        fmt.KEY_SENDER: to_mojibake("Zoë"),
        fmt.KEY_TIMESTAMP_MS: 5,
        fmt.KEY_CONTENT: to_mojibake("café ☕"),
        fmt.KEY_PHOTOS: [{fmt.KEY_URI: "p/1.jpg"}, {fmt.KEY_URI: "p/2.jpg"}],
        fmt.KEY_VIDEOS: [{fmt.KEY_URI: "v/1.mp4"}],
        fmt.KEY_SHARE: {fmt.KEY_SHARE_LINK: "https://example.com/x"},
        fmt.KEY_REACTIONS: [
            {fmt.KEY_REACTION_EMOJI: to_mojibake("😂"), fmt.KEY_REACTION_ACTOR: to_mojibake("José")}
        ],
    }
    msg = parse_message(raw, thread_id="thr", thread_title="Title", source_file="a/message_1.json")
    assert msg.platform == "instagram"
    assert msg.thread_id == "thr"
    assert msg.thread_title == "Title"
    assert msg.sender_name == "Zoë"
    assert msg.content == "café ☕"
    assert msg.media_count == 3
    assert msg.share_url == "https://example.com/x"
    assert [(r.actor, r.emoji) for r in msg.reactions] == [("José", "😂")]
    assert msg.source_file == "a/message_1.json"
    assert len(msg.message_key) == 64


def test_missing_optional_fields_do_not_crash() -> None:
    msg = parse_message(
        {fmt.KEY_SENDER: "a", fmt.KEY_TIMESTAMP_MS: 1},
        thread_id="t",
        thread_title=None,
        source_file="f",
    )
    assert msg.content is None
    assert msg.media_count == 0
    assert msg.share_url is None
    assert msg.reactions == []


def test_null_and_odd_optional_fields_do_not_crash() -> None:
    raw = {
        fmt.KEY_SENDER: "a",
        fmt.KEY_TIMESTAMP_MS: 1,
        fmt.KEY_CONTENT: None,
        fmt.KEY_PHOTOS: None,
        fmt.KEY_SHARE: {},
        fmt.KEY_REACTIONS: [{"unexpected": 1}, "junk"],
    }
    msg = parse_message(raw, thread_id="t", thread_title=None, source_file="f")
    assert msg.content is None
    assert msg.share_url is None
    assert msg.reactions == []


@pytest.mark.parametrize("missing", [fmt.KEY_SENDER, fmt.KEY_TIMESTAMP_MS])
def test_missing_core_field_raises_without_values(missing: str) -> None:
    raw: dict[str, object] = {fmt.KEY_SENDER: "SECRET_NAME", fmt.KEY_TIMESTAMP_MS: 1}
    del raw[missing]
    with pytest.raises(ParseError) as exc:
        one(raw)
    assert missing in str(exc.value)
    assert "SECRET_NAME" not in str(exc.value)


# --- keys -----------------------------------------------------------------------------------


def test_occurrence_distinguishes_identical_messages() -> None:
    raw = {fmt.KEY_SENDER: "a", fmt.KEY_TIMESTAMP_MS: 1, fmt.KEY_CONTENT: "x"}
    k0 = parse_message(raw, thread_id="t", thread_title=None, source_file="f", occurrence=0)
    k1 = parse_message(raw, thread_id="t", thread_title=None, source_file="f", occurrence=1)
    assert k0.message_key != k1.message_key


def test_share_url_is_part_of_the_key() -> None:
    base = {fmt.KEY_SENDER: "a", fmt.KEY_TIMESTAMP_MS: 1, fmt.KEY_CONTENT: "x"}
    shared = {**base, fmt.KEY_SHARE: {fmt.KEY_SHARE_LINK: "https://example.com/x"}}
    other = {**base, fmt.KEY_SHARE: {fmt.KEY_SHARE_LINK: "https://example.com/y"}}
    keys_ = {
        parse_message(r, thread_id="t", thread_title=None, source_file="f").message_key
        for r in (base, shared, other)
    }
    assert len(keys_) == 3


def test_key_ignores_reactions_and_source_file() -> None:
    base = {fmt.KEY_SENDER: "a", fmt.KEY_TIMESTAMP_MS: 1, fmt.KEY_CONTENT: "x"}
    reacted = {
        **base,
        fmt.KEY_REACTIONS: [{fmt.KEY_REACTION_EMOJI: "👍", fmt.KEY_REACTION_ACTOR: "b"}],
    }
    a = parse_message(base, thread_id="t", thread_title=None, source_file="f1")
    b = parse_message(reacted, thread_id="t", thread_title=None, source_file="f2")
    assert a.message_key == b.message_key


def test_identical_messages_in_export_get_distinct_keys(exports: tuple[Path, Path]) -> None:
    twins = [m for m in parse_export([exports[0]]) if m.content == IDENTICAL_TEXT]
    assert len(twins) == 2 * len(THREADS)  # one identical pair per thread
    for thread_id in {m.thread_id for m in twins}:
        pair = [m for m in twins if m.thread_id == thread_id]
        assert len(pair) == 2
        assert pair[0].message_key != pair[1].message_key
        assert pair[0].sent_at == pair[1].sent_at


def test_keys_unique_within_each_export(exports: tuple[Path, Path]) -> None:
    for root in exports:
        ks = keys(root)
        assert len(ks) == len(set(ks))


def test_exports_have_different_chunking(exports: tuple[Path, Path]) -> None:
    thread = THREADS[0][0]
    n_a = len(fmt.message_files(exports[0] / fmt.MESSAGES_DIR / thread))
    n_b = len(fmt.message_files(exports[1] / fmt.MESSAGES_DIR / thread))
    assert n_a > 1
    assert n_b > 1
    assert n_a != n_b


def test_keys_stable_across_overlapping_exports(exports: tuple[Path, Path]) -> None:
    a, b = exports
    keys_a, keys_b = set(keys(a)), set(keys(b))
    overlap = keys_a & keys_b
    assert overlap  # the exports really do overlap
    assert keys_a - keys_b  # ...and each has messages the other lacks
    assert keys_b - keys_a

    stats = ParseStats()
    union = [m.message_key for m in parse_export([a, b], stats)]
    assert len(union) == len(set(union)) == len(keys_a | keys_b)
    assert stats.duplicates_dropped == len(overlap)
    assert stats.messages == len(union)
    assert len(stats.thread_ids) == len(THREADS)


def test_identical_pair_keys_match_across_exports(exports: tuple[Path, Path]) -> None:
    a, b = exports
    twins_a = {m.message_key for m in parse_export([a]) if m.content == IDENTICAL_TEXT}
    twins_b = {m.message_key for m in parse_export([b]) if m.content == IDENTICAL_TEXT}
    assert len(twins_a) == 2 * len(THREADS)
    assert twins_a == twins_b


def test_reparse_yields_identical_keys(exports: tuple[Path, Path]) -> None:
    assert keys(*exports) == keys(*exports)


def test_export_contents(exports: tuple[Path, Path]) -> None:
    msgs = list(parse_export([exports[0]]))
    assert any(m.media_count > 0 for m in msgs)
    assert any(m.share_url for m in msgs)
    assert any(m.reactions for m in msgs)
    assert any(m.content == "" for m in msgs)
    assert any(m.content is None for m in msgs)
    assert any("☕" in (m.content or "") or "😂" in (m.content or "") for m in msgs)
    assert any(m.thread_title == "Weekend Crew ☕" for m in msgs)
    assert all(m.source_file.startswith(fmt.MESSAGES_DIR.as_posix()) for m in msgs)
    assert all("\\" not in m.source_file for m in msgs)


def test_parser_protocol_adapter(exports: tuple[Path, Path]) -> None:
    assert [m.message_key for m in InstagramParser().parse(exports[0])] == keys(exports[0])


def test_empty_root_yields_nothing(tmp_path: Path) -> None:
    assert list(parse_export([tmp_path])) == []


# --- NUL bytes ------------------------------------------------------------------------------

NUL = chr(0)  # Postgres text cannot store it; built with chr() so no source tool mangles it


def test_nul_bytes_are_stripped_from_every_text_field_and_not_hashed() -> None:
    def raw(nul: str) -> dict[str, object]:
        return {
            fmt.KEY_SENDER: to_mojibake(f"Jo{nul}sé"),
            fmt.KEY_TIMESTAMP_MS: 1_700_000_000_000,
            fmt.KEY_CONTENT: to_mojibake(f"ca{nul}fé ☕{nul}"),
            fmt.KEY_PHOTOS: [{fmt.KEY_URI: f"photos/a{nul}.jpg"}],
            fmt.KEY_SHARE: {fmt.KEY_SHARE_LINK: f"https://x.example/p{nul}/"},
            fmt.KEY_REACTIONS: [
                {
                    fmt.KEY_REACTION_EMOJI: to_mojibake(f"😂{nul}"),
                    fmt.KEY_REACTION_ACTOR: to_mojibake(f"Zo{nul}ë"),
                }
            ],
        }

    dirty = parse_message(raw(NUL), thread_id="t", thread_title=None, source_file="f")
    clean = parse_message(raw(""), thread_id="t", thread_title=None, source_file="f")

    assert dirty.sender_name == "José"
    assert dirty.content == "café ☕"
    assert dirty.share_url == "https://x.example/p/"
    assert [(r.actor, r.emoji) for r in dirty.reactions] == [("Zoë", "😂")]
    assert dirty.message_key == clean.message_key
    assert NUL not in dirty.model_dump_json()
    assert NUL not in str(dirty.model_dump())


def test_nul_bytes_are_stripped_from_the_thread_title(tmp_path: Path) -> None:
    thread = tmp_path / fmt.MESSAGES_DIR / "t_1"
    thread.mkdir(parents=True)
    doc = {
        fmt.KEY_PARTICIPANTS: [],
        fmt.KEY_THREAD_TITLE: f"Cr{NUL}ew",
        fmt.KEY_MESSAGES: [{fmt.KEY_SENDER: "a", fmt.KEY_TIMESTAMP_MS: 1}],
    }
    (thread / "message_1.json").write_text(json.dumps(doc), encoding="utf-8")

    [msg] = parse_export([tmp_path])

    assert msg.thread_title == "Crew"
