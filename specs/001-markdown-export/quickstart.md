# Quickstart: Validate Markdown Dictation Export

## Prerequisites

- macOS with FlowSpeech dependencies installed.
- A temporary writable folder outside a personal vault for automated/manual
  validation.
- A second temporary writable folder if you also want to verify separate-file
  mode. Do not use an existing personal note as the test target.

## Automated Checks

Run the focused tests from the repository root:

```bash
.venv/bin/python -m pytest tests/test_markdown_export.py tests/test_config.py tests/test_config_manager.py tests/test_hotkey_state.py -q
```

Expected outcomes:

- Unicode and Markdown characters round-trip exactly.
- Two different snapshots create two notes, even with equal timestamps.
- Retrying one snapshot returns one verified note with no duplicate.
- A missing, read-only, or outside-root target returns a retryable failure.
- An occupied unrelated path is never replaced.

Then run the established suite:

```bash
.venv/bin/python -m pytest tests/ -q
```

## Recorded Automated Result

On 2026-09-22, the focused export, configuration, configuration-reload, and
pipeline checks completed with `60 passed`. The final full suite completed
with `197 passed`, and `./build_app.sh` produced `dist/FlowSpeech.app` before
the final test-only update.
UI automation could not enumerate native applications in this environment, so
the manual macOS scenario remains required.

## Manual macOS Scenario

1. Start the development build and open Settings.
2. Open the Markdown export tab, select a disposable folder, choose "Дневной
   файл", enable export, and confirm the write check and shown path match the
   selection.
3. Dictate two short Russian sentences into TextEdit. Confirm the text appears
   in TextEdit and one `YYYY-MM-DD.md` file contains two time-stamped blocks.
4. Make the destination unavailable, dictate a third sentence, and confirm the
   application says the text was not saved while keeping a retry action.
5. Restore access, choose retry, and confirm the daily file contains one block
   for the third sentence.
6. Switch to "Отдельные файлы", dictate once, and confirm one new independent
   Markdown file appears.
7. Disable export, dictate again, and confirm no new file or daily block is
   created while the old notes remain unchanged.

## Evidence to Record

Record the test folder, app version/commit, local/cloud recognition mode,
number of created notes, retry result, and any unverified UI behavior. Do not
record dictated content or secret paths in public logs.
