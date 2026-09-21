# Research: Markdown Dictation Export

## Decision: Isolate export from paste and history

**Rationale**: The current pipeline in `flowspeech/main.py` prepares `clean`,
then calls the paste helper, then records statistics. A paste exception can
therefore skip later work. The exporter receives final text immediately after
it is prepared and returns its own result; paste and internal history do not
determine whether the note is saved.

**Alternatives considered**:

- Export after paste: rejected because an insertion failure loses the export.
- Reuse the statistics row ID: rejected because it is created after paste and
  is unavailable before a failure.

## Decision: Create one immutable snapshot per dictation

**Rationale**: Generate a UUID and timestamp once after final text is ready.
The snapshot supplies the same destination, file name, and text to every retry.
It is held in memory for the active app session when export fails. This meets
the export requirement without changing the existing history-retention policy.

**Alternatives considered**:

- Generate an ID for every retry: rejected because it creates duplicate notes.
- Persist failed exports in hidden files: rejected because it violates the
  explicit "do not save anywhere" mode and expands the first slice.

## Decision: Use a deterministic ASCII filename and verbatim UTF-8 body

**Rationale**: A file name of `<timestamp>-<uuid>.md` cannot depend on spoken
text, app names, or a title. This avoids separators, reserved names, excessive
length, collisions, and path traversal. The Markdown body stores the final
text unchanged as UTF-8; front matter stores only session ID and creation time.

**Alternatives considered**:

- Derive a title from the dictation: deferred to note templates because it
  creates naming, collision, and privacy ambiguities.
- Store raw transcript, application name, provider, or audio by default:
  rejected by the privacy boundary.

## Decision: Validate the root at configuration and write time

**Rationale**: Folder selection validates that the resolved path is an existing
directory and supports a temporary write check. The writer repeats the check
because a drive or permissions can change later. It creates a basename directly
inside that root and does not scan the vault or resolve user-supplied relative
paths.

**Alternatives considered**:

- Treat every path as valid until the final write: rejected because errors
  arrive too late for setup feedback.
- Write into FlowSpeech data storage and move later: rejected because it hides
  a second copy of private text.

## Decision: Use atomic no-clobber creation and content verification on retry

**Rationale**: Write complete bytes to a unique temporary file in the selected
directory, flush it, and create the deterministic final file only if it does
not already exist. If it does exist, verify its session marker before returning
it as the previous success. Do not replace a preexisting file because it may
not belong to FlowSpeech.

**Alternatives considered**:

- Direct `write_text`: rejected because a crash can leave a partial note.
- Atomic replace: rejected because it can overwrite an unrelated file if the
  expected path is occupied.

## Decision: Treat concurrent hostile directory replacement as out of scope

**Rationale**: The selected local directory is trusted against accidental user
changes. Resolving and checking the selected root prevents ordinary escapes and
symlink misconfiguration. Defending against a process that swaps the directory
between checks requires lower-level descriptor-based operations and is not
needed for this first user-facing slice.

**Alternatives considered**:

- Descriptor-only writer with platform-specific no-follow controls: deferred
  until a threat model requires protection from a local hostile process.

## Decision: Use a native folder picker and a last-failure retry action

**Rationale**: The feature must work without editing YAML. The settings window
will let the user pick a folder and test it. The menu displays a retry action
only after the most recent export has failed, using the in-memory snapshot.

**Alternatives considered**:

- Ask users to paste a path into YAML: rejected by the usability requirement.
- Persist an unlimited retry queue: deferred until the session store redesign.
