"""FlowSpeech menu bar app: wires hotkey → recorder → whisper → LLM → paste."""

import logging
import os
import subprocess
import threading
import time
from dataclasses import replace
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

import rumps

from flowspeech import __version__, feedback, updates
from flowspeech.config import (
    AppConfig,
    ConfigError,
    PROVIDER_ENV_KEYS,
    load_config,
    save_hotkey,
    save_private_mode,
    save_provider,
    save_whisper_cloud,
)
from flowspeech import formatter
from flowspeech.config_manager import ConfigManager
from flowspeech.dictionary import (
    apply_snippets,
    ensure_dictionary,
    load_snippets,
    load_words,
    whisper_prompt,
)
from flowspeech.formatter import format_text, run_command
from flowspeech.hotkey import MultiHotkeyListener, is_accessibility_trusted
from flowspeech.injector import (
    capture_selection,
    copy_to_clipboard,
    frontmost_app_name,
    insert_text,
)
from flowspeech.license import BUY_URL, KIND_LICENSED, LicenseManager
from flowspeech.markdown_export import DictationExport, ExportStatus, MarkdownExporter
from flowspeech.overlay import Overlay
from flowspeech.recorder import Recorder
from flowspeech.stats import SessionRecord, StatsStore
from flowspeech.transcriber import Transcriber

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

def _assets_dir() -> Path:
    """Assets live next to the package in the repo, or in Resources in the .app."""
    resources = os.environ.get("RESOURCEPATH")  # set by py2app inside the bundle
    if resources:
        return Path(resources)
    return Path(__file__).parent.parent / "assets"


def _icon(state: str) -> str | None:
    path = _assets_dir() / f"menubar_{state}@2x.png"
    return str(path) if path.exists() else None


ICON_IDLE = _icon("idle") or "🎤"
ICON_RECORDING = _icon("recording") or "🔴"
ICON_PROCESSING = _icon("processing") or "⏳"
_HAS_ICON_FILES = _icon("idle") is not None

# A press shorter than this is a "tap": recording latches on until the next
# tap (toggle mode). Longer presses behave as classic push-to-talk.
TOGGLE_TAP_SECONDS = 0.35

# The dictation state machine. Every transition happens under _state_lock.
STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_PROCESSING = "processing"  # capture finished; whisper/LLM/paste running

# What the current capture is for. Set under _state_lock in _begin_recording
# and read by the worker thread while state is PROCESSING.
MODE_DICTATION = "dictation"
MODE_COMMAND = "command"  # SPEC.md §A1: spoken command applied to the selection

# Why a capture produced nothing. The user always gets told; a dictation that
# vanishes without a trace is the single worst failure mode for this app.
ABORT_MESSAGES = {
    "too_short": "⚠️ Слишком коротко — не успел записать",
    "silence": "🎙️ Микрофон молчит — дай доступ в настройках",
    "no_audio": "⚠️ Микрофон не отдал звук",
    "no_device": "⚠️ Не удалось открыть микрофон",
    "not_recording": "",  # nothing was running; stay quiet
    "empty": "⚠️ Речь не распознана",
}

MSG_COMMAND_NEEDS_LLM = "⚠️ Command Mode требует LLM — выбери провайдера в меню"
MSG_TRANSLATE_NEEDS_LLM = "⚠️ Перевод требует LLM — выбери провайдера в меню"
MSG_COMMAND_FAILED = "⚠️ Команда не выполнена — текст не тронут"
MSG_TRIAL_EXPIRED = "🔑 Триал закончился — введи ключ через меню FlowSpeech"
MSG_PRIVATE_ON = "🔒 Приватный режим включён — всё локально"
MSG_PRIVATE_ON_NO_OLLAMA = "🔒 Приватный режим включён (без Ollama — очистки не будет)"
MSG_PRIVATE_OFF = "🔓 Приватный режим выключен"
MSG_PRIVATE_RESTORE_FAILED = "⚠️ Не удалось восстановить прежние настройки"

PRIVATE_MODE_TITLE = "🔒 Приватный режим"

SOUND_START = "/System/Library/Sounds/Pop.aiff"
SOUND_DONE = "/System/Library/Sounds/Tink.aiff"
SOUND_START_VOLUME = 0.4
SOUND_DONE_VOLUME = 0.15  # barely audible confirmation

HISTORY_MENU_TITLE = "История диктовок"
HISTORY_LIMIT = 10
RETRY_MARKDOWN_EXPORT_TITLE = "Повторить сохранение Markdown"

MENU_ACTIVATE_IDLE = "🎙️  Начать запись"
MENU_ACTIVATE_RECORDING = "⏹️  Остановить запись"
MENU_ACTIVATE_PROCESSING = "⏳  Обработка…"

PROVIDER_LABELS = {
    "claude": "Claude",
    "openai": "OpenAI",
    "deepseek": "DeepSeek",
    "groq": "Groq (Llama 70B)",
    "ollama": "Ollama (локально)",
    "none": "Без очистки (сырой Whisper)",
}

# The neutral cleanup mode (SPEC.md §A3): empty prompt fragment. Used as the
# default active mode and shown first in the «Режим» submenu.
DEFAULT_MODE = "Обычный"

# Translate presets (SPEC.md §A4) share the «Режим» submenu; their menu titles
# are this prefix + the target language, which is how the pipeline recognises
# that the active mode is a translation rather than a plain cleanup mode.
TRANSLATE_PREFIX = "Перевод → "

HOTKEY_LABELS = {
    "right_option": "Правый ⌥ Option",
    "right_command": "Правый ⌘ Command",
    "right_shift": "Правый ⇧ Shift",
    "right_control": "Правый ⌃ Control",
    "f13": "F13",
    "f14": "F14",
    "f15": "F15",
}

ACCESSIBILITY_HELP = (
    "macOS не даёт приложению слышать горячую клавишу.\n\n"
    "1. Открой Системные настройки → Конфиденциальность и безопасность → "
    "Универсальный доступ.\n"
    "2. Добавь туда программу, из которой запускаешь FlowSpeech "
    "(Терминал, iTerm или VS Code), и включи галочку.\n"
    "3. Полностью перезапусти эту программу и запусти FlowSpeech снова."
)


def _on_main(block) -> None:
    """Run `block` on the main thread. AppKit (and therefore rumps) requires it."""
    from AppKit import NSOperationQueue

    NSOperationQueue.mainQueue().addOperationWithBlock_(block)


def play_sound(path: str, volume: float = 1.0) -> None:
    subprocess.Popen(
        ["afplay", "-v", str(volume), path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _bundle_app_path() -> str | None:
    """Path to FlowSpeech.app when running bundled, else None."""
    resources = os.environ.get("RESOURCEPATH")
    if not resources:
        return None
    return str(Path(resources).parents[1])  # …/FlowSpeech.app/Contents/Resources


def _login_item_enabled() -> bool:
    result = subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to get the name of every login item'],
        capture_output=True, text=True, check=False,
    )
    return "FlowSpeech" in result.stdout


def _set_login_item(enabled: bool) -> None:
    if enabled:
        app_path = _bundle_app_path()
        script = (
            'tell application "System Events" to make login item at end '
            f'with properties {{path:"{app_path}", hidden:false}}'
        )
    else:
        script = 'tell application "System Events" to delete login item "FlowSpeech"'
    subprocess.run(["osascript", "-e", script], capture_output=True, check=False)


class FlowSpeechApp(rumps.App):
    def __init__(self, config: AppConfig):
        if _HAS_ICON_FILES:
            super().__init__("FlowSpeech", icon=ICON_IDLE, template=True,
                             quit_button="Выйти")
        else:
            super().__init__(ICON_IDLE, quit_button="Выйти")
        self._config = config
        self._config_manager = ConfigManager(config)
        self._config_manager.subscribe(self._on_config_changed)
        self._active_provider = config.llm.provider
        # Active cleanup mode (SPEC.md §A3) is app state, not config: default to
        # "Обычный" if present, otherwise the first listed mode, else none.
        self._cleanup_mode = self._default_cleanup_mode(config)
        self._recorder = Recorder(tail_seconds=config.audio.tail_seconds)
        self._transcriber = Transcriber(config.whisper)
        self._stats = StatsStore(config.data_dir)
        self._apply_history_retention()
        self._license = LicenseManager(config.data_dir)
        self._overlay = Overlay()
        self._target_app = "unknown"
        self._failed_markdown_export: DictationExport | None = None

        # One lock guards the whole hotkey state machine. Hotkey callbacks
        # arrive on pynput's listener thread, menu clicks on the main thread.
        self._state_lock = threading.RLock()
        self._state = STATE_IDLE
        self._mode = MODE_DICTATION    # what the current capture is for
        self._selection = None         # selected text captured for Command Mode
        self._latched = False          # recording held on by a short tap
        self._swallow_release = False  # the release that ends a toggle tap
        self._press_started = 0.0

        self._active_hotkey = config.hotkey
        self._listener = None

        ensure_dictionary(config.data_dir)
        self._build_menu()
        self._start_listener(config.hotkey)

        # Load the Whisper model in the background so the first dictation is
        # fast. The microphone is opened per dictation, never ahead of time.
        threading.Thread(target=self._transcriber.warm_up, daemon=True).start()
        # Soft license revalidation: at most monthly, silent on any failure.
        self._license.revalidate_in_background()
        # Daily update check in the background, silent unless something's new.
        threading.Thread(target=self._update_check_on_start, daemon=True).start()

        # First run gets the onboarding window (permissions + a privacy note);
        # returning users who lost Accessibility still get the quick alert.
        from flowspeech.onboarding import onboarding_needed, show_onboarding

        if onboarding_needed(config.data_dir):
            show_onboarding(config.data_dir, HOTKEY_LABELS.get(config.hotkey, "правый ⌥"))
        elif not is_accessibility_trusted():
            rumps.alert("Нет разрешения Accessibility", ACCESSIBILITY_HELP)

    def _set_state(self, icon: str) -> None:
        """Switch the menu bar icon (or emoji title as a fallback).

        Callable from any thread: hotkey callbacks run on pynput's listener
        thread, and mutating a rumps/AppKit object off the main thread is
        undefined behaviour — it deadlocks or corrupts the menu bar item.
        """
        _on_main(lambda: self._apply_state_icon(icon))

    def _apply_state_icon(self, icon: str) -> None:
        if _HAS_ICON_FILES:
            self.icon = icon
        else:
            self.title = icon
        activate_item = getattr(self, "_activate_item", None)
        if activate_item is not None:
            if icon == ICON_RECORDING:
                activate_item.title = MENU_ACTIVATE_RECORDING
            elif icon == ICON_PROCESSING:
                activate_item.title = MENU_ACTIVATE_PROCESSING
            else:
                activate_item.title = MENU_ACTIVATE_IDLE
            if _HAS_ICON_FILES:
                activate_item.icon = icon

    def _start_listener(self, hotkey_name: str) -> None:
        if self._listener is not None:
            self._listener.stop()
        listener = MultiHotkeyListener()
        listener.bind(
            hotkey_name,
            on_press_start=self._on_hotkey_down,
            on_press_end=self._on_hotkey_up,
        )
        command_hotkey = self._config.command_hotkey
        if command_hotkey and command_hotkey != hotkey_name:
            listener.bind(
                command_hotkey,
                on_press_start=self._on_command_hotkey_down,
                on_press_end=self._on_command_hotkey_up,
            )
        elif command_hotkey:
            logger.warning(
                "command_hotkey collides with hotkey (%s); Command Mode disabled",
                hotkey_name,
            )
        listener.start()
        self._listener = listener
        self._active_hotkey = hotkey_name

    # --- Menu -------------------------------------------------------------

    @staticmethod
    def _default_cleanup_mode(config: AppConfig) -> str:
        """Pick the starting cleanup mode: "Обычный", else the first listed."""
        if not config.modes:
            return DEFAULT_MODE
        if DEFAULT_MODE in config.modes:
            return DEFAULT_MODE
        return next(iter(config.modes))

    def _mode_menu_titles(self) -> list[str]:
        """Cleanup modes, then translate presets, in «Режим» submenu order."""
        titles = list(self._config.modes)
        titles += [TRANSLATE_PREFIX + lang for lang in self._config.translate_to]
        return titles

    def _active_mode_fragment(self) -> tuple[str, bool]:
        """Resolve the active mode to (prompt fragment, is_translate).

        A translate preset is recognised by its menu title prefix; the
        language after it keys into config.translate_to.
        """
        name = self._cleanup_mode
        if name.startswith(TRANSLATE_PREFIX):
            lang = name[len(TRANSLATE_PREFIX):]
            return self._config.translate_to.get(lang, ""), True
        return self._config.modes.get(name, ""), False

    def _build_menu(self) -> None:
        provider_items = []
        available = list(self._config.llm.providers) + ["none"]
        for name in available:
            item = rumps.MenuItem(PROVIDER_LABELS[name], callback=self._on_provider_click)
            item.state = name == self._active_provider
            provider_items.append(item)
        mode_items = []
        for name in self._mode_menu_titles():
            item = rumps.MenuItem(name, callback=self._on_mode_click)
            item.state = name == self._cleanup_mode
            mode_items.append(item)
        hotkey_items = []
        for name, label in HOTKEY_LABELS.items():
            item = rumps.MenuItem(label, callback=self._on_hotkey_choice_click)
            item.state = name == self._active_hotkey
            hotkey_items.append(item)

        self._status_item = rumps.MenuItem(self._status_line())
        self._activate_item = rumps.MenuItem(
            MENU_ACTIVATE_IDLE,
            callback=self._on_activate_click,
            icon=ICON_IDLE if _HAS_ICON_FILES else None,
        )

        menu = [
            self._activate_item,
            None,
            self._status_item,
            None,
            rumps.MenuItem("Статистика за 7 дней", callback=self._on_stats_click),
            (HISTORY_MENU_TITLE, self._history_items()),
            rumps.MenuItem(
                RETRY_MARKDOWN_EXPORT_TITLE,
                callback=self._on_retry_markdown_export_click,
            ),
            None,
            ("Провайдер очистки", provider_items),
        ]
        # Only offer the mode picker when modes are configured — an empty
        # submenu is worse than no submenu.
        if mode_items:
            menu.append(("Режим", mode_items))
        self._private_item = rumps.MenuItem(
            PRIVATE_MODE_TITLE, callback=self._on_private_mode_click
        )
        self._private_item.state = self._config.private_mode.enabled
        menu += [
            self._private_item,
            ("Горячая клавиша", hotkey_items),
            rumps.MenuItem("Настройки…", callback=self._on_settings_click),
            rumps.MenuItem("Проверить обновления", callback=self._on_check_updates_click),
        ]
        license_status = self._license.status()
        self._license_item = rumps.MenuItem(
            license_status.detail,
            callback=None if license_status.kind == KIND_LICENSED
            else self._on_license_click,
        )
        menu.extend([None, self._license_item])
        if _bundle_app_path():
            login_item = rumps.MenuItem(
                "Запускать при входе", callback=self._on_login_item_click
            )
            login_item.state = _login_item_enabled()
            menu.append(login_item)
        menu.append(None)
        self.menu = menu

    def _status_line(self) -> str:
        today = self._stats.summary(days=1)
        return f"Сегодня: {today.total_sessions} диктовок · {today.total_words} слов"

    def _on_settings_click(self, _sender) -> None:
        from flowspeech.settings import show_settings

        show_settings(self._config, self._stats, self._config_manager)

    def _on_license_click(self, _sender) -> None:
        """Ask for a license key. rumps.Window is a modal text prompt —
        good enough for a once-per-purchase interaction."""
        window = rumps.Window(
            title="Активация FlowSpeech",
            message=(
                "Вставь лицензионный ключ из письма о покупке.\n"
                f"Купить: {BUY_URL}\n\n"
                "Для активации нужен интернет (один раз)."
            ),
            default_text="",
            ok="Активировать",
            cancel="Отмена",
            dimensions=(320, 24),
        )
        response = window.run()
        if not response.clicked or not response.text.strip():
            return
        ok, message = self._license.activate(response.text)
        rumps.alert("Активация", message)
        if ok:
            status = self._license.status()
            self._license_item.title = status.detail
            self._license_item.set_callback(None)

    def _on_login_item_click(self, sender: rumps.MenuItem) -> None:
        _set_login_item(not sender.state)
        sender.state = _login_item_enabled()

    def _history_items(self) -> list[rumps.MenuItem]:
        """Menu items for recent dictations; clicking one copies its text."""
        items = []
        for created_at, app_name, clean_text in self._stats.recent_sessions(HISTORY_LIMIT):
            time_label = created_at[11:16]  # HH:MM from ISO timestamp
            preview = clean_text if len(clean_text) <= 50 else clean_text[:50] + "…"
            item = rumps.MenuItem(
                f"{time_label} · {app_name} · {preview}",
                callback=self._on_history_click,
            )
            item._flowspeech_text = clean_text
            items.append(item)
        if not items:
            items.append(rumps.MenuItem("Пока пусто"))
        return items

    def _refresh_history_menu(self) -> None:
        def apply():
            submenu = self.menu[HISTORY_MENU_TITLE]
            submenu.clear()
            for item in self._history_items():
                submenu.add(item)
            self._status_item.title = self._status_line()

        _on_main(apply)  # menu mutation must happen on the main thread

    def _on_history_click(self, sender: rumps.MenuItem) -> None:
        text = getattr(sender, "_flowspeech_text", "")
        if not text:
            return
        if copy_to_clipboard(text):
            self._overlay.flash("📋 Скопировано в буфер")
        else:
            self._overlay.flash("⚠️ Не удалось скопировать")

    def _export_markdown(self, snapshot: DictationExport):
        """Export separately from paste; preserve one failed snapshot for retry."""
        result = MarkdownExporter(snapshot.destination).export(snapshot)
        if result.status is ExportStatus.FAILED:
            self._failed_markdown_export = snapshot
        elif result.status is ExportStatus.SAVED:
            self._failed_markdown_export = None
        return result

    def _on_retry_markdown_export_click(self, _sender) -> None:
        snapshot = self._failed_markdown_export
        if snapshot is None:
            self._overlay.flash("Нет Markdown-заметки для повтора")
            return
        destination = self._config.markdown_export
        if not destination.enabled:
            self._overlay.flash("Включи сохранение Markdown в настройках")
            return

        result = self._export_markdown(replace(snapshot, destination=destination))
        if result.status is ExportStatus.SAVED:
            self._overlay.flash("💾 Markdown-заметка сохранена")
        else:
            self._overlay.flash("⚠️ Markdown-заметка всё ещё не сохранена")

    def _on_hotkey_choice_click(self, sender: rumps.MenuItem) -> None:
        name = next(n for n, label in HOTKEY_LABELS.items() if label == sender.title)
        if name == self._active_hotkey:
            return
        self._start_listener(name)
        save_hotkey(name)  # persist so the choice survives restarts
        for item in self.menu["Горячая клавиша"].values():
            item.state = item.title == sender.title
        logger.info("Hotkey switched to %s", name)

    def _on_provider_click(self, sender: rumps.MenuItem) -> None:
        name = next(n for n, label in PROVIDER_LABELS.items() if label == sender.title)
        provider = self._config.llm.providers.get(name)
        env_key = PROVIDER_ENV_KEYS.get(name)
        if name != "none" and env_key and (provider is None or not provider.api_key):
            rumps.alert(
                "Нет API-ключа",
                f"Для «{sender.title}» нужна переменная окружения {env_key}. "
                "Добавь её в .env и перезапусти приложение.",
            )
            return
        self._active_provider = name
        for item in self.menu["Провайдер очистки"].values():
            item.state = item.title == sender.title
        logger.info("Provider switched to %s", name)

    def _on_mode_click(self, sender: rumps.MenuItem) -> None:
        # The menu title IS the mode name (unlike providers, which map a label
        # to an id). Mode is app state only, so nothing is persisted.
        if sender.title == self._cleanup_mode:
            return
        self._cleanup_mode = sender.title
        for item in self.menu["Режим"].values():
            item.state = item.title == sender.title
        logger.info("Cleanup mode switched to %s", sender.title)

    def _sync_provider_menu(self) -> None:
        """Reflect self._active_provider in the «Провайдер очистки» checkmarks."""
        label = PROVIDER_LABELS.get(self._active_provider)
        for item in self.menu["Провайдер очистки"].values():
            item.state = item.title == label

    @staticmethod
    def _ollama_available(provider) -> bool:
        """True if the local Ollama server accepts a connection (fast probe).

        Private mode must degrade to raw transcript, not hang: a 0.5 s socket
        connect to the configured Ollama endpoint decides whether to force the
        provider to "ollama" or fall back to "none".
        """
        import socket
        import urllib.parse

        try:
            url = urllib.parse.urlparse(provider.base_url or "")
            host = url.hostname or "localhost"
            port = url.port or 11434
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except Exception:
            return False

    def _on_private_mode_click(self, sender: rumps.MenuItem) -> None:
        if self._config.private_mode.enabled:
            self._disable_private_mode(sender)
        else:
            self._enable_private_mode(sender)

    def _enable_private_mode(self, sender: rumps.MenuItem) -> None:
        # Remember what to restore, then force offline values. Ollama is local;
        # if it isn't answering, fall back to "none" so cleanup degrades to the
        # raw transcript rather than silently doing nothing.
        saved_cloud = self._config.whisper.cloud
        saved_provider = self._active_provider
        ollama = self._config.llm.providers.get("ollama")
        forced = "ollama" if (ollama and self._ollama_available(ollama)) else "none"

        save_whisper_cloud("none")
        save_provider(forced)
        save_private_mode(True, saved_cloud, saved_provider)
        try:
            self._config_manager.reload()
        except ConfigError:
            logger.exception("Enabling private mode failed")
            self._overlay.flash(MSG_PRIVATE_RESTORE_FAILED)
            return
        self._active_provider = forced
        self._sync_provider_menu()
        sender.state = True
        self._overlay.flash(MSG_PRIVATE_ON if forced == "ollama" else MSG_PRIVATE_ON_NO_OLLAMA)
        logger.info("Private mode ON (provider forced to %s)", forced)

    def _disable_private_mode(self, sender: rumps.MenuItem) -> None:
        pm = self._config.private_mode
        # Never restore a cloud provider whose key is now missing — that would
        # make the next launch fail load_config. Fall back to "none" instead.
        provider = pm.saved_provider
        env_key = PROVIDER_ENV_KEYS.get(provider)
        if env_key and not os.environ.get(env_key):
            logger.warning("Saved provider %s has no key; restoring to none", provider)
            provider = "none"

        save_whisper_cloud(pm.saved_cloud)
        save_provider(provider)
        save_private_mode(False, pm.saved_cloud, provider)
        try:
            self._config_manager.reload()
        except ConfigError:
            logger.exception("Disabling private mode failed")
            self._overlay.flash(MSG_PRIVATE_RESTORE_FAILED)
            return
        self._active_provider = provider
        self._sync_provider_menu()
        sender.state = False
        self._overlay.flash(MSG_PRIVATE_OFF)
        logger.info("Private mode OFF (provider restored to %s)", provider)

    def _on_stats_click(self, _sender) -> None:
        summary = self._stats.summary(days=7)
        top = ", ".join(f"{word} ({count})" for word, count in summary.top_words[:10])
        rumps.alert(
            "Статистика за 7 дней",
            f"Диктовок: {summary.total_sessions}\n"
            f"Слов: {summary.total_words}\n"
            f"Время речи: {summary.total_speaking_sec / 60:.1f} мин\n"
            f"Средняя скорость: {summary.average_wpm} слов/мин\n\n"
            f"Топ слов: {top or '—'}",
        )

    # --- Dictation pipeline -------------------------------------------------

    def _begin_recording(self, mode: str = MODE_DICTATION) -> None:
        """Start capturing. Caller holds _state_lock and has checked the state.

        The recorder goes first. Showing the overlay, resolving the frontmost
        app and forking `afplay` together cost a few hundred milliseconds, and
        anything spoken during them used to be lost.
        """
        if not self._license.status().can_dictate:
            # Trial over, no key: dictation is gated but the app never turns
            # into a silent pumpkin — the menu and activation keep working
            # and the user is told exactly what to do (SPEC.md §C1).
            self._overlay.flash(MSG_TRIAL_EXPIRED)
            return
        if mode == MODE_COMMAND and self._active_provider == "none":
            # Command Mode is an LLM feature by definition; without a provider
            # there is nothing to run the command. Tell the user, touch nothing.
            self._overlay.flash(MSG_COMMAND_NEEDS_LLM)
            return
        try:
            self._recorder.start()
        except Exception:
            logger.exception("Recording could not start")
            self._state = STATE_IDLE
            self._overlay.flash(ABORT_MESSAGES["no_device"])
            return
        self._state = STATE_RECORDING
        self._mode = mode
        self._selection = None
        self._press_started = time.time()
        self._target_app = frontmost_app_name()
        if mode == MODE_COMMAND:
            # Grab the selection while the user is already speaking; the
            # recorder is running, so no audio is lost during the ~50-500 ms
            # the frontmost app takes to service the synthetic Cmd+C.
            self._selection = capture_selection()
            logger.info(
                "Command Mode: selection %s",
                f"{len(self._selection)} chars" if self._selection else "empty",
            )
        self._set_state(ICON_RECORDING)
        self._overlay.show_recording(level_source=lambda: self._recorder.level)
        play_sound(SOUND_START, SOUND_START_VOLUME)

    def _on_activate_click(self, _sender) -> None:
        """Click on the menu-bar item: start, or stop a running recording."""
        with self._state_lock:
            if self._state == STATE_PROCESSING:
                return  # a dictation is already being transcribed/cleaned up
            if self._state == STATE_RECORDING:
                self._finish_recording()
                return
            self._begin_recording()
            # A click always latches; the next one stops. Unless the mic
            # refused to open, in which case we are back at IDLE.
            self._latched = self._state == STATE_RECORDING

    def _on_hotkey_down(self) -> None:
        self._hotkey_down(MODE_DICTATION)

    def _on_hotkey_up(self) -> None:
        self._hotkey_up()

    def _on_command_hotkey_down(self) -> None:
        self._hotkey_down(MODE_COMMAND)

    def _on_command_hotkey_up(self) -> None:
        self._hotkey_up()

    def _hotkey_down(self, mode: str) -> None:
        with self._state_lock:
            if self._state == STATE_PROCESSING:
                return
            if self._state == STATE_RECORDING:
                # A tap while a latched recording runs: stop and process. The
                # matching release must NOT be read as the end of a push-to-talk
                # hold, or _finish_recording would run twice — the second call
                # reset the icon and hid the overlay mid-processing. Either
                # hotkey stops a running recording — its mode was fixed at start.
                self._swallow_release = True
                self._finish_recording()
                return
            self._swallow_release = False
            self._latched = False
            self._begin_recording(mode)

    def _hotkey_up(self) -> None:
        with self._state_lock:
            if self._swallow_release:
                self._swallow_release = False
                return
            if self._state != STATE_RECORDING:
                return
            if self._latched:
                return  # recording is latched; the next tap will stop it
            if time.time() - self._press_started < TOGGLE_TAP_SECONDS:
                # Short tap → latch recording on until the next tap.
                self._latched = True
                logger.info("Recording latched (tap); tap again to stop")
                return
            self._finish_recording()

    def _finish_recording(self) -> None:
        """Hand the capture to a worker. Caller holds _state_lock.

        Returns immediately: Recorder.stop() sleeps to drain the audio tail and
        must never block pynput's listener thread.
        """
        if self._state != STATE_RECORDING:
            return
        self._state = STATE_PROCESSING
        self._latched = False
        self._set_state(ICON_PROCESSING)
        self._overlay.show_processing()
        threading.Thread(target=self._process_audio, daemon=True).start()

    def _abort(self, reason: str) -> None:
        message = ABORT_MESSAGES.get(reason, ABORT_MESSAGES["empty"])
        if message:
            self._overlay.flash(message)
        else:
            self._overlay.hide()

    def _process_audio(self) -> None:
        # State is PROCESSING for the whole of this method, so no second
        # dictation can start and interleave its paste with ours.
        try:
            capture = self._recorder.stop()
            logger.info(
                "Capture: %.2fs, rms=%.5f, reason=%s",
                capture.duration_sec, capture.rms, capture.reason or "ok",
            )
            if not capture.ok:
                self._abort(capture.reason)
                return
            audio = capture.audio

            words = load_words(self._config.data_dir)
            whisper_started = time.time()
            transcript = self._transcriber.transcribe(audio, whisper_prompt(words))
            whisper_seconds = time.time() - whisper_started
            if not transcript.text:
                logger.info("Empty transcript, nothing to insert")
                self._abort("empty")
                return

            if self._mode == MODE_COMMAND:
                self._run_command(transcript, whisper_seconds)
                return

            provider = None
            if self._active_provider != "none":
                provider = self._config.llm.providers[self._active_provider]
            style = self._config.app_style_for(self._target_app)
            mode, is_translate = self._active_mode_fragment()
            llm_started = time.time()
            clean = format_text(
                transcript.text, provider, words, self._target_app, style, mode,
                translate=is_translate,
            )
            llm_seconds = time.time() - llm_started

            # Snippet expansion (SPEC §A5) runs on the final text, after any
            # LLM cleanup and regardless of provider — only for dictation, not
            # Command Mode (that path returns earlier).
            clean = apply_snippets(clean, load_snippets(self._config.data_dir))

            created_at = datetime.now()
            export_snapshot = DictationExport.create(
                session_id=uuid4(),
                created_at=created_at,
                final_text=clean,
                destination=self._config.markdown_export,
            )
            export_result = self._export_markdown(export_snapshot)

            paste_failed = False
            try:
                insert_text(clean)
            except Exception:
                paste_failed = True
                logger.exception("Text insertion failed; dictation remains exportable")

            if export_result.status is ExportStatus.FAILED:
                self._overlay.flash("⚠️ Markdown не сохранён - выбери «Повторить сохранение Markdown»")
            elif paste_failed:
                if export_result.status is ExportStatus.SAVED:
                    self._overlay.flash("⚠️ Вставка не выполнена, Markdown сохранён")
                else:
                    self._overlay.flash("⚠️ Вставка не выполнена")
            elif is_translate and provider is None:
                # Nothing to translate with: the raw transcript was inserted so
                # the dictation isn't lost; tell the user why it wasn't translated.
                self._overlay.flash(MSG_TRANSLATE_NEEDS_LLM)
            else:
                self._overlay.hide()
            play_sound(SOUND_DONE, SOUND_DONE_VOLUME)
            logger.info(
                "Timing: whisper %.1fs, llm(%s) %.1fs, %d chars",
                whisper_seconds, self._active_provider, llm_seconds, len(clean),
            )
            logger.debug("Inserted text → %r", clean)

            self._stats.record_session(SessionRecord(
                created_at=created_at,
                duration_sec=transcript.duration_sec,
                raw_text=transcript.text,
                clean_text=clean,
                app_name=self._target_app,
                provider=self._active_provider,
                language=transcript.language,
            ))
            feedback.log_entry(
                self._config.data_dir, transcript.text, clean,
                self._active_provider, self._target_app, transcript.language,
            )
            self._refresh_history_menu()
        except Exception:
            logger.exception("Dictation pipeline failed")
            self._overlay.flash("⚠️ Ошибка — смотри лог в терминале")
        finally:
            with self._state_lock:
                self._state = STATE_IDLE
                self._mode = MODE_DICTATION
                self._selection = None
            self._set_state(ICON_IDLE)

    def _run_command(self, transcript, whisper_seconds: float) -> None:
        """Command Mode tail of the pipeline. The one hard rule (SPEC.md §A1):
        on ANY failure the user's selection stays untouched."""
        provider = self._config.llm.providers.get(self._active_provider)
        if provider is None:  # provider switched to "none" mid-recording
            self._overlay.flash(MSG_COMMAND_NEEDS_LLM)
            return

        selection = self._selection
        llm_started = time.time()
        result = run_command(transcript.text, selection, provider, self._target_app)
        llm_seconds = time.time() - llm_started
        if result is None:
            self._overlay.flash(MSG_COMMAND_FAILED)
            return

        # Pasting replaces the still-active selection (or inserts at the
        # cursor when nothing was selected).
        insert_text(result)
        self._overlay.hide()
        play_sound(SOUND_DONE, SOUND_DONE_VOLUME)
        logger.info(
            "Command Mode timing: whisper %.1fs, llm(%s) %.1fs, "
            "command %d words, selection %d chars → %d chars",
            whisper_seconds, self._active_provider, llm_seconds,
            len(transcript.text.split()), len(selection or ""), len(result),
        )
        self._stats.record_session(SessionRecord(
            created_at=datetime.now(),
            duration_sec=transcript.duration_sec,
            raw_text=transcript.text,
            clean_text=result,
            app_name=self._target_app,
            provider=f"command/{self._active_provider}",
            language=transcript.language,
        ))
        self._refresh_history_menu()

    def _apply_history_retention(self) -> None:
        """Rotate stored dictation texts on start (SPEC.md §4.2).

        0 = forget all texts, N = drop texts older than N days, -1 = keep all.
        Aggregate stats always survive (purge_texts blanks text but keeps the
        counts). Never fatal — a rotation error must not stop the app.
        """
        days = self._config.history_retention_days
        try:
            if days == 0:
                self._stats.purge_texts()
            elif days > 0:
                self._stats.purge_older_than(days)
            feedback.rotate(self._config.data_dir, days)
        except Exception:
            logger.exception("History retention pass failed")

    # --- Updates (SPEC.md §C4) ---------------------------------------------

    def _update_check_on_start(self) -> None:
        """Once-a-day silent check, throttled by a stamp file in the data dir."""
        try:
            if updates.due_for_check(self._config.data_dir):
                self._run_update_check(manual=False)
                updates.record_check(self._config.data_dir)
        except Exception:
            logger.exception("Background update check failed")

    def _on_check_updates_click(self, _sender) -> None:
        self._overlay.flash("🔄 Проверяю обновления…")
        threading.Thread(
            target=lambda: self._run_update_check(manual=True), daemon=True
        ).start()

    def _run_update_check(self, manual: bool) -> None:
        """Compare versions; on a newer one, notify and open the download page.

        `manual` only changes the no-update feedback: the background check stays
        silent, the menu action confirms it looked.
        """
        info = updates.check_for_update(__version__)
        if info is None:
            if manual:
                self._overlay.flash("✓ Установлена последняя версия")
            return

        def announce():
            rumps.notification(
                "FlowSpeech", "Доступно обновление",
                f"Версия {info.version} — открываю страницу загрузки",
            )
            if info.url:
                subprocess.Popen(
                    ["open", info.url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )

        _on_main(announce)
        logger.info("Update available: %s", info.version)

    def _on_config_changed(self, config) -> None:
        """ConfigManager subscriber: new keys/engine apply without a restart."""
        self._config = config
        self._transcriber.update_config(config.whisper)
        formatter.reset_clients()


def setup_file_logging(data_dir: Path) -> RotatingFileHandler | None:
    """In .app mode, also write logs to ~/.flowspeech/logs/flowspeech.log.

    Only when bundled (py2app sets RESOURCEPATH): a terminal run already prints
    to stderr, and there is no console for a menu-bar .app. Capped at 3 files ×
    1 MB so it never grows without bound. Full dictation texts are logged at
    DEBUG, so at the default INFO level this file records timings and lengths
    but never what was dictated. Returns the handler, or None when skipped.
    """
    if not os.environ.get("RESOURCEPATH"):
        return None
    log_dir = Path(data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "flowspeech.log",
        maxBytes=1_000_000, backupCount=2, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    return handler


def main() -> None:
    try:
        config = load_config()
    except ConfigError as error:
        rumps.alert("FlowSpeech: ошибка конфигурации", str(error))
        raise SystemExit(1)
    setup_file_logging(config.data_dir)
    FlowSpeechApp(config).run()


if __name__ == "__main__":
    main()
