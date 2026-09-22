# Implementation Plan: Markdown Dictation Export

**Branch**: `codex/flowspeech-2` | **Date**: 2026-09-22 |
**Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-markdown-export/spec.md`

## Summary

Add an opt-in destination for one Markdown file per completed normal dictation.
The export runs once the final text is ready and records an outcome independently
from text insertion. A stable session identifier and deterministic file name make
retries safe. A native settings tab lets the user choose and validate a folder,
including a folder inside an Obsidian vault, without changing the vault.

## Technical Context

**Language/Version**: Python 3.12 runtime; existing code is compatible with
Python 3.11+

**Primary Dependencies**: Existing PyObjC/AppKit, rumps, PyYAML, pytest;
no new runtime dependency

**Storage**: User-selected local directory for UTF-8 Markdown files; existing
YAML configuration stores an opt-in flag and directory

**Testing**: pytest 9.1.1 for unit tests; manual AppKit and Obsidian checks on
macOS

**Target Platform**: macOS arm64 first; files must use portable Markdown and
filesystem-safe ASCII names

**Project Type**: Existing macOS desktop application

**Performance Goals**: Exporting one final note must not delay a ready text by
more than 100 ms at p95 on a local SSD; the main dictation pipeline remains
responsive while a write failure is reported

**Constraints**: Export is disabled by default and is never implied by internal
history retention; Markdown export is independent from paste and internal
history; no vault scan, no network request, no audio or raw transcript by
default; no overwrite of an unrelated existing file

**Scale/Scope**: One selected root and one final `.md` file per ordinary
dictation; no daily-note append, live transcription, cross-device sync, or
Obsidian plugin in this feature

## Constitution Check

| Principle | Evidence before design | Result |
|---|---|---|
| User-Safe Dictation | Export has a distinct result, happens before the fallible paste call, and uses a stable retry snapshot. | Pass |
| Measurable Speech Quality | This feature does not change ASR or cleanup. Export tests check exact final-text fidelity. | Pass |
| Privacy Is an Enforced Policy | Export is opt-in, local-only, and never implied by history retention. No raw/audio export. | Pass |
| User-Owned Files and Obsidian Safety | Root is selected by the user, validated again at write, and retries cannot overwrite another note. | Pass |
| Accessible, Native, and Testable Workflows | Native folder selection, explicit success/failure, pytest coverage, and a manual macOS scenario are defined. | Pass |

No principle requires an exception.

## Project Structure

### Documentation (this feature)

```text
specs/001-markdown-export/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── markdown-export.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
flowspeech/
├── config.py              # immutable export configuration and persistence
├── config_manager.py      # existing configuration notification path
├── markdown_export.py     # new isolated validation and Markdown writer
├── main.py                # create one export snapshot and surface outcomes
└── settings.py            # selected folder and opt-in setting

tests/
├── test_config.py         # parse and save export configuration
├── test_markdown_export.py# writer, idempotency, containment, fidelity
└── test_config_manager.py # reload notification for export configuration

config.yaml                # disabled-by-default sample setting
```

**Structure Decision**: Keep the writer independent of AppKit and the
dictation pipeline. This preserves focused tests, lets the settings layer own
folder selection, and lets later daily/live note sinks reuse the same session
snapshot and outcome model.

## Phase 0: Research Decisions

See [research.md](research.md). Key decisions are an immutable export snapshot,
ASCII file names derived only from time and UUID, atomic no-clobber creation,
and a contained user-selected root. The local filesystem is trusted against
accidental misconfiguration, not an attacker that swaps directories during an
open file operation.

## Phase 1: Design Artifacts

- [Data model](data-model.md) defines destination, snapshot, result, and
  states.
- [Export contract](contracts/markdown-export.md) defines the boundary between
  the pipeline, settings, and isolated writer.
- [Quickstart](quickstart.md) defines automated and manual verification.

## Constitution Check After Design

All gates remain satisfied. The chosen design makes an export failure visible
without discarding a prepared dictation, limits filesystem access to the
selected root, and gives repeat attempts an idempotency key. The user-facing
setting is native and does not require a terminal. No new cloud service,
credential, or unbounded vault read is introduced.

## Complexity Tracking

No constitutional violation or added architectural exception requires tracking.
