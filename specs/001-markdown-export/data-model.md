# Data Model: Markdown Dictation Export

## Export Destination

| Field | Description | Validation |
|---|---|---|
| enabled | Whether ordinary completed dictations are exported. | Boolean; default false. |
| directory | Root selected by the user. | Empty only when disabled; when enabled it resolves to an existing writable directory. |
| mode | How the writer stores completed dictations. | `daily` by default or `separate`. |

The destination is part of the configuration snapshot used by a dictation.
Changing settings affects future dictations and does not move existing notes.

## Dictation Export Snapshot

| Field | Description | Validation |
|---|---|---|
| session_id | Stable opaque identity of one completed dictation. | UUID generated once. |
| created_at | Local creation timestamp. | Captured once; valid ISO 8601 time. |
| final_text | Text ready for the user. | Non-empty Unicode string. |
| destination | Export destination selected when the text became ready. | Must be enabled and valid to attempt export. |

The writer does not inspect raw text, audio, active application data, or other
vault files.

## Export Result

| Field | Description |
|---|---|
| status | `saved`, `skipped`, or `failed`. |
| path | Created or verified existing note for `saved`; absent otherwise. |
| message | Safe user-facing description without dictated text. |
| retryable | True only when the same in-memory snapshot may be tried again. |

### State Transitions

```text
disabled -> skipped
ready -> saving -> saved
ready -> saving -> failed -> saving (retry) -> saved | failed
```

`failed` never changes paste status. `saved` is idempotent for the same
snapshot. A cancelled or empty dictation creates no snapshot.

## Exported Markdown

The default daily file is named `YYYY-MM-DD.md`. Each completed dictation
appends a Markdown block with its session marker, time heading, and final text.
The marker allows retry verification without adding the same block twice. The
separate-file mode uses YAML front matter and a deterministic file name.
