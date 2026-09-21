# Tasks: Markdown Dictation Export

**Input**: Design documents from `specs/001-markdown-export/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md),
[research.md](research.md), [data-model.md](data-model.md), and
[contracts/markdown-export.md](contracts/markdown-export.md)

**Tests**: Required by the FlowSpeech Constitution because the feature writes
user-owned text. Write the focused tests before their implementation tasks.

## Phase 1: Setup

**Purpose**: Make the first bounded FlowSpeech 2 feature visible and retain a
clean baseline.

- [x] T001 Record the focused test command and manual scenario in `specs/001-markdown-export/quickstart.md`.
- [x] T002 Add disabled-by-default Markdown export example settings to `config.yaml`.

---

## Phase 2: Foundational Export Contract

**Purpose**: Establish configuration and an isolated writer before connecting
the dictation pipeline.

- [x] T003 [P] Add failing configuration parsing and persistence tests for an optional export destination in `tests/test_config.py`.
- [x] T004 [P] Add failing unit tests for UTF-8 fidelity, deterministic retry, no-clobber collision, unavailable destination, and empty text in `tests/test_markdown_export.py`.
- [x] T005 Add immutable Markdown export configuration and safe block persistence in `flowspeech/config.py`.
- [x] T006 Implement destination validation, snapshot creation, atomic no-clobber creation, session-marker verification, and safe failures in `flowspeech/markdown_export.py`.
- [x] T007 Add configuration-reload coverage for the export destination in `tests/test_config_manager.py`.

**Checkpoint**: The writer can save or safely reject a snapshot without AppKit,
and focused tests prove one file per session with exact Unicode text.

---

## Phase 3: User Story 1 - Save Every Dictation (Priority: P1) MVP

**Goal**: A completed normal dictation creates one Markdown note in the selected
folder independently of paste delivery.

**Independent Test**: Mock one completed normal dictation with export enabled
and an insert failure; confirm exactly one note exists with the final text.

- [x] T008 [P] [US1] Add pipeline-facing export outcome tests, including a paste failure that still saves the note, in `tests/test_hotkey_state.py`.
- [x] T009 [US1] Create one export snapshot after final text is ready and call the writer separately from paste/history in `flowspeech/main.py`.
- [x] T010 [US1] Surface distinct saved, skipped, and failed export outcomes without leaking dictated text in `flowspeech/main.py`.
- [x] T011 [US1] Update the sample setting and user help for automatic Markdown export in `config.yaml` and `README.md`.

**Checkpoint**: With configuration supplied programmatically, ordinary
dictation saves one note before a paste failure can end the pipeline.

---

## Phase 4: User Story 2 - Control What Is Stored (Priority: P2)

**Goal**: A user selects, validates, enables, or disables an export directory
from native settings without editing YAML.

**Independent Test**: In the macOS development build, select a disposable
folder, validate it, save one note, disable export, and confirm no later note
is created.

- [x] T012 [P] [US2] Add focused configuration tests proving disabled export and `history_retention_days: 0` do not create an export destination in `tests/test_config.py`.
- [x] T013 [US2] Add a native Markdown export settings tab with folder selection, validation, clear destination preview, enable/disable control, and config reload in `flowspeech/settings.py`.
- [x] T014 [US2] Wire export configuration updates into the running app in `flowspeech/main.py`.
- [x] T015 [US2] Add the Markdown export tab and privacy behavior to `README.md`.

**Checkpoint**: The normal UI can enable or disable export and uses the saved
selection for future dictations only.

---

## Phase 5: User Story 3 - Recover From a Save Failure (Priority: P3)

**Goal**: A save failure keeps the prepared result available for one explicit
retry without producing a duplicate note.

**Independent Test**: Start with a destination that fails, restore it, invoke
retry once, and verify one note with the original session ID.

- [x] T016 [P] [US3] Add failure-then-retry and retry-after-success tests in `tests/test_markdown_export.py`.
- [x] T017 [US3] Retain only the most recent failed export snapshot in memory and expose a manual retry action in `flowspeech/main.py`.
- [x] T018 [US3] Show a concise retryable error and successful retry result in `flowspeech/main.py`.
- [x] T019 [US3] Document recovery behavior and its in-memory limitation in `README.md`.

**Checkpoint**: A failed destination does not create a partial note or lose the
ready text; retry creates or verifies one note.

---

## Phase 6: Polish and Cross-Cutting Validation

- [x] T020 [P] Check every new Russian user-facing string for U+2013/U+2014 in `flowspeech/`, `README.md`, and `config.yaml`.
- [x] T021 Run focused and full pytest suites from `tests/` and record outcomes in `specs/001-markdown-export/quickstart.md`.
- [ ] T022 Run the disposable-folder manual macOS scenarios from `specs/001-markdown-export/quickstart.md`.
- [x] T023 Review `git diff --check`, `.gitignore` coverage for `!notes`, and the final feature artifacts in `specs/001-markdown-export/`.

## Dependencies & Execution Order

```text
T001-T002
  -> T003-T007
  -> T008-T011 (US1, MVP)
  -> T012-T015 (US2)
  -> T016-T019 (US3)
  -> T020-T023
```

US2 depends on the configuration and writer established for US1. US3 depends
on the same writer and the export result created by US1. No user story requires
daily-note append, live-note editing, or website work.

## Parallel Opportunities

- T003 and T004 modify different test files and can run in parallel.
- T008 can be drafted while T005-T007 are under review, but must remain failing
  until T009 is implemented.
- T012 and documentation preparation T015 can run in parallel after US1.
- T016 and the text for T019 can run in parallel after US2.

## Implementation Strategy

1. Deliver the isolated writer and configuration first.
2. Complete and validate US1 before adding the UI: this proves the privacy and
   no-loss contract without depending on AppKit.
3. Add native configuration, then manual recovery.
4. Run the same export test against a disposable Obsidian vault only after the
   ordinary-folder scenario passes.

All tasks use the required checklist format and name their exact target files.

## Daily Markdown Revision

- [x] T024 Make the daily `YYYY-MM-DD.md` format the default while retaining
  separate-file export in `flowspeech/config.py` and `flowspeech/markdown_export.py`.
- [x] T025 Add daily-file append, existing-content preservation, and retry
  coverage in `tests/test_markdown_export.py`.
- [x] T026 Add the daily or separate mode selector and revised help text in
  `flowspeech/settings.py`, `README.md`, and this quickstart.
