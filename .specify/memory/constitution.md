<!--
Sync Impact Report
- Version change: template -> 1.0.0
- Added principles: user-safe dictation, measurable quality, privacy by policy,
  reversible data handling, and accessible native workflows.
- Added sections: product boundaries and delivery workflow.
- Removed sections: none.
- Follow-up TODOs: none.
-->
# FlowSpeech Constitution

## Core Principles

### I. User-Safe Dictation

Every dictated result MUST remain recoverable until every user-selected delivery
channel reports its own outcome. A failed or uncertain paste MUST never be
presented as a successful insertion, and a late result MUST never be inserted
into a different window. Cancellation MUST prevent later delivery from the
cancelled session. This protects the user's words and trust.

### II. Measurable Speech Quality

Changes to recognition, cleanup, or latency MUST be evaluated against a versioned
corpus with Russian, English, mixed-language, silence, and noise samples. Product
claims about quality MUST cite the applicable measurements. Tests MUST cover
semantic preservation of names, numbers, negation, and literal dictation, not
only string formatting.

### III. Privacy Is an Enforced Policy

The active privacy mode MUST centrally govern every network destination and every
text, audio, diagnostic, and Markdown write. Strict local mode MUST make no
external request after required models are available. Explicit export to a user
selected folder is distinct from internal history; "do not save anywhere" MUST
disable both. Secrets MUST use platform-secure storage once signing is stable and
MUST NOT appear in logs, source control, or diagnostic exports.

### IV. User-Owned Files and Obsidian Safety

Markdown and Obsidian output MUST only be written below an explicitly selected
destination. Session and revision identifiers MUST make retries idempotent.
External edits, permission loss, or write conflicts MUST preserve the user's
content and produce a recoverable status, never silent overwrite. The live note
integration MUST not claim safe concurrent editing until its behavior is verified
with an open Obsidian note.

### V. Accessible, Native, and Testable Workflows

The normal dictation path MUST be usable without a terminal or manual config-file
editing. Critical states, errors, and retries MUST be visible without stealing
focus. Every user-visible behavior needs focused automated coverage where
possible, plus macOS scenario tests for microphone, Accessibility, paste, and
supported editor classes. Website pages MUST work with keyboard navigation and
reduced motion.

## Product Boundaries

FlowSpeech is a macOS dictation product with local and cloud recognition modes,
optional text processing, safe insertion, Markdown/Obsidian destinations, and a
public website. Both local and cloud modes are first-class. The first major
rework excludes mobile clients, meeting-recording products, team sync, model
training, monetization, and automatic message submission. Existing user data,
including dictionaries, snippets, history, and internal `!notes`, MUST be
preserved through migrations. Private `!notes` MUST remain ignored by Git.

## Delivery Workflow

Each bounded feature follows Specify -> Plan -> Tasks -> Analyze -> Implement ->
Converge. Specifications define user outcomes and compatibility boundaries before
implementation. The maintained `.plan.md` records the cross-feature roadmap; a
feature specification records the next independently reviewable slice. The
existing project tests and relevant macOS checks MUST pass before completion.
Run only the checks justified by the change, but do not weaken tests to obtain a
pass. Review the diff, verify no U+2013 or U+2014 characters appear in user-facing
Russian materials, and state any unverified system behavior.

## Governance

This constitution governs FlowSpeech 2 planning and implementation. Amendments
MUST be recorded here with a semantic version bump and a Sync Impact Report.
MAJOR changes redefine or remove a principle; MINOR changes add a principle or
material requirement; PATCH changes clarify existing governance. Every feature
plan and final review MUST explicitly check compliance with the five principles.
User instructions and repository-specific instructions take precedence over this
constitution.

**Version**: 1.0.0 | **Ratified**: 2026-09-22 | **Last Amended**: 2026-09-22
