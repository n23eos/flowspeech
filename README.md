# FlowSpeech

**FlowSpeech is a local dictation tool for macOS: hold right option in any window, speak, release, and cleaned-up text lands at the cursor.** Recognition runs on faster-whisper on the machine itself, so it costs nothing and needs no internet. Cleanup of filler words, punctuation and slips goes through Claude, OpenAI, DeepSeek or Ollama, switchable from the menu bar. It counts words and speed in WPM, breaks usage down per application, and keeps a personal dictionary and a feedback log. Push-to-talk and toggle modes, a live waveform overlay while recording, and a build script that produces a real FlowSpeech.app with launch at login.

<div align="center">

[![Star on GitHub](https://img.shields.io/github/stars/N23eos/flowspeech?style=for-the-badge&logo=github&label=Star%20this%20repo&color=FFD700&labelColor=1a1a1a)](https://github.com/N23eos/flowspeech)

</div>

- Speech recognition runs **locally** (faster-whisper — free, no internet required).
- Text cleanup (filler words, punctuation, mistakes) via Claude / OpenAI / DeepSeek / Ollama, switchable right from the menu bar.
- Statistics: words, speed (WPM), top words, per-application breakdown.
- Personal dictionary and feedback log.
- Optional Markdown export: add completed dictations to one daily note or create a separate note in a selected folder.
- Built-in daily journal with calendar navigation, safe editing, note/task/idea entries and local search.
- Durable Markdown delivery queue with retry, backoff, recovery copies and explicit destination redirect.
- Optional raw/clean preview, strict voice prefixes and a reviewed daily summary.
- Live waveform overlay on screen while recording.
- Two modes: push-to-talk (hold) and toggle (short tap starts recording until the next tap).
- Builds into a real FlowSpeech.app with its own icon and launch-at-login support.

## Installation (one-time)

```bash
git clone https://github.com/<your-username>/flowspeech.git
cd flowspeech

# 1. Create the environment:
uv venv --python 3.12 .venv
uv pip install -r requirements.txt --python .venv/bin/python

# 2. API keys (not needed for Ollama or the "none" cleanup mode):
cp .env.example .env
open -e .env   # paste your keys and save
```

### macOS permissions (required)

System Settings → Privacy & Security:

1. **Microphone** — allow for Terminal (it will ask on first launch).
2. **Accessibility** — add Terminal (needed for the global hotkey and text insertion).

Without the second one the hotkey won't work — this is the most common issue.

## Running

### Option A: as an app (recommended)

```bash
./build_app.sh            # dev build (fast)
open dist/FlowSpeech.app
# like it? move it to /Applications:
cp -r dist/FlowSpeech.app /Applications/
```

Benefits: its own menu-bar icon, macOS asks Microphone/Accessibility permissions for FlowSpeech (not Terminal), and a "Launch at login" menu item.
`./build_app.sh --full` builds a standalone .app you can distribute.

In .app mode the config lives in `~/.flowspeech/config.yaml` (created automatically), API keys in `~/.flowspeech/.env`.

### Option B: from the terminal

```bash
.venv/bin/python -m flowspeech.main
```

A 🎤 icon appears in the menu bar. The first launch downloads the Whisper model (~460 MB) — give it a minute.

## Usage

1. Place your cursor in any text field (Telegram, email, VS Code…).
2. **Hold right option** (right ⌥) — you'll hear a pop and a live waveform appears at the bottom of the screen.
3. Speak. Release the key — the text is inserted within 2–4 seconds.

**Toggle mode for long dictations:** a short tap on the hotkey latches recording on — speak as long as you need, the next tap stops it.

Menu bar:

- Status line — "Today: N dictations · M words".
- **7-day statistics** — dictations, words, speed, top words.
- **Dictation history** — click to copy text to the clipboard.
- **Record in journal** - captures a note into today's Markdown file without pasting into the active app.
- **Open today's journal** - opens the built-in Markdown editor with conflict detection and a button for the source file.
- **Create daily summary** - previews a local or provider-generated summary before saving it. Cloud transfer requires confirmation first.
- **Journal hotkey** - optional dedicated key in the menu; it refuses collisions with dictation and Command Mode keys.
- **Cleanup provider** — Claude / OpenAI / DeepSeek / Ollama / no cleanup.
- **Hotkey** — right ⌥ / ⌘ / ⇧ / ⌃ or F13–F15. The choice is saved to config.yaml and survives restarts.
- **Settings…** — a window with tabs: General / Dictionary / Statistics.
- **Launch at login** (in .app mode).

## Configuration

Everything lives in `config.yaml`:

- `whisper.model` — `small` (fast) → `medium` (more accurate) → `large-v3` (best).
- `whisper.language` — `auto`, or pin `ru` / `en` (slightly faster and more accurate). Only pin it if you dictate in a single language: with `ru` set, English speech goes to Whisper flagged as "Russian audio", and you may get a translation instead of a transcription.
- `llm.provider` — the cleanup provider. Defaults to `none`: Whisper already produces punctuated text, and an LLM in this step can read the transcript as a message addressed to itself — dictate a question in English and the answer lands in your document. `formatter.py` guards against this with `<transcript>` tags, an explicit prompt, and a similarity check against what was said, but the only full guarantee is not calling the model. Enable cleanup from the menu bar if filler words bother you more.
- For Ollama: run `ollama pull llama3.1` first; the server must be running.
- `audio.tail_seconds` — extra recording after the key is released: the last audio block is still inside PortAudio when you release the hotkey. Without it, the tail of the last word gets clipped and short phrases like "one, two, three, testing" fail intermittently.

The microphone opens only for the duration of a recording and closes right after — the orange indicator is lit exactly while you dictate, Bluetooth headsets don't fall into the headset profile, and a fresh stream per recording picks up the current input device.

## Personal dictionary

Whisper mangling your terms and names? Add them to `~/.flowspeech/dictionary.yaml`:

```yaml
words:
  - FlowSpeech
  - Kubernetes
  - PostgreSQL
```

The words are hinted both to Whisper (during recognition) and to the LLM (during cleanup).

## Daily Markdown export

Open **Settings… → Markdown**, choose a folder, press **Check**, choose a
format, then enable automatic saving. The default format appends every
completed ordinary dictation to a UTF-8 file named `YYYY-MM-DD.md`, with a
time heading and a stable session ID. The other format creates a separate file
for every dictation.

The Markdown settings also control the daily template and folder layout. Keep
the compatible flat layout or choose `YYYY/MM/YYYY-MM-DD.md` for new entries.
Existing files are never moved. The **Journal** window lets you move between
days, edit the source Markdown, add a note, checkbox task or idea, search the
local index and open the file in another editor. External edits are detected
before save.

Optional voice prefixes work only when enabled and written as an exact prefix:
`task:`, `idea:`, `note:`, `задача:`, `идея:` or `заметка:`. A phrase such as
"my task: review this" stays ordinary text. Optional preview shows raw and
cleaned text before the single final Markdown block is written.

Export is off by default and is independent from clipboard insertion and the
internal history setting. If paste fails, FlowSpeech still tries to save the
ready text. Completed text enters a private local delivery queue before the
Markdown write. Failed writes survive app restarts and are retried on the next
launch or with **Retry Markdown export**. Interrupted daily blocks are restored
from the same session ID without duplicating a completed entry. Disabling export
never removes existing notes. Any Markdown-aware editor can open the chosen
folder, including Obsidian if you already use it.

Recovery steps and the queue export command are documented in
[`docs/RECOVERY.md`](docs/RECOVERY.md).

## Statistics and feedback

```bash
.venv/bin/python stats_report.py       # 7-day report
.venv/bin/python stats_report.py 30    # 30 days
```

Every dictation is logged to `~/.flowspeech/`:

- `stats.db` — SQLite: time, words, WPM, application, language.
- `feedback.jsonl` — "raw text → clean text" pairs. Review it to find dictionary candidates and weak spots in the prompt.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

## How it works

```
right option pressed  → microphone recording (16 kHz)
right option released → faster-whisper (local) → raw transcript
    → LLM cleanup (selected provider) → clean text
    → insertion: clipboard + Cmd+V (your clipboard is restored)
    → stats to SQLite + feedback log
```

| File | Responsibility |
|---|---|
| `flowspeech/main.py` | menu bar, pipeline assembly |
| `flowspeech/settings.py` | settings window (tabs) |
| `setup.py` + `build_app.sh` | FlowSpeech.app build (py2app) |
| `assets/make_icons.py` | icon generation (.icns + menu bar) |
| `flowspeech/hotkey.py` | global push-to-talk listener |
| `flowspeech/recorder.py` | microphone recording |
| `flowspeech/transcriber.py` | local Whisper |
| `flowspeech/formatter.py` | LLM cleanup (4 providers) |
| `flowspeech/injector.py` | text insertion into the active window |
| `flowspeech/stats.py` | statistics (SQLite) |
| `flowspeech/dictionary.py` | personal dictionary |
| `flowspeech/feedback.py` | raw/clean text log |

## Troubleshooting

- **Hotkey doesn't fire** — Terminal isn't added to Accessibility. Add it and restart the app.
- **Text wasn't inserted** — some fields block Cmd+V (password fields). That's expected.
- **Provider complains about a key** — check `.env`, restart the app.
- **Slow** — set `whisper.model: small` and pin `language` in config.yaml.

## Privacy

Audio never leaves your machine unless you explicitly enable cloud transcription (`whisper.cloud: groq`). Transcript text can leave the machine when an LLM cleanup provider is enabled. A cloud daily summary asks before sending the day's note. Private mode blocks cloud summary transfer. With `llm.provider: none` and local Whisper, everything is fully offline.

## License

[MIT](LICENSE)

## Support

If this project was useful to you, feel free to support further development:

[![ETH](https://img.shields.io/badge/ETH-0x7777...88C4-blue?logo=ethereum&style=flat-square)](https://etherscan.io/address/0x77777da54702AC8789D53fc7cC6201C29a1A88C4)[![Donate](https://img.shields.io/badge/donate-crypto-orange?style=flat-square)](https://etherscan.io/address/0x77777da54702AC8789D53fc7cC6201C29a1A88C4)

[![Buy me a coffee](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/n23eos)
