# Implementation Plan: FlowSpeech Product Site

## Technical Context

The repository has no existing web runtime. Build a static site under
`website/` with plain HTML, CSS and small progressive JavaScript. This keeps
the product preview dependency-free and avoids coupling product messaging to
the macOS app bundle.

## Design

- Use a dark indigo hero, warm light content surfaces, one violet action
  colour, responsive cards, and a short animated waveform.
- Reuse one stylesheet and one small reveal-animation script across pages.
- Write only claims verified by the current implementation and show future
  live-note editing as planned work.

## Validation

- Run the static page integrity tests.
- Serve the directory with Python's static HTTP server.
- Inspect the home page visually and verify each page heading in the browser.
