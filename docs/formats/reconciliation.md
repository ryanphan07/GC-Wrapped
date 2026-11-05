# Reconciling the parser with the real Instagram format

Source of truth: `docs/formats/instagram-schema.md` (content-free report from the real export,
reviewed by Ryan). This page lists what the code assumed before that report, what the report says,
and what changed. Only shapes and counts from the report appear here.

## 1. Assumption vs. real format vs. code change

| Topic | Earlier assumption | Real format (report) | Code change |
|---|---|---|---|
| Thread folder name | `<name>_<hex>`; `thread_id` = whole folder name | 422 folders: 416 `<name>_<digits>`, 6 bare `<digits>`; digit runs 15-17 long. The name part embeds a display name | `thread_id` = trailing digit run (`(?:^\|_)(\d+)$`); any other shape raises `ValueError` without the folder name. Generator emits both shapes, no hex |
| Files per thread | Several small chunks | 420 threads have 1 `message_N.json`, 2 have 2; max 10,000 messages per file | `MAX_MESSAGES_PER_FILE = 10000`; generator chunks at it (overridable for tests) |
| Message order | Newest first | 404 files newest-first, 20 have fewer than 2 distinct timestamps, 0 oldest-first | None in the parser (key does not depend on order). Generator writes newest-first |
| Timestamps | `timestamp_ms` integer | Always 13 digits | None |
| Attachment keys | `photos`, `videos`, `audio_files`, plus a `sticker` object | `photos`, `videos`, `audio_files` only; no `sticker` key | `sticker` handling removed; `ATTACHMENT_KEYS` lists the three |
| Attachment URI shape | Always under the export | `your_instagram_activity/messages/inbox/<thread>/{photos,videos,audio}/<file>`; 108 photo basenames have no extension; 5 video URIs are outside `your_instagram_activity/` | `EXPORT_URI_PREFIX`; `key_basename()` returns the basename or `None` for external URIs. Generator emits extensionless photos and external `https` video URLs |
| Attachment identity in `message_key` | Sorted full URIs | Full URIs embed the name-bearing folder; 0 duplicate basenames within a thread | `message_key` hashes sorted basenames of export URIs only. External URIs are not hashed but count in `media_count`. See decisions.md |
| Text encoding | Mojibake on non-ASCII text | 22649 of 107236 text messages match the latin-1 mojibake pattern (about 21%) | None (`fix_mojibake` already handles it). Generator now includes emoji and Vietnamese diacritics |
| `content` | Present, or missing on some | Present on 107236 of 110348 messages | None. Generator omits it on some messages |
| `share.link` | Present on shares | Present on 14285 of 14568 shares | None (already optional). Generator omits it on some shares |
| `reactions[].timestamp` | Not modelled | Present on 2005 of 26204 reactions | Ignored. Generator emits it on a minority of reactions |
| Attachment `creation_timestamp` | Not modelled | Missing on some photos (2111 of 2204 present) and videos (167 of 180) | Ignored. Generator omits it on some attachments |
| Extra top-level and message keys | Not modelled | See section 2 | Ignored. Generator emits all of them |
| Overlapping exports | Same folder names | A chat rename changes the name part of the folder | Generator renames one thread folder in export b (same digits) and uses a different export root; tests assert identical keys |

## 2. Mapped vs. deliberately ignored fields

Mapped (decisions.md, "Real-format fields not mapped in Phase 1"):

| Field | Why mapped |
|---|---|
| `sender_name`, `timestamp_ms`, `content` | Core message identity and text |
| `title` | Stored as `thread_title` |
| `photos`, `videos`, `audio_files` (`uri`) | Count for `media_count`; basename for the key |
| `share.link` | Part of the key; stored as `share_url` |
| `reactions[].actor`, `reactions[].reaction` | `fct_reactions` |

Ignored:

| Field (report presence) | Why ignored |
|---|---|
| `call_duration` (175 of 110348 messages) | No Phase 1 model uses it |
| `share.share_text` (13350 of 14568), `share.original_content_owner` (13545) | Unused; carries extra text and a name |
| `share.profile_share_name`, `share.profile_share_username` (30) | A person's name and handle (PII) |
| `joinable_mode` (`link`, `mode`; 65 of 424 files) | `link` is a live group invite link (PII) |
| `image` (7 of 424 files) | Unused group image reference |
| `magic_words` (always empty) | Unused |
| `thread_path` | Embeds the name-bearing folder name |
| `is_still_participant` | Unused |
| `is_geoblocked_for_viewer`, `is_unsent_image_by_messenger_kid_parent` | Unused flags |
| `reactions[].timestamp` | Unused; present on a minority |
| `creation_timestamp` on attachments | Unused; sometimes missing |
| `participants[]` | Members come from senders and reactors in dbt |

## 3. Only `inbox/` is ingested

The report lists these entries next to `inbox`: `message_requests`, `photos` and
`your_scheduled_chat_notifications.json`. The parser reads `your_instagram_activity/messages/inbox/`
and nothing else.

- `message_requests/` holds unaccepted DMs. Their senders have no consent relationship with the
  group, so they are out of scope by design.
- No attachment URI in `inbox/` points into `messages/photos/`, so that folder is not needed to
  resolve any attachment. (Phase 1 stores attachment metadata only.)

This closes the open question in the previous STATUS.md about the siblings of `inbox`.
