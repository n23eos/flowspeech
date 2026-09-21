# Feature Specification: FlowSpeech Product Site

**Feature Branch**: `codex/flowspeech-2`
**Created**: 2026-09-22
**Status**: Implemented and locally verified in English and Spanish

## User Scenarios

### P1: Understand the product in one visit

A visitor opens the home page and understands the normal dictation path,
the local and cloud choices, and that Markdown export is optional.

### P2: Decide whether daily Markdown fits their workflow

A visitor opens the daily-notes page and sees the implemented dated-file
export, its safe retry behaviour, and that it works in an ordinary folder.

### P3: Check privacy and installation claims

A visitor can read the privacy boundary between local processing and a
third-party synchronized vault, then open the download page without seeing a
false public-release claim.

## Functional Requirements

- FR-001: Provide English-first pages for home, daily notes, privacy, and
  download, plus Russian, Spanish, Portuguese, German, and French localization.
- FR-002: Keep shared navigation and working local links on every page.
- FR-003: Use responsive layout and motion that respects normal browser use
  without a framework or third-party runtime.
- FR-004: Describe only confirmed FlowSpeech behaviour. Present daily Markdown
  export as the primary workflow and do not require Obsidian.
- FR-005: Make the site previewable with a local static HTTP server.

## Success Criteria

- All four pages render through the local preview and expose their page title
  and main heading in the browser accessibility tree.
- The desktop home page visibly presents the product path and animated
  dictation demonstration.
- Automated checks find no broken local page or asset link.
