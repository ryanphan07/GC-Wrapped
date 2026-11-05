"""Instagram DM export parser.

Faithful by design: repairs Meta's text encoding (drops NUL bytes and replaces lone surrogates,
which Postgres text cannot store) and maps fields, nothing else. Dedup is by `message_key`
(see decisions.md, "Message dedup key").

Privacy: error messages name fields and counts only, never values.
"""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gcwrapped.models import ParsedMessage, Reaction
from gcwrapped.parsers import instagram_format as fmt

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class ParseError(ValueError):
    """The export does not match the expected format. Messages never include values."""


@dataclass
class ParseStats:
    """Counts filled in while `parse_export` is consumed."""

    thread_ids: set[str] = field(default_factory=set)
    messages: int = 0
    duplicates_dropped: int = 0


def fix_mojibake(s: str) -> str:
    """Undo Meta's "UTF-8 bytes written as latin-1 code points". Clean strings pass through."""
    try:
        return s.encode(fmt.MOJIBAKE_CODEC).decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def clean_text(s: str) -> str:
    """Fix mojibake, drop NUL bytes, replace lone surrogates with U+FFFD.

    Applied to every text field before hashing and loading. Postgres rejects both NUL and
    surrogates, and one bad row would roll back the whole load.
    """
    return _LONE_SURROGATE.sub("�", fix_mojibake(s).replace("\x00", ""))


def message_key(
    *,
    thread_id: str,
    timestamp_ms: int,
    sender_name: str,
    content: str | None,
    attachment_basenames: Iterable[str],
    share_url: str | None,
    occurrence: int,
) -> str:
    """Stable id for a message: sha256 over every immutable field plus an occurrence index.

    Reactions are excluded on purpose: they can change between exports.

    Attachments enter the key as the sorted basenames of export URIs only (`fmt.key_basename`);
    external URIs are excluded (see decisions.md, "Attachment identity is the URI basename").
    `sender_name` and `content` must already be cleaned with `clean_text`. `occurrence` is the
    0-based index among messages identical on every other field, which keeps two genuinely
    identical messages distinct while staying the same across re-exports.
    """
    payload = json.dumps(
        [
            fmt.PLATFORM,
            thread_id,
            timestamp_ms,
            sender_name,
            content or "",
            sorted(attachment_basenames),
            share_url or "",
            occurrence,
        ],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _Identity:
    """The fields (other than thread and occurrence) that identify a message."""

    timestamp_ms: int
    sender_name: str
    content: str | None
    attachment_basenames: tuple[str, ...]
    share_url: str | None


def _clean_optional(s: str | None) -> str | None:
    return clean_text(s) if s is not None else None


def _attachment_basenames(raw: dict[str, Any]) -> tuple[str, ...]:
    """Sorted key basenames of a message's export attachments; external URIs are skipped."""
    names = (fmt.key_basename(clean_text(u)) for u in fmt.attachment_uris(raw))
    return tuple(sorted(n for n in names if n is not None))


def _identity(raw: dict[str, Any]) -> _Identity:
    timestamp_ms = raw.get(fmt.KEY_TIMESTAMP_MS)
    sender = raw.get(fmt.KEY_SENDER)
    if not isinstance(timestamp_ms, int) or isinstance(timestamp_ms, bool):
        raise ParseError(f"message field {fmt.KEY_TIMESTAMP_MS} missing or not an integer")
    if not isinstance(sender, str):
        raise ParseError(f"message field {fmt.KEY_SENDER} missing or not a string")
    content = raw.get(fmt.KEY_CONTENT)
    return _Identity(
        timestamp_ms=timestamp_ms,
        sender_name=clean_text(sender),
        content=clean_text(content) if isinstance(content, str) else None,
        attachment_basenames=_attachment_basenames(raw),
        share_url=_clean_optional(fmt.share_link(raw)),
    )


def _reactions(raw: dict[str, Any]) -> list[Reaction]:
    out: list[Reaction] = []
    for item in raw.get(fmt.KEY_REACTIONS) or []:
        if not isinstance(item, dict):
            continue
        emoji = item.get(fmt.KEY_REACTION_EMOJI)
        actor = item.get(fmt.KEY_REACTION_ACTOR)
        if isinstance(emoji, str) and isinstance(actor, str):
            out.append(Reaction(actor=clean_text(actor), emoji=clean_text(emoji)))
    return out


def parse_message(
    raw: dict[str, Any],
    *,
    thread_id: str,
    thread_title: str | None,
    source_file: str,
    occurrence: int = 0,
) -> ParsedMessage:
    """Map one raw message object to a ParsedMessage. Raises ParseError on missing core fields."""
    ident = _identity(raw)
    return ParsedMessage(
        message_key=message_key(
            thread_id=thread_id,
            timestamp_ms=ident.timestamp_ms,
            sender_name=ident.sender_name,
            content=ident.content,
            attachment_basenames=ident.attachment_basenames,
            share_url=ident.share_url,
            occurrence=occurrence,
        ),
        platform=fmt.PLATFORM,
        thread_id=thread_id,
        thread_title=thread_title,
        sender_name=ident.sender_name,
        sent_at=_EPOCH + timedelta(milliseconds=ident.timestamp_ms),
        content=ident.content,
        media_count=len(fmt.attachment_uris(raw)),
        share_url=ident.share_url,
        reactions=_reactions(raw),
        source_file=source_file,
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ParseError(f"cannot read message file as JSON ({type(exc).__name__})") from None
    if not isinstance(data, dict) or not isinstance(data.get(fmt.KEY_MESSAGES), list):
        raise ParseError(f"message file lacks a {fmt.KEY_MESSAGES} list")
    return data


def _parse_root(root: Path) -> Iterator[ParsedMessage]:
    """Parse one export root. Occurrence counters are per thread, across all its files."""
    for thread_dir in fmt.find_thread_dirs(root):
        tid = fmt.thread_id(thread_dir)
        seen: Counter[_Identity] = Counter()
        for path in fmt.message_files(thread_dir):
            data = _read_json(path)
            title = data.get(fmt.KEY_THREAD_TITLE)
            thread_title = clean_text(title) if isinstance(title, str) else None
            source_file = fmt.source_file_label(tid, path)
            for raw in data[fmt.KEY_MESSAGES]:
                if not isinstance(raw, dict):
                    raise ParseError(f"{fmt.KEY_MESSAGES} entry is not an object")
                ident = _identity(raw)
                occurrence = seen[ident]
                seen[ident] += 1
                yield parse_message(
                    raw,
                    thread_id=tid,
                    thread_title=thread_title,
                    source_file=source_file,
                    occurrence=occurrence,
                )


def parse_export(roots: Iterable[Path], stats: ParseStats | None = None) -> Iterator[ParsedMessage]:
    """Yield deduplicated messages from one or more export roots.

    Roots are separate exports (possibly overlapping). A message whose key was already yielded in
    this run is dropped and counted in `stats.duplicates_dropped`. Within a single root keys are
    unique by construction, because the occurrence index separates identical messages.
    """
    stats = stats if stats is not None else ParseStats()
    yielded: set[str] = set()
    for root in roots:
        for msg in _parse_root(root):
            stats.thread_ids.add(msg.thread_id)
            if msg.message_key in yielded:
                stats.duplicates_dropped += 1
                continue
            yielded.add(msg.message_key)
            stats.messages += 1
            yield msg


class InstagramParser:
    """`Parser` protocol adapter: parse a single export root."""

    def parse(self, path: Path) -> Iterator[ParsedMessage]:
        return parse_export([path])
