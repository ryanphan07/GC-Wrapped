# Export structure

## Directory layout

```
.
thread_001/
  message_N.json x1
  audio/
    N.mp4 x1
  photos/
    N.heic x1
    N.jpg x14
    N.png x31
```

Thread folders: 1

## File name patterns

| pattern | files |
|---|---|
| `N.heic` | 1 |
| `N.jpg` | 14 |
| `N.mp4` | 1 |
| `N.png` | 31 |
| `message_N.json` | 1 |

## JSON schema: `message_N.json`

Files: 1 (unparseable: 0)

| path | types | presence | array len (min-max) | strings empty | strings mojibake | int digits (min-max) |
|---|---|---|---|---|---|---|
| `$` | object | - | - | - | - | - |
| `$.image` | object | 1/1 (100.0%) | - | - | - | - |
| `$.image.creation_timestamp` | integer | 1/1 (100.0%) | - | - | - | 10-10 |
| `$.image.uri` | string | 1/1 (100.0%) | - | 0/1 (0.0%) | 0/1 (0.0%) | - |
| `$.is_still_participant` | boolean | 1/1 (100.0%) | - | - | - | - |
| `$.joinable_mode` | object | 1/1 (100.0%) | - | - | - | - |
| `$.joinable_mode.link` | string | 1/1 (100.0%) | - | 0/1 (0.0%) | 0/1 (0.0%) | - |
| `$.joinable_mode.mode` | integer | 1/1 (100.0%) | - | - | - | 1-1 |
| `$.magic_words` | array | 1/1 (100.0%) | 0-0 | - | - | - |
| `$.messages` | array | 1/1 (100.0%) | 3220-3220 | - | - | - |
| `$.messages[]` | object | - | - | - | - | - |
| `$.messages[].audio_files` | array | 1/3220 (0.0%) | 1-1 | - | - | - |
| `$.messages[].audio_files[]` | object | - | - | - | - | - |
| `$.messages[].audio_files[].creation_timestamp` | integer | 1/1 (100.0%) | - | - | - | 10-10 |
| `$.messages[].audio_files[].uri` | string | 1/1 (100.0%) | - | 0/1 (0.0%) | 0/1 (0.0%) | - |
| `$.messages[].content` | string | 3152/3220 (97.9%) | - | 0/3152 (0.0%) | 652/3152 (20.7%) | - |
| `$.messages[].is_geoblocked_for_viewer` | boolean | 3220/3220 (100.0%) | - | - | - | - |
| `$.messages[].is_unsent_image_by_messenger_kid_parent` | boolean | 3213/3220 (99.8%) | - | - | - | - |
| `$.messages[].photos` | array | 44/3220 (1.4%) | 1-2 | - | - | - |
| `$.messages[].photos[]` | object | - | - | - | - | - |
| `$.messages[].photos[].creation_timestamp` | integer | 46/46 (100.0%) | - | - | - | 10-10 |
| `$.messages[].photos[].uri` | string | 46/46 (100.0%) | - | 0/46 (0.0%) | 0/46 (0.0%) | - |
| `$.messages[].reactions` | array | 1405/3220 (43.6%) | 1-4 | - | - | - |
| `$.messages[].reactions[]` | object | - | - | - | - | - |
| `$.messages[].reactions[].actor` | string | 2492/2492 (100.0%) | - | 0/2492 (0.0%) | 1061/2492 (42.6%) | - |
| `$.messages[].reactions[].reaction` | string | 2492/2492 (100.0%) | - | 0/2492 (0.0%) | 2492/2492 (100.0%) | - |
| `$.messages[].sender_name` | string | 3220/3220 (100.0%) | - | 0/3220 (0.0%) | 1119/3220 (34.8%) | - |
| `$.messages[].share` | object | 1101/3220 (34.2%) | - | - | - | - |
| `$.messages[].share.link` | string | 1090/1101 (99.0%) | - | 0/1090 (0.0%) | 0/1090 (0.0%) | - |
| `$.messages[].share.original_content_owner` | string | 1098/1101 (99.7%) | - | 1/1098 (0.1%) | 0/1098 (0.0%) | - |
| `$.messages[].share.share_text` | string | 1088/1101 (98.8%) | - | 98/1088 (9.0%) | 608/1088 (55.9%) | - |
| `$.messages[].timestamp_ms` | integer | 3220/3220 (100.0%) | - | - | - | 13-13 |
| `$.participants` | array | 1/1 (100.0%) | 5-5 | - | - | - |
| `$.participants[]` | object | - | - | - | - | - |
| `$.participants[].name` | string | 5/5 (100.0%) | - | 0/5 (0.0%) | 2/5 (40.0%) | - |
| `$.thread_path` | string | 1/1 (100.0%) | - | 0/1 (0.0%) | 0/1 (0.0%) | - |
| `$.title` | string | 1/1 (100.0%) | - | 0/1 (0.0%) | 0/1 (0.0%) | - |

