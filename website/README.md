# FlowSpeech product site

Static product pages for FlowSpeech. English is the default language; Russian,
Spanish, Portuguese, German, and French are selectable in the browser. Pages
describe only implemented behaviour, including daily Markdown export to an
ordinary user-selected folder.

## Preview

From the repository root:

```bash
python3 -m http.server 8765 --directory website
```

Open `http://127.0.0.1:8765/index.html`.

The site has no build step or third-party runtime dependency. The static-site
test checks the local page links and required shared assets.
