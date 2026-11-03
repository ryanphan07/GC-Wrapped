> **PROVISIONAL — not yet verified against a real export.**
> Built from public third-party descriptions, not from Meta documentation (none found) or from
> Ryan's export. Replace with the output of `scripts/inspect_export.py` run on the real export,
> then reconcile `src/gcwrapped/parsers/instagram_format.py`, the only module that encodes these
> assumptions.

# Instagram DM export format (JSON)

## Sources
- [chatpack Instagram parser](https://docs.rs/crate/chatpack/latest/source/src/parsers/instagram.rs): `participants` + `messages` per file; `sender_name`, `timestamp_ms`, `content`; `share_text` used when a shared post has no `content`; mojibake repair applied to `sender_name` and `content`.
- [Instagram-Direct-Stats](https://github.com/dudynets/Instagram-Direct-Stats) and [igmd](https://github.com/kr3t3n/igmd): thread folders contain `message_N.json`; media (photos, videos, audio, shares, stickers) appear as per-message keys.
- [Analyzing Instagram chat data using Python](https://devqueue.medium.com/analyzing-instagram-chat-data-using-python-500ab6941dc3): per-message keys `share`, `reactions`, `photos`, `audio_files`, `videos`; `timestamp_ms` is epoch milliseconds.
- [StackOverflow: Facebook JSON badly encoded (mirror)](https://magenaut.com/?p=21847): UTF-8 bytes written as latin-1 code points; fix is `s.encode("latin-1").decode("utf-8")`.

## Layout (assumed)
```
<export root>/your_instagram_activity/messages/inbox/<thread folder>/message_1.json
                                                                     message_2.json ...
```
- `message_1.json` holds the newest messages; each file is ordered newest-first.
- Thread identity = folder name (stable however the files are chunked).

## File object (assumed)
| key | type | used |
|---|---|---|
| `participants` | `[{"name": str}]` | no (member identity is a dbt concern) |
| `messages` | `[message]` | yes |
| `title` | str | yes → `thread_title` |
| `thread_path` | str | no |

## Message object (assumed)
| key | type | required | maps to |
|---|---|---|---|
| `sender_name` | str (display name, not username) | yes | `sender_name` |
| `timestamp_ms` | int, epoch ms | yes | `sent_at` (UTC) |
| `content` | str | no (absent for media-only or unsent) | `content` |
| `photos` / `videos` / `audio_files` | `[{"uri": str, ...}]` | no | `media_count`, key |
| `sticker` | `{"uri": str}` | no | `media_count`, key |
| `share` | `{"link": str, "share_text": str}` | no | `share_url` |
| `reactions` | `[{"reaction": str, "actor": str}]` | no | `reactions` |

## Encoding quirk
All strings (`sender_name`, `content`, `title`, reaction fields) have UTF-8 bytes serialized as
latin-1 code points. The parser repairs this before hashing; strings that are already clean (or
don't round-trip) are returned unchanged.

## Known unknowns to check against the real export
- Exact root path (`your_instagram_activity/messages/inbox` vs older `messages/inbox`).
- Whether reaction objects, `share`, and `sticker` have the shapes above.
- Whether `uri` values are stable across exports (they feed the message key).
- Message shapes not modeled: calls, unsent messages, story replies, system messages (dbt later).
