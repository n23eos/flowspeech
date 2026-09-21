# Markdown Export Contract

## Purpose

This contract defines the application boundary for exporting one final normal
dictation to an opt-in local Markdown destination. It is an internal contract,
not a network API.

## Configuration Contract

The application exposes an immutable destination with an enabled flag, one
selected directory, and a `daily` or `separate` mode. `daily` is the default.
Missing or malformed optional configuration means export is disabled. Enabling
without a valid writable directory fails visibly and leaves the last known-good
configuration active.

## Writer Contract

Input is a non-empty immutable export snapshot. The writer returns exactly one
result:

| Status | Meaning | Pipeline behavior |
|---|---|---|
| saved | A complete note was created, or the existing note was verified as this snapshot. | Display a saved result; do not retry automatically. |
| skipped | Export was not enabled for this snapshot. | Continue with paste/history normally. |
| failed | No note was created or verified. | Keep the snapshot in memory and expose manual retry. |

The writer MUST NOT throw an expected filesystem failure to the main pipeline.
Unexpected failures are converted to a safe failed result and logged without
dictated text.

## Note Contract

The default daily file has a deterministic `YYYY-MM-DD.md` name below the
selected root and uses UTF-8. It appends a session marker, time heading, and
the final text. The writer never appends a block whose marker is already in the
file. Separate-file mode uses front matter with `flowspeech_session`,
`created`, and `status: final`; it never overwrites a preexisting file whose
session marker does not match the input snapshot.

## Integration Contract

The ordinary-dictation pipeline creates the snapshot once final text is ready,
attempts export separately from paste, and preserves the export result even if
paste or statistics fail. Command Mode does not use this contract in the first
slice. A settings change applies only to snapshots created after the change.
