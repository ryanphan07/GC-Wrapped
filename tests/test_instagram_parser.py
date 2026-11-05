import json
import random
import re
import shutil
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
from synthetic import sentinel
from synthetic.instagram import RENAMED_IN_B, THREADS, generate_pair, to_mojibake

IDENTICAL_TEXT = "same message 👍"


@pytest.fixture
def exports(tmp_path: Path) -> tuple[Path, Path]:
    return generate_pair(tmp_path, max_per_file=30)  # small chunks: several files per thread


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
    kwargs = dict(
        thread_id="t", timestamp_ms=1, attachment_basenames=[], share_url=None, occurrence=0
    )
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
    assert all(re.fullmatch(r"\d+/message_\d+\.json", m.source_file) for m in msgs)


def test_source_file_identical_for_renamed_folder(tmp_path: Path) -> None:
    _, b = generate_pair(tmp_path)
    before = sorted(m.source_file for m in parse_export([b]))
    folder = next(d for d in (b / fmt.MESSAGES_DIR).iterdir() if d.name != d.name.split("_")[-1])
    folder.rename(folder.with_name("totally_different_name_" + fmt.thread_id(folder)))
    after = sorted(m.source_file for m in parse_export([b]))
    assert before == after


def test_sentinel_source_file_has_no_name(tmp_path: Path) -> None:
    sentinel.build(tmp_path)
    for d in fmt.find_thread_dirs(tmp_path):  # drop the deliberately unparseable folder shape
        try:
            fmt.thread_id(d)
        except ValueError:
            shutil.rmtree(d)
    msgs = list(parse_export([tmp_path]))
    assert msgs
    assert all("SENTINELNAME" not in m.source_file for m in msgs)


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


# --- lone surrogates ------------------------------------------------------------------------

LONE = chr(0xD83D)  # half of a UTF-16 pair; json.loads yields it for a bare "\ud83d" escape


def test_lone_surrogates_become_replacement_chars_in_every_text_field() -> None:
    raw = {
        fmt.KEY_SENDER: f"Jo{LONE}",
        fmt.KEY_TIMESTAMP_MS: 1_700_000_000_000,
        fmt.KEY_CONTENT: f"hi {LONE} there {chr(0xDE00)}",
        fmt.KEY_SHARE: {fmt.KEY_SHARE_LINK: f"https://x.example/{LONE}"},
        fmt.KEY_REACTIONS: [
            {fmt.KEY_REACTION_EMOJI: LONE, fmt.KEY_REACTION_ACTOR: f"Zo{LONE}"},
        ],
    }

    msg = parse_message(raw, thread_id="t", thread_title=None, source_file="f")

    assert msg.sender_name == "Jo�"
    assert msg.content == "hi � there �"
    assert msg.share_url == "https://x.example/�"
    assert [(r.actor, r.emoji) for r in msg.reactions] == [("Zo�", "�")]
    for text in (msg.sender_name, msg.content or ""):
        text.encode("utf-8")  # raises UnicodeEncodeError if any surrogate survived


def test_message_key_is_computed_from_the_replaced_text() -> None:
    def key(content: str) -> str:
        raw = {fmt.KEY_SENDER: "a", fmt.KEY_TIMESTAMP_MS: 1, fmt.KEY_CONTENT: content}
        return parse_message(raw, thread_id="t", thread_title=None, source_file="f").message_key

    assert key(f"x{LONE}y") == key("x�y")


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


# --- thread_id ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("weekendcrew_1784563920183746", "1784563920183746"),
        ("48271936502817364", "48271936502817364"),
        ("abc12_3456", "3456"),  # digits inside the name are not the id
        ("two_words_and_more_123456789012345", "123456789012345"),
        ("_77", "77"),
    ],
)
def test_thread_id_is_the_trailing_digit_run(folder: str, expected: str) -> None:
    assert fmt.thread_id(Path("inbox") / folder) == expected


@pytest.mark.parametrize("folder", ["SENTINELFOLDER", "abc12", "name_12x", "name_", "12_ab", ""])
def test_thread_id_rejects_other_shapes_without_naming_the_folder(folder: str) -> None:
    with pytest.raises(ValueError, match=r"<name>_<digits> or <digits>") as exc:
        fmt.thread_id(Path("inbox") / folder)
    if folder:
        assert folder not in str(exc.value)


def test_synthetic_folders_match_the_real_shapes() -> None:
    shapes = {"name_digits": 0, "digits": 0}
    for folder, *_ in THREADS:
        digits = fmt.thread_id(Path(folder))
        assert 15 <= len(digits) <= 17
        assert folder == digits or folder.endswith(f"_{digits}")
        shapes["digits" if folder == digits else "name_digits"] += 1
    assert all(shapes.values())  # both shapes are present


# --- attachment identity --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("your_instagram_activity/messages/inbox/a_1/photos/img_1.jpg", "img_1.jpg"),
        ("your_instagram_activity/messages/inbox/a_1/photos/extensionless", "extensionless"),
        ("https://cdn.example/v/clip.mp4?oh=1&oe=2", None),
        ("other_root/photos/x.jpg", None),
    ],
)
def test_key_basename(uri: str, expected: str | None) -> None:
    assert fmt.key_basename(uri) == expected


def _msg_with_videos(*uris: str) -> dict[str, object]:
    return {
        fmt.KEY_SENDER: "a",
        fmt.KEY_TIMESTAMP_MS: 1,
        fmt.KEY_VIDEOS: [{fmt.KEY_URI: u} for u in uris],
    }


def test_external_attachments_count_toward_media_count_but_not_the_key() -> None:
    inside = "your_instagram_activity/messages/inbox/t_1/videos/v.mp4"
    outside = "https://cdn.example/v/clip.mp4?oh=abc"
    both = parse_message(
        _msg_with_videos(inside, outside), thread_id="t", thread_title=None, source_file="f"
    )
    only_inside = parse_message(
        _msg_with_videos(inside), thread_id="t", thread_title=None, source_file="f"
    )
    only_outside = parse_message(
        _msg_with_videos(outside), thread_id="t", thread_title=None, source_file="f"
    )
    assert (both.media_count, only_inside.media_count, only_outside.media_count) == (2, 1, 1)
    assert both.message_key == only_inside.message_key
    assert only_outside.message_key != only_inside.message_key


def test_identical_messages_differing_only_in_external_attachment_keep_both_rows(
    tmp_path: Path,
) -> None:
    thread = tmp_path / fmt.MESSAGES_DIR / "t_1"
    thread.mkdir(parents=True)
    msgs = [_msg_with_videos(f"https://cdn.example/{n}.mp4") for n in ("x", "y")]
    doc = {fmt.KEY_MESSAGES: msgs, fmt.KEY_THREAD_TITLE: "t"}
    (thread / "message_1.json").write_text(json.dumps(doc), encoding="utf-8")

    assert len(keys(tmp_path)) == len(set(keys(tmp_path))) == 2


# --- keys survive renames, new roots, query strings and reordering --------------------------


def clone(
    src: Path,
    dst: Path,
    *,
    rename: dict[str, str] | None = None,
    replace: dict[str, str] | None = None,
    shuffle_seed: int | None = None,
) -> Path:
    """Copy an export, renaming thread folders (and the folder text in files) and/or rewriting
    text. With `shuffle_seed`, shuffles the messages in every file."""
    shutil.copytree(src, dst)
    for old, new in (rename or {}).items():
        (dst / fmt.MESSAGES_DIR / old).rename(dst / fmt.MESSAGES_DIR / new)
    swaps = {**(rename or {}), **(replace or {})}
    for path in dst.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        for old, new in swaps.items():
            text = text.replace(old, new)
        if shuffle_seed is not None:
            doc = json.loads(text)
            random.Random(shuffle_seed).shuffle(doc[fmt.KEY_MESSAGES])
            text = json.dumps(doc)
        path.write_text(text, encoding="utf-8")
    return dst


def test_renamed_thread_folder_gives_identical_keys(
    exports: tuple[Path, Path], tmp_path: Path
) -> None:
    old = THREADS[0][0]
    new = "renamedcrew_" + fmt.thread_id(Path(old))
    renamed = clone(exports[0], tmp_path / "renamed", rename={old: new})
    assert (renamed / fmt.MESSAGES_DIR / new).is_dir()

    assert sorted(keys(renamed)) == sorted(keys(exports[0]))  # folder order changes with names
    assert {m.thread_id for m in parse_export([renamed])} == {
        m.thread_id for m in parse_export([exports[0]])
    }


def test_different_export_root_gives_identical_keys(
    exports: tuple[Path, Path], tmp_path: Path
) -> None:
    moved = clone(exports[0], tmp_path / "instagram-someone-2030-01-01-xyz")
    assert keys(moved) == keys(exports[0])


def test_external_uri_with_different_query_string_gives_identical_keys(
    exports: tuple[Path, Path], tmp_path: Path
) -> None:
    text = "".join(p.read_text(encoding="utf-8") for p in exports[0].rglob("*.json"))
    assert "oh=aaaa1111" in text  # the export really has external URLs to rewrite
    changed = clone(exports[0], tmp_path / "q", replace={"oh=aaaa1111": "oh=different"})
    assert "oh=aaaa1111" not in "".join(
        p.read_text(encoding="utf-8") for p in changed.rglob("*.json")
    )
    assert keys(changed) == keys(exports[0])


def test_shuffled_message_order_gives_identical_keys(
    exports: tuple[Path, Path], tmp_path: Path
) -> None:
    shuffled = clone(exports[0], tmp_path / "shuffled", shuffle_seed=5)
    assert [m.sent_at for m in parse_export([shuffled])] != [
        m.sent_at for m in parse_export([exports[0]])
    ]
    assert sorted(keys(shuffled)) == sorted(keys(exports[0]))


def test_overlapping_exports_differ_in_folder_name_and_external_query(
    exports: tuple[Path, Path],
) -> None:
    a, b = exports
    folders_a = {d.name for d in (a / fmt.MESSAGES_DIR).iterdir()}
    folders_b = {d.name for d in (b / fmt.MESSAGES_DIR).iterdir()}
    assert folders_a != folders_b
    assert set(RENAMED_IN_B) <= folders_a
    assert set(RENAMED_IN_B.values()) <= folders_b
    assert {fmt.thread_id(Path(f)) for f in folders_a} == {
        fmt.thread_id(Path(f)) for f in folders_b
    }
    text_b = "".join(p.read_text(encoding="utf-8") for p in b.rglob("*.json"))
    assert "oh=bbbb2222" in text_b
    assert "oh=aaaa1111" not in text_b


# --- unmapped real keys ---------------------------------------------------------------------

_UNMAPPED_TOP = (
    fmt.KEY_IMAGE,
    fmt.KEY_JOINABLE_MODE,
    fmt.KEY_MAGIC_WORDS,
    fmt.KEY_IS_STILL_PARTICIPANT,
    fmt.KEY_THREAD_PATH,
)
_UNMAPPED_MESSAGE = (fmt.KEY_CALL_DURATION, fmt.KEY_IS_GEOBLOCKED, fmt.KEY_IS_UNSENT_IMAGE)
_UNMAPPED_SHARE = (
    fmt.KEY_SHARE_TEXT,
    fmt.KEY_SHARE_OWNER,
    fmt.KEY_SHARE_PROFILE_NAME,
    fmt.KEY_SHARE_PROFILE_USERNAME,
)


def _strip_unmapped(doc: dict[str, object]) -> dict[str, object]:
    out = {k: v for k, v in doc.items() if k not in _UNMAPPED_TOP}
    messages = []
    for m in doc[fmt.KEY_MESSAGES]:  # type: ignore[attr-defined]
        m = {k: v for k, v in m.items() if k not in _UNMAPPED_MESSAGE}
        if fmt.KEY_SHARE in m:
            m[fmt.KEY_SHARE] = {
                k: v for k, v in m[fmt.KEY_SHARE].items() if k not in _UNMAPPED_SHARE
            }
        for key in fmt.ATTACHMENT_KEYS:
            if key in m:
                m[key] = [
                    {k: v for k, v in item.items() if k != fmt.KEY_CREATION_TIMESTAMP}
                    for item in m[key]
                ]
        if fmt.KEY_REACTIONS in m:
            m[fmt.KEY_REACTIONS] = [
                {k: v for k, v in r.items() if k != fmt.KEY_REACTION_TIMESTAMP}
                for r in m[fmt.KEY_REACTIONS]
            ]
        messages.append(m)
    out[fmt.KEY_MESSAGES] = messages
    return out


def test_parser_ignores_every_unmapped_key(exports: tuple[Path, Path], tmp_path: Path) -> None:
    stripped = tmp_path / "stripped"
    shutil.copytree(exports[0], stripped)
    for path in stripped.rglob("message_*.json"):
        doc = _strip_unmapped(json.loads(path.read_text(encoding="utf-8")))
        path.write_text(json.dumps(doc), encoding="utf-8")

    assert list(parse_export([stripped])) == list(parse_export([exports[0]]))


def test_message_with_all_unmapped_keys_parses() -> None:
    raw = {
        fmt.KEY_SENDER: "a",
        fmt.KEY_TIMESTAMP_MS: 1,
        fmt.KEY_CALL_DURATION: 12,
        fmt.KEY_IS_GEOBLOCKED: False,
        fmt.KEY_IS_UNSENT_IMAGE: False,
        fmt.KEY_SHARE: {
            fmt.KEY_SHARE_LINK: "https://example.com/x",
            fmt.KEY_SHARE_TEXT: "t",
            fmt.KEY_SHARE_OWNER: "o",
            fmt.KEY_SHARE_PROFILE_NAME: "n",
            fmt.KEY_SHARE_PROFILE_USERNAME: "u",
        },
        fmt.KEY_PHOTOS: [{fmt.KEY_URI: "p/1.jpg", fmt.KEY_CREATION_TIMESTAMP: 5}],
        fmt.KEY_REACTIONS: [
            {
                fmt.KEY_REACTION_EMOJI: "x",
                fmt.KEY_REACTION_ACTOR: "b",
                fmt.KEY_REACTION_TIMESTAMP: 9,
            }
        ],
    }
    msg = parse_message(raw, thread_id="t", thread_title=None, source_file="f")
    assert msg.share_url == "https://example.com/x"
    assert msg.media_count == 1
    assert [(r.actor, r.emoji) for r in msg.reactions] == [("b", "x")]


def test_generated_export_exercises_every_unmapped_and_optional_key(
    exports: tuple[Path, Path],
) -> None:
    seen: set[str] = set()
    missing: set[str] = set()
    reactions_with_ts = reactions_without_ts = 0
    for path in exports[0].rglob("message_*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        seen |= set(doc)
        for m in doc[fmt.KEY_MESSAGES]:
            seen |= set(m)
            if fmt.KEY_CONTENT not in m:
                missing.add(fmt.KEY_CONTENT)
            if fmt.KEY_SHARE in m:
                seen |= set(m[fmt.KEY_SHARE])
                if fmt.KEY_SHARE_LINK not in m[fmt.KEY_SHARE]:
                    missing.add(fmt.KEY_SHARE_LINK)
            for key in fmt.ATTACHMENT_KEYS:
                for item in m.get(key, []):
                    if fmt.KEY_CREATION_TIMESTAMP in item:
                        seen.add(fmt.KEY_CREATION_TIMESTAMP)
                    else:
                        missing.add(fmt.KEY_CREATION_TIMESTAMP)
            for r in m.get(fmt.KEY_REACTIONS, []):
                if fmt.KEY_REACTION_TIMESTAMP in r:
                    reactions_with_ts += 1
                else:
                    reactions_without_ts += 1
    assert {*_UNMAPPED_TOP, *_UNMAPPED_MESSAGE, *_UNMAPPED_SHARE} <= seen
    assert fmt.KEY_CREATION_TIMESTAMP in seen
    assert missing == {fmt.KEY_CONTENT, fmt.KEY_SHARE_LINK, fmt.KEY_CREATION_TIMESTAMP}
    assert reactions_with_ts > 0
    assert reactions_without_ts > reactions_with_ts  # timestamps are the minority, as in reality


def test_generated_export_has_real_attachment_shapes(exports: tuple[Path, Path]) -> None:
    uris: list[str] = []
    for path in exports[0].rglob("message_*.json"):
        for m in json.loads(path.read_text(encoding="utf-8"))[fmt.KEY_MESSAGES]:
            uris += fmt.attachment_uris(m)
    external = [u for u in uris if not u.startswith(fmt.EXPORT_URI_PREFIX)]
    inside = [u for u in uris if u.startswith(fmt.EXPORT_URI_PREFIX)]
    assert external
    assert all(u.startswith("https://") for u in external)
    assert any("." not in u.rsplit("/", 1)[-1] for u in inside)  # an extensionless photo
    assert any("/photos/" in u for u in inside)
    assert any("/videos/" in u for u in inside)
    assert any("/audio/" in u for u in inside)


def test_generated_text_includes_vietnamese_and_emoji_mojibake(exports: tuple[Path, Path]) -> None:
    contents = [m.content or "" for m in parse_export([exports[0]])]
    assert any("Nguyễn" in c or "chào" in c for c in contents)
    assert any("😂" in c for c in contents)
    assert any(m.sender_name == "Nguyễn Thị Hương" for m in parse_export([exports[0]]))


def test_files_are_newest_first_and_chunked(exports: tuple[Path, Path], tmp_path: Path) -> None:
    for folder in (exports[0] / fmt.MESSAGES_DIR).iterdir():
        files = fmt.message_files(folder)
        counts = []
        newest_seen = None
        for f in files:
            ts = [
                m[fmt.KEY_TIMESTAMP_MS] for m in json.loads(f.read_text("utf-8"))[fmt.KEY_MESSAGES]
            ]
            assert ts == sorted(ts, reverse=True)
            if newest_seen is not None:
                assert ts[0] <= newest_seen  # later files hold older messages
            newest_seen = ts[-1]
            counts.append(len(ts))
        assert all(c == 30 for c in counts[:-1])
    default_a, _ = generate_pair(tmp_path / "default")
    assert all(len(fmt.message_files(d)) == 1 for d in (default_a / fmt.MESSAGES_DIR).iterdir())


def test_external_video_in_the_overlap_has_the_same_key_in_both_exports(
    exports: tuple[Path, Path],
) -> None:
    a, b = exports
    external_ts: set[int] = set()
    for path in b.rglob("message_*.json"):
        for m in json.loads(path.read_text(encoding="utf-8"))[fmt.KEY_MESSAGES]:
            if any(not u.startswith(fmt.EXPORT_URI_PREFIX) for u in fmt.attachment_uris(m)):
                external_ts.add(m[fmt.KEY_TIMESTAMP_MS])
    shared = {
        m.message_key for m in parse_export([a]) if int(m.sent_at.timestamp() * 1000) in external_ts
    } & set(keys(b))
    assert shared  # a message with an external URI whose query string differs, same key in both
