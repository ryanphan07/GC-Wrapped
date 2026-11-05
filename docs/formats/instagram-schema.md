# Instagram export format report

Aggregates only. No names, text or URIs.

- `your_instagram_activity/messages/inbox` exists under the root: True

## Entries next to inbox

| entry | count |
|---|---|
| `inbox` | 1 |
| `message_requests` | 1 |
| `photos` | 1 |
| `your_scheduled_chat_notifications.json` | 1 |

## Threads

Thread folders: 422

### Folder shape

| value | count |
|---|---|
| <digits> | 6 |
| <name>_<digits> | 416 |

### Digit-run length (<name>_<digits> and <digits>)

| value | count |
|---|---|
| 15 | 32 |
| 16 | 204 |
| 17 | 186 |

### Message files per thread

| value | count |
|---|---|
| 1 | 420 |
| 2 | 2 |

Message files: 424 (unparseable: 0)

## Messages

Total messages: 110348

### Timestamp digit length

| value | count |
|---|---|
| 13 | 110348 |

### Message order within files

| value | count |
|---|---|
| indeterminate | 20 |
| newest-first | 404 |

Text messages matching the latin-1 mojibake pattern: 22649 of 107236

## Key frequencies

| path | types | present | array len |
|---|---|---|---|
| `$` | object | - | - |
| `$.image` | object | 7 of 424 | - |
| `$.image.creation_timestamp` | int | 7 of 7 | - |
| `$.image.uri` | str | 7 of 7 | - |
| `$.is_still_participant` | bool | 424 of 424 | - |
| `$.joinable_mode` | object | 65 of 424 | - |
| `$.joinable_mode.link` | str | 65 of 65 | - |
| `$.joinable_mode.mode` | int | 65 of 65 | - |
| `$.magic_words` | array | 424 of 424 | 0-0 |
| `$.messages` | array | 424 of 424 | 1-10000 |
| `$.messages[]` | object | - | - |
| `$.messages[].audio_files` | array | 44 of 110348 | 1-1 |
| `$.messages[].audio_files[]` | object | - | - |
| `$.messages[].audio_files[].creation_timestamp` | int | 44 of 44 | - |
| `$.messages[].audio_files[].uri` | str | 44 of 44 | - |
| `$.messages[].call_duration` | int | 175 of 110348 | - |
| `$.messages[].content` | str | 107236 of 110348 | - |
| `$.messages[].is_geoblocked_for_viewer` | bool | 110348 of 110348 | - |
| `$.messages[].is_unsent_image_by_messenger_kid_parent` | bool | 110213 of 110348 | - |
| `$.messages[].photos` | array | 1805 of 110348 | 1-30 |
| `$.messages[].photos[]` | object | - | - |
| `$.messages[].photos[].creation_timestamp` | int | 2111 of 2204 | - |
| `$.messages[].photos[].uri` | str | 2204 of 2204 | - |
| `$.messages[].reactions` | array | 20567 of 110348 | 1-37 |
| `$.messages[].reactions[]` | object | - | - |
| `$.messages[].reactions[].actor` | str | 26204 of 26204 | - |
| `$.messages[].reactions[].reaction` | str | 26204 of 26204 | - |
| `$.messages[].reactions[].timestamp` | int | 2005 of 26204 | - |
| `$.messages[].sender_name` | str | 110348 of 110348 | - |
| `$.messages[].share` | object | 14568 of 110348 | - |
| `$.messages[].share.link` | str | 14285 of 14568 | - |
| `$.messages[].share.original_content_owner` | str | 13545 of 14568 | - |
| `$.messages[].share.profile_share_name` | str | 30 of 14568 | - |
| `$.messages[].share.profile_share_username` | str | 30 of 14568 | - |
| `$.messages[].share.share_text` | str | 13350 of 14568 | - |
| `$.messages[].timestamp_ms` | int | 110348 of 110348 | - |
| `$.messages[].videos` | array | 130 of 110348 | 1-8 |
| `$.messages[].videos[]` | object | - | - |
| `$.messages[].videos[].creation_timestamp` | int | 167 of 180 | - |
| `$.messages[].videos[].uri` | str | 180 of 180 | - |
| `$.participants` | array | 424 of 424 | 1-224 |
| `$.participants[]` | object | - | - |
| `$.participants[].name` | str | 2006 of 2006 | - |
| `$.thread_path` | str | 424 of 424 | - |
| `$.title` | str | 424 of 424 | - |

## Attachments

| attachment key | masked URI shape | count |
|---|---|---|
| `audio_files` | `your_instagram_activity/messages/inbox/<thread>/audio/<file>.mp4` | 44 |
| `photos` | `your_instagram_activity/messages/inbox/<thread>/photos/<file>` | 108 |
| `photos` | `your_instagram_activity/messages/inbox/<thread>/photos/<file>.jpg` | 1579 |
| `photos` | `your_instagram_activity/messages/inbox/<thread>/photos/<file>.png` | 507 |
| `photos` | `your_instagram_activity/messages/inbox/<thread>/photos/<file>.webp` | 10 |
| `videos` | `<x>/<x>/<x>/<x>/<x>/<file>` | 5 |
| `videos` | `your_instagram_activity/messages/inbox/<thread>/videos/<file>.mp4` | 175 |

Duplicate attachment basenames within a thread: 0

