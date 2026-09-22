"""Tests for the hotkey state machine in main.py.

main.py pulls in rumps, AppKit, Quartz and pynput, none of which import on a
headless box — and the state machine has nothing to do with any of them. Stub
them, then build a FlowSpeechApp without running rumps.App.__init__ and drive
the callbacks the way the listener thread would.

The bug these tests pin down: stopping a latched recording with a tap fired
_finish_recording twice — once from the key-down, once from the key-up, whose
staleness check compared against the press time of the *starting* press. The
second call reset the menu bar icon and hid the overlay mid-transcription.
"""

import logging
import sys
import threading
import types

import pytest

from flowspeech.config import (
    AppConfig,
    LLMConfig,
    MarkdownExportConfig,
    PrivateMode,
    ProviderConfig,
    WhisperConfig,
)
from flowspeech.export_queue import ExportQueue
from flowspeech.session import SessionToken
from flowspeech.transcriber import Transcript


def _stub(name: str, **attrs) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules.setdefault(name, module)


class _FakeMenuItem:
    def __init__(self, title="", callback=None, icon=None):
        self.title = title
        self.callback = callback
        self.icon = icon
        self.state = False


class _FakeApp:
    def __init__(self, *args, **kwargs):
        self.menu = []
        self.icon = None
        self.title = None


def _install_stubs() -> None:
    _stub("rumps", App=_FakeApp, MenuItem=_FakeMenuItem, alert=lambda *a, **k: None)
    _stub("objc", super=super)
    _stub(
        "AppKit",
        NSPasteboard=object, NSPasteboardTypeString="s", NSWorkspace=object,
        NSOperationQueue=object, NSPanel=object, NSScreen=object, NSColor=object,
        NSFont=object, NSTextField=object, NSView=object, NSBezierPath=object,
        NSVisualEffectView=object,
        NSBackingStoreBuffered=0, NSStatusWindowLevel=0,
        NSWindowStyleMaskBorderless=0, NSTextAlignmentCenter=1,
    )
    _stub("Foundation", NSMakeRect=lambda *a: None, NSTimer=object)
    _stub("Quartz", CGEventSourceCreate=lambda *a: None)
    keyboard = types.SimpleNamespace(
        Key=types.SimpleNamespace(
            alt_r="alt_r", cmd_r="cmd_r", shift_r="shift_r", ctrl_r="ctrl_r",
            f13="f13", f14="f14", f15="f15",
        ),
        Listener=object,
    )
    _stub("pynput", keyboard=keyboard)
    _stub("pynput.keyboard", **vars(keyboard))


_install_stubs()

from flowspeech import main as fsmain  # noqa: E402


@pytest.fixture
def app(monkeypatch):
    """A FlowSpeechApp with only the state-machine fields wired up."""
    instance = object.__new__(fsmain.FlowSpeechApp)
    instance._state_lock = threading.RLock()
    instance._state = fsmain.STATE_IDLE
    instance._mode = fsmain.MODE_DICTATION
    instance._selection = None
    instance._latched = False
    instance._swallow_release = False
    instance._press_started = 0.0
    instance.finishes = 0
    instance.begins = 0
    instance.modes = []
    instance._overlay = types.SimpleNamespace(
        messages=[], flash=lambda message: instance._overlay.messages.append(message)
    )

    def begin(mode=fsmain.MODE_DICTATION):
        instance.begins += 1
        instance.modes.append(mode)
        instance._state = fsmain.STATE_RECORDING
        instance._mode = mode
        instance._press_started = fsmain.time.monotonic()

    def finish():
        instance.finishes += 1
        if instance._state == fsmain.STATE_RECORDING:
            instance._state = fsmain.STATE_PROCESSING
            instance._latched = False

    monkeypatch.setattr(instance, "_begin_recording", begin)
    monkeypatch.setattr(instance, "_finish_recording", finish)
    return instance


def _hold(app, seconds: float) -> None:
    app._on_hotkey_down()
    app._press_started -= seconds  # pretend the key was held that long
    app._on_hotkey_up()


def test_push_to_talk_records_once_and_finishes_once(app):
    _hold(app, 1.0)

    assert (app.begins, app.finishes) == (1, 1)
    assert app._state == fsmain.STATE_PROCESSING


def test_short_tap_latches_instead_of_finishing(app):
    _hold(app, 0.1)

    assert app.begins == 1
    assert app.finishes == 0
    assert app._latched
    assert app._state == fsmain.STATE_RECORDING


def test_second_tap_stops_a_latched_recording_exactly_once(app):
    _hold(app, 0.1)          # latch on
    app._on_hotkey_down()    # the stopping tap
    app._on_hotkey_up()      # its release must be swallowed

    assert app.finishes == 1, "the release re-entered _finish_recording"
    assert app.begins == 1
    assert app._state == fsmain.STATE_PROCESSING
    assert not app._swallow_release


def test_keypress_during_processing_is_ignored(app):
    _hold(app, 1.0)
    assert app._state == fsmain.STATE_PROCESSING

    _hold(app, 1.0)

    assert (app.begins, app.finishes) == (1, 1)
    assert app._overlay.messages == ["⏳ Предыдущая запись ещё обрабатывается"]


def test_release_without_a_press_is_ignored(app):
    app._on_hotkey_up()

    assert (app.begins, app.finishes) == (0, 0)


def test_command_hotkey_starts_command_mode(app):
    app._on_command_hotkey_down()
    app._press_started -= 1.0
    app._on_command_hotkey_up()

    assert app.modes == [fsmain.MODE_COMMAND]
    assert (app.begins, app.finishes) == (1, 1)


def test_other_hotkey_stops_a_running_recording_exactly_once(app):
    # Dictation is latched on; a command-key tap stops it (the capture's mode
    # was fixed at start), and the command key's release must be swallowed too.
    _hold(app, 0.1)                  # dictation latched
    app._on_command_hotkey_down()    # stop tap from the OTHER key
    app._on_command_hotkey_up()

    assert app.finishes == 1
    assert app.modes == [fsmain.MODE_DICTATION]
    assert app._state == fsmain.STATE_PROCESSING


def test_latched_recording_survives_a_stray_release(app):
    _hold(app, 0.1)  # latched
    app._on_hotkey_up()  # e.g. a repeated release event from the OS

    assert app.finishes == 0
    assert app._state == fsmain.STATE_RECORDING


def test_hold_after_a_completed_dictation_starts_a_new_one(app):
    _hold(app, 1.0)
    app._state = fsmain.STATE_IDLE  # the worker thread finished

    _hold(app, 1.0)

    assert (app.begins, app.finishes) == (2, 2)


def test_500_hotkey_cycles_have_no_duplicate_finish(app):
    for _ in range(500):
        app._state = fsmain.STATE_IDLE
        _hold(app, 1.0)

    assert (app.begins, app.finishes) == (500, 500)


def test_journal_hotkey_uses_journal_mode_when_export_is_enabled(app):
    app._config = types.SimpleNamespace(
        markdown_export=MarkdownExportConfig(enabled=True, directory=None)
    )

    app._on_journal_hotkey_down()
    app._press_started -= 1.0
    app._on_journal_hotkey_up()

    assert app.modes == [fsmain.MODE_JOURNAL]
    assert (app.begins, app.finishes) == (1, 1)


def test_listener_health_restarts_dead_listener_after_sleep(monkeypatch):
    instance = object.__new__(fsmain.FlowSpeechApp)
    instance._listener = types.SimpleNamespace(is_alive=False)
    instance._active_hotkey = "right_option"
    restarted = []
    monkeypatch.setattr(fsmain, "is_accessibility_trusted", lambda: True)
    monkeypatch.setattr(instance, "_start_listener", restarted.append)

    instance._check_listener_health()

    assert restarted == ["right_option"]


def test_listener_health_reports_revoked_accessibility(monkeypatch):
    instance = object.__new__(fsmain.FlowSpeechApp)
    instance._listener = types.SimpleNamespace(is_alive=True)
    instance._active_hotkey = "right_option"
    instance._overlay = types.SimpleNamespace(
        messages=[], flash=lambda message: instance._overlay.messages.append(message)
    )
    monkeypatch.setattr(fsmain, "is_accessibility_trusted", lambda: False)

    instance._check_listener_health()

    assert instance._overlay.messages == ["⚠️ FlowSpeech потерял доступ Accessibility"]


@pytest.mark.parametrize(
    (
        "mode",
        "expected_message",
        "expect_paste",
        "current_app",
        "expect_copy",
        "markdown_options",
        "formatted_text",
        "expected_note",
        "expected_previews",
    ),
    [
        (
            fsmain.MODE_DICTATION,
            "⚠️ Вставка не выполнена, Markdown сохранён",
            True,
            "unknown",
            False,
            {},
            "Готовая заметка",
            "Готовая заметка",
            [],
        ),
        (
            fsmain.MODE_JOURNAL,
            "💾 Запись добавлена в дневник",
            False,
            "unknown",
            False,
            {},
            "Готовая заметка",
            "Готовая заметка",
            [],
        ),
        (
            fsmain.MODE_DICTATION,
            "📋 Фокус изменился. Текст скопирован, вставка отменена",
            False,
            "Safari",
            True,
            {},
            "Готовая заметка",
            "Готовая заметка",
            [],
        ),
        (
            fsmain.MODE_JOURNAL,
            "💾 Запись добавлена в дневник",
            False,
            "unknown",
            False,
            {"voice_prefixes": True, "live_preview": True},
            "задача: Купить молоко",
            "- [ ] Купить молоко",
            ["черновик", "- [ ] Купить молоко"],
        ),
    ],
)
def test_pipeline_saves_markdown_and_respects_delivery_mode(
    tmp_path,
    monkeypatch,
    caplog,
    mode,
    expected_message,
    expect_paste,
    current_app,
    expect_copy,
    markdown_options,
    formatted_text,
    expected_note,
    expected_previews,
):
    """The file export happens before a failed accessibility paste."""
    caplog.set_level(logging.INFO, logger=fsmain.logger.name)
    destination = tmp_path / "Dictations"
    destination.mkdir()
    instance = object.__new__(fsmain.FlowSpeechApp)
    instance._config = AppConfig(
        hotkey="right_option",
        whisper=WhisperConfig("small", "ru", "auto"),
        llm=LLMConfig("none", {}),
        data_dir=tmp_path,
        markdown_export=MarkdownExportConfig(
            enabled=True, directory=destination, **markdown_options
        ),
    )
    instance._recorder = types.SimpleNamespace(
        stop=lambda: types.SimpleNamespace(
            duration_sec=1.0, rms=0.1, reason=None, ok=True, audio=object()
        )
    )
    instance._transcriber = types.SimpleNamespace(
        transcribe=lambda _audio, _prompt: Transcript("черновик", "ru", 1.0)
    )
    instance._overlay = types.SimpleNamespace(
        messages=[], previews=[],
        flash=lambda message: instance._overlay.messages.append(message),
        show_preview=lambda text: instance._overlay.previews.append(text),
        hide=lambda: None,
    )
    instance._stats = types.SimpleNamespace(record_session=lambda _session: None)
    instance._export_queue = ExportQueue(tmp_path)
    instance._target_app = "TextEdit"
    instance._active_provider = "none"
    instance._cleanup_mode = fsmain.DEFAULT_MODE
    instance._mode = mode
    instance._selection = None
    instance._session_id = None
    instance._session_started_at = None
    instance._session_config = None
    instance._session_provider = None
    instance._session_cleanup_mode = None
    instance._session_started_monotonic = None
    instance._state = fsmain.STATE_PROCESSING
    instance._state_lock = threading.RLock()
    instance._failed_markdown_export = None
    monkeypatch.setattr(instance, "_set_state", lambda _icon: None)
    monkeypatch.setattr(instance, "_refresh_history_menu", lambda: None)
    monkeypatch.setattr(fsmain, "load_words", lambda _directory: [])
    monkeypatch.setattr(fsmain, "load_snippets", lambda _directory: {})
    monkeypatch.setattr(
        fsmain, "format_text", lambda *_args, **_kwargs: formatted_text
    )
    monkeypatch.setattr(fsmain, "apply_snippets", lambda text, _snippets: text)
    monkeypatch.setattr(fsmain, "play_sound", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fsmain.feedback, "log_entry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fsmain, "frontmost_app_name", lambda: current_app)
    copied = []
    monkeypatch.setattr(
        fsmain, "copy_to_clipboard", lambda text: copied.append(text) or True
    )

    paste_calls = []

    def paste_fails(text):
        paste_calls.append(text)
        raise RuntimeError("Accessibility denied")

    monkeypatch.setattr(fsmain, "insert_text", paste_fails)

    instance._process_audio()

    notes = list(destination.glob("*.md"))
    assert len(notes) == 1
    content = notes[0].read_text(encoding="utf-8")
    assert expected_note in content
    assert content.count("flowspeech_entry_begin:") == 1
    assert bool(paste_calls) is expect_paste
    assert bool(copied) is expect_copy
    assert instance._overlay.previews == expected_previews
    assert instance._overlay.messages == [expected_message]
    timing_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "whisper=" in timing_logs
    assert "export=" in timing_logs
    assert "Готовая заметка" not in timing_logs


def test_cancelled_processing_discards_late_formatter_result(tmp_path, monkeypatch):
    destination = tmp_path / "Dictations"
    destination.mkdir()
    instance = object.__new__(fsmain.FlowSpeechApp)
    instance._config = AppConfig(
        hotkey="right_option",
        whisper=WhisperConfig("small", "ru", "auto"),
        llm=LLMConfig("none", {}),
        data_dir=tmp_path,
        markdown_export=MarkdownExportConfig(
            enabled=True, directory=destination, live_preview=True
        ),
    )
    instance._recorder = types.SimpleNamespace(
        stop=lambda: types.SimpleNamespace(
            duration_sec=1.0, rms=0.1, reason=None, ok=True, audio=object()
        )
    )
    instance._transcriber = types.SimpleNamespace(
        transcribe=lambda _audio, _prompt: Transcript("черновик", "ru", 1.0)
    )
    instance._overlay = types.SimpleNamespace(
        messages=[], previews=[],
        flash=lambda message: instance._overlay.messages.append(message),
        show_preview=lambda text: instance._overlay.previews.append(text),
        hide=lambda: None,
    )
    instance._stats = types.SimpleNamespace(record_session=lambda _session: None)
    instance._export_queue = ExportQueue(tmp_path)
    instance._target_app = "TextEdit"
    instance._active_provider = "none"
    instance._cleanup_mode = fsmain.DEFAULT_MODE
    instance._mode = fsmain.MODE_DICTATION
    instance._selection = None
    instance._session_id = None
    instance._session_started_at = None
    instance._session_config = None
    instance._session_provider = None
    instance._session_cleanup_mode = None
    instance._session_started_monotonic = None
    instance._session_token = SessionToken()
    instance._state = fsmain.STATE_PROCESSING
    instance._state_lock = threading.RLock()
    instance._failed_markdown_export = None
    monkeypatch.setattr(instance, "_set_state", lambda _icon: None)
    monkeypatch.setattr(instance, "_refresh_history_menu", lambda: None)
    monkeypatch.setattr(fsmain, "load_words", lambda _directory: [])
    monkeypatch.setattr(fsmain, "load_snippets", lambda _directory: {})

    def late_result(*_args, **_kwargs):
        instance._session_token.cancel()
        return "Поздний результат"

    monkeypatch.setattr(fsmain, "format_text", late_result)
    monkeypatch.setattr(fsmain, "play_sound", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fsmain.feedback, "log_entry", lambda *_args, **_kwargs: None)
    paste_calls = []
    monkeypatch.setattr(fsmain, "insert_text", paste_calls.append)

    instance._process_audio()

    assert list(destination.glob("*.md")) == []
    assert paste_calls == []
    assert instance._overlay.messages == ["Отменено"]
    assert instance._overlay.previews == ["черновик"]


def _summary_app(tmp_path, *, provider="none", private=False):
    destination = tmp_path / "notes"
    destination.mkdir()
    providers = {}
    if provider != "none":
        providers[provider] = ProviderConfig(provider, "test-model", None, "key")
    instance = object.__new__(fsmain.FlowSpeechApp)
    instance._config = AppConfig(
        hotkey="right_option",
        whisper=WhisperConfig("small", "auto", "auto"),
        llm=LLMConfig(provider, providers),
        data_dir=tmp_path / "data",
        markdown_export=MarkdownExportConfig(True, destination, "daily"),
        private_mode=PrivateMode(enabled=private),
    )
    instance._overlay = types.SimpleNamespace(
        messages=[], flash=lambda message: instance._overlay.messages.append(message)
    )
    path = destination / f"{__import__('datetime').date.today():%Y-%m-%d}.md"
    path.write_text("# День\n\nИсходная запись\n", encoding="utf-8")
    return instance, path


def test_daily_summary_can_be_cancelled_before_cloud_transfer(tmp_path, monkeypatch):
    instance, path = _summary_app(tmp_path, provider="claude")
    original = path.read_text(encoding="utf-8")
    monkeypatch.setattr(fsmain.rumps, "alert", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        fsmain.formatter,
        "summarize_day",
        lambda *_args: (_ for _ in ()).throw(AssertionError("cloud call must not start")),
    )

    instance._on_daily_summary_click(None)

    assert path.read_text(encoding="utf-8") == original


def test_daily_summary_private_mode_never_uses_cloud(tmp_path, monkeypatch):
    instance, path = _summary_app(tmp_path, provider="claude", private=True)
    providers = []
    monkeypatch.setattr(
        fsmain.formatter,
        "summarize_day",
        lambda _text, provider: providers.append(provider) or ("- Итог", False),
    )
    response = types.SimpleNamespace(clicked=False, text="")
    monkeypatch.setattr(
        fsmain.rumps,
        "Window",
        lambda **_kwargs: types.SimpleNamespace(run=lambda: response),
        raising=False,
    )

    instance._on_daily_summary_click(None)

    assert providers == [None]
    assert "flowspeech_daily_summary_begin" not in path.read_text(encoding="utf-8")


def test_confirmed_daily_summary_preserves_source_and_saves_preview(tmp_path, monkeypatch):
    instance, path = _summary_app(tmp_path)
    response = types.SimpleNamespace(clicked=True, text="- Проверенный итог")
    monkeypatch.setattr(
        fsmain.rumps,
        "Window",
        lambda **_kwargs: types.SimpleNamespace(run=lambda: response),
        raising=False,
    )
    monkeypatch.setattr(
        fsmain.formatter,
        "summarize_day",
        lambda _text, _provider: ("- Черновик", False),
    )

    instance._on_daily_summary_click(None)

    content = path.read_text(encoding="utf-8")
    assert "Исходная запись" in content
    assert "Проверенный итог" in content
    assert content.count("flowspeech_daily_summary_begin") == 1
