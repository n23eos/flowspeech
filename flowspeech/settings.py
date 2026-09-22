"""Native settings window: General / Dictionary / History / Keys / Statistics.

Laid out with Auto Layout via the helpers in uikit.py (NSStackView, NSGridView,
grouped sections, semantic label colors) so it reads like a System Settings
pane rather than hand-placed rectangles. Everything runs on the main thread;
the «Проверить» key round-trip hops to a background thread and back.
"""

import logging
import threading

import objc
from Foundation import NSMakeRect, NSObject

from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSBackingStoreBuffered,
    NSFont,
    NSModalResponseOK,
    NSOpenPanel,
    NSLayoutConstraint,
    NSScrollView,
    NSSearchField,
    NSSecureTextField,
    NSSegmentedControl,
    NSTableColumn,
    NSTableView,
    NSTabView,
    NSTabViewItem,
    NSTextField,
    NSTextView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskTitled,
)

from flowspeech import secrets, uikit as ui
from flowspeech.config import AppConfig, config_path, save_markdown_export, save_whisper_cloud
from flowspeech.config_manager import ConfigManager
from flowspeech.dictionary import dictionary_path, ensure_dictionary
from flowspeech.formatter import verify_provider
from flowspeech.markdown_export import validate_export_directory
from flowspeech.stats import StatsStore

logger = logging.getLogger(__name__)

WIDTH, HEIGHT = 680, 520
BODY_WIDTH = WIDTH - 2 * ui.MARGIN  # wrap width for multiline captions

# LLM API-key fields (env var → human label). Ollama is local and keyless.
LLM_KEY_ROWS = (
    ("ANTHROPIC_API_KEY", "Claude (Anthropic)"),
    ("OPENAI_API_KEY", "OpenAI"),
    ("DEEPSEEK_API_KEY", "DeepSeek"),
    ("GROQ_API_KEY", "Groq"),
)

_window = None  # keep a reference so the window isn't garbage-collected


def _on_main(block) -> None:
    from AppKit import NSOperationQueue

    NSOperationQueue.mainQueue().addOperationWithBlock_(block)


def _key_placeholder(has_key: bool) -> str:
    return "•••••••••••• сохранён" if has_key else "не задан"


def _tab(identifier: str, label: str) -> NSTabViewItem:
    item = NSTabViewItem.alloc().initWithIdentifier_(identifier)
    item.setLabel_(label)
    return item


def _scroll_text(*, editable: bool, monospaced: bool = True):
    """An NSScrollView wrapping an NSTextView, ready for Auto Layout."""
    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, BODY_WIDTH, 200))
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setBorderType_(2)  # NSBezelBorder — the standard framed editor look
    scroll.setTranslatesAutoresizingMaskIntoConstraints_(False)
    text = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, BODY_WIDTH, 200))
    if monospaced:
        text.setFont_(NSFont.userFixedPitchFontOfSize_(13))
    else:
        text.setFont_(NSFont.systemFontOfSize_(13))
    text.setRichText_(False)
    text.setEditable_(editable)
    text.setAutomaticQuoteSubstitutionEnabled_(False)
    scroll.setDocumentView_(text)
    return scroll, text


# --- General ---------------------------------------------------------------

def _general_tab(config: AppConfig) -> NSTabViewItem:
    item = _tab("general", "Общие")

    form = ui.form([
        ("Модель Whisper", ui.label(f"{config.whisper.model} · язык {config.whisper.language}")),
        ("Облачный ASR", ui.label("Groq" if config.whisper.cloud == "groq" else "локально")),
        ("Провайдер очистки", ui.label(config.llm.provider)),
        ("Горячая клавиша", ui.label(config.hotkey)),
    ])

    config_path_label = ui.wrapping(ui.secondary(str(config_path().resolve())), BODY_WIDTH)
    data_path_label = ui.wrapping(ui.secondary(str(config.data_dir)), BODY_WIDTH)
    paths = ui.vstack([
        ui.secondary("Файл конфигурации", size=11),
        config_path_label,
        data_path_label,
    ], spacing=3)

    hint = ui.wrapping(
        ui.secondary("Провайдер, режим и горячая клавиша переключаются в меню-баре. "
                     "Остальное — в config.yaml."),
        BODY_WIDTH,
    )

    body = ui.vstack(
        [ui.title("Обзор"), form, ui.divider(), paths, hint],
        spacing=ui.SECTION_SPACING, fill=True,
    )
    ui.pin(body, item.view())
    return item


# --- Dictionary ------------------------------------------------------------

def _dictionary_tab(config: AppConfig) -> NSTabViewItem:
    item = _tab("dictionary", "Словарь")
    view = item.view()

    path = ensure_dictionary(config.data_dir)
    scroll, text = _scroll_text(editable=True)
    text.setString_(path.read_text(encoding="utf-8"))

    header = ui.vstack([
        ui.title("Личный словарь"),
        ui.wrapping(ui.secondary("Слова, имена и термины, которые Whisper коверкает — "
                                 "по одному на строку. Плюс секции snippets: для замен."),
                    BODY_WIDTH),
    ], spacing=4, fill=True)

    status = ui.secondary("")
    save = ui.push_button("Сохранить", None, default=True)
    footer = ui.hstack([save, status])

    def on_save(_sender):
        try:
            dictionary_path(config.data_dir).write_text(text.string(), encoding="utf-8")
            status.setStringValue_("Сохранено ✓")
        except Exception as error:
            logger.exception("Failed to save dictionary")
            status.setStringValue_(f"Ошибка: {error}")

    ui.on_action(save, on_save)
    ui.pin_column(view, header, scroll, footer)
    return item


# --- History ---------------------------------------------------------------

class _HistoryDataSource(NSObject):
    """NSTableView data source over StatsStore.search() results."""

    def initWithStore_(self, store):
        self = objc.super(_HistoryDataSource, self).init()
        if self is None:
            return None
        self._store = store
        self._rows = ()
        return self

    def reloadWithQuery_(self, query):
        self._rows = self._store.search(query or "")

    def rowIdAt_(self, index):
        return self._rows[index][0]

    def numberOfRowsInTableView_(self, table):
        return len(self._rows)

    def tableView_objectValueForTableColumn_row_(self, table, column, row):
        _id, created_at, app_name, clean_text = self._rows[row]
        ident = str(column.identifier())
        if ident == "time":
            return created_at[:16].replace("T", " ")
        if ident == "app":
            return app_name
        return clean_text


_history_sources = []  # keep data sources alive (NSTableView holds a weak ref)


def _history_tab(config: AppConfig, stats: StatsStore) -> NSTabViewItem:
    item = _tab("history", "История")
    view = item.view()

    source = _HistoryDataSource.alloc().initWithStore_(stats)
    source.reloadWithQuery_("")
    _history_sources.append(source)

    search = NSSearchField.alloc().initWithFrame_(NSMakeRect(0, 0, BODY_WIDTH, 24))
    search.setTranslatesAutoresizingMaskIntoConstraints_(False)
    search.setPlaceholderString_("Поиск по диктовкам")

    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, BODY_WIDTH, 240))
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setBorderType_(2)
    scroll.setTranslatesAutoresizingMaskIntoConstraints_(False)
    table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, BODY_WIDTH, 240))
    table.setUsesAlternatingRowBackgroundColors_(True)
    table.setRowHeight_(22)
    for ident, title, width in (
        ("time", "Время", 130),
        ("app", "Приложение", 130),
        ("text", "Текст", BODY_WIDTH - 300),
    ):
        col = NSTableColumn.alloc().initWithIdentifier_(ident)
        col.headerCell().setStringValue_(title)
        col.setWidth_(width)
        col.setEditable_(False)
        table.addTableColumn_(col)
    table.setDataSource_(source)
    scroll.setDocumentView_(table)

    header = ui.vstack([ui.title("История диктовок"), search], spacing=8, fill=True)

    status = ui.secondary("")
    delete_btn = ui.push_button("Удалить")
    clear_btn = ui.push_button("Очистить историю…")
    footer = ui.hstack([delete_btn, clear_btn, status])

    def reload():
        source.reloadWithQuery_(search.stringValue())
        table.reloadData()

    def on_search(_sender):
        reload()

    def on_delete(_sender):
        row = table.selectedRow()
        if row < 0:
            status.setStringValue_("Выбери строку в таблице")
            return
        stats.delete_session(source.rowIdAt_(row))
        reload()
        status.setStringValue_("Удалено ✓")

    def on_clear(_sender):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Очистить историю диктовок?")
        alert.setInformativeText_("Тексты будут удалены безвозвратно. "
                                  "Статистика (слова, скорость) сохранится.")
        alert.addButtonWithTitle_("Очистить")
        alert.addButtonWithTitle_("Отмена")
        if alert.runModal() == NSAlertFirstButtonReturn:
            stats.purge_texts()
            reload()
            status.setStringValue_("История очищена ✓")

    ui.on_action(search, on_search)
    ui.on_action(delete_btn, on_delete)
    ui.on_action(clear_btn, on_clear)
    ui.pin_column(view, header, scroll, footer)
    return item


# --- Keys ------------------------------------------------------------------

def _keys_tab(config: AppConfig, config_manager: ConfigManager) -> NSTabViewItem:
    item = _tab("keys", "Ключи")

    existing = secrets.read_keys(config.data_dir)
    field_map: dict[str, object] = {}

    rows = []
    for env_key, label in LLM_KEY_ROWS:
        field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 320, 22))
        field.setPlaceholderString_(_key_placeholder(env_key in existing))
        field.setTranslatesAutoresizingMaskIntoConstraints_(False)
        NSLayoutConstraint.activateConstraints_([
            field.widthAnchor().constraintEqualToConstant_(320),
        ])
        field_map[env_key] = field
        rows.append((label, field))
    keys_form = ui.form(rows)

    asr = NSSegmentedControl.alloc().initWithFrame_(NSMakeRect(0, 0, 240, 24))
    asr.setSegmentCount_(2)
    asr.setLabel_forSegment_("Локально", 0)
    asr.setLabel_forSegment_("Groq (облако)", 1)
    asr.setSelectedSegment_(0 if config.whisper.cloud == "none" else 1)
    asr.setTranslatesAutoresizingMaskIntoConstraints_(False)

    status = ui.secondary("")
    save = ui.push_button("Сохранить ключи", None, default=True)
    check = ui.push_button("Проверить")
    footer = ui.hstack([save, check, status])

    def on_save(_sender):
        updates = {
            env_key: field.stringValue().strip()
            for env_key, field in field_map.items()
            if field.stringValue().strip()
        }
        if not updates:
            status.setStringValue_("Введите ключ, чтобы сохранить")
            return
        try:
            config_manager.apply_keys(updates)
        except Exception:
            logger.exception("Saving API keys failed")
            status.setStringValue_("Ошибка сохранения ключей")
            return
        refreshed = secrets.read_keys(config.data_dir)
        for env_key, field in field_map.items():
            field.setStringValue_("")
            field.setPlaceholderString_(_key_placeholder(env_key in refreshed))
        status.setStringValue_("Ключи сохранены ✓")

    def on_asr_change(sender):
        value = "none" if sender.selectedSegment() == 0 else "groq"
        try:
            save_whisper_cloud(value)
            config_manager.reload()
        except Exception:
            logger.exception("Saving ASR engine failed")
            status.setStringValue_("Ошибка сохранения движка ASR")
            return
        status.setStringValue_("Движок ASR: " + ("локально ✓" if value == "none" else "Groq ✓"))

    def on_check(_sender):
        status.setStringValue_("Проверяю…")

        def work():
            provider = config_manager.config.llm.active()
            if provider is None:
                _on_main(lambda: status.setStringValue_("Провайдер очистки отключён (none)"))
                return
            _, message = verify_provider(provider)
            _on_main(lambda: status.setStringValue_(message))

        threading.Thread(target=work, daemon=True).start()

    ui.on_action(save, on_save)
    ui.on_action(check, on_check)
    ui.on_action(asr, on_asr_change)

    body = ui.vstack([
        ui.title("Ключи API"),
        ui.wrapping(ui.secondary("Ключи хранятся в ~/.flowspeech/.env (0600), не в config.yaml. "
                                 "Пустое поле — оставить сохранённый ключ."), BODY_WIDTH),
        keys_form,
        ui.divider(),
        ui.section("Движок распознавания (ASR)", asr),
        footer,
    ], spacing=ui.SECTION_SPACING, fill=True)
    ui.pin(body, item.view())
    return item


# --- Statistics ------------------------------------------------------------

def _stats_tab(stats: StatsStore) -> NSTabViewItem:
    item = _tab("stats", "Статистика")

    lines = []
    for days, title in ((7, "За 7 дней"), (30, "За 30 дней")):
        s = stats.summary(days=days)
        top = ", ".join(f"{w} ({c})" for w, c in s.top_words[:8]) or "—"
        apps = ", ".join(f"{a} ({c})" for a, c in s.words_by_app[:5]) or "—"
        lines.append(
            f"{title}\n"
            f"  Диктовок: {s.total_sessions}   Слов: {s.total_words}\n"
            f"  Время речи: {s.total_speaking_sec / 60:.1f} мин   "
            f"Скорость: {s.average_wpm} слов/мин\n"
            f"  Топ слов: {top}\n"
            f"  По приложениям: {apps}\n"
        )

    scroll, text = _scroll_text(editable=False, monospaced=False)
    text.setString_("\n".join(lines))

    ui.pin_header_body(item.view(), ui.title("Статистика"), scroll)
    return item


# --- Markdown export -------------------------------------------------------

def _markdown_export_tab(config: AppConfig, config_manager: ConfigManager) -> NSTabViewItem:
    item = _tab("markdown-export", "Markdown")
    selected_directory = config.markdown_export.directory

    enabled = ui.push_button("Сохранять диктовки в Markdown", None)
    enabled.setButtonType_(3)  # NSSwitchButton
    enabled.setState_(1 if config.markdown_export.enabled else 0)
    choose = ui.push_button("Выбрать папку…")
    check = ui.push_button("Проверить")
    export_mode = NSSegmentedControl.alloc().initWithFrame_(NSMakeRect(0, 0, 360, 24))
    export_mode.setSegmentCount_(2)
    export_mode.setLabel_forSegment_("Дневной файл", 0)
    export_mode.setLabel_forSegment_("Отдельные файлы", 1)
    export_mode.setSelectedSegment_(0 if config.markdown_export.mode == "daily" else 1)
    export_mode.setTranslatesAutoresizingMaskIntoConstraints_(False)
    structure = NSSegmentedControl.alloc().initWithFrame_(NSMakeRect(0, 0, 360, 24))
    structure.setSegmentCount_(2)
    structure.setLabel_forSegment_("В одной папке", 0)
    structure.setLabel_forSegment_("Год / месяц", 1)
    structure.setSelectedSegment_(
        1 if config.markdown_export.structure == "year_month" else 0
    )
    structure.setTranslatesAutoresizingMaskIntoConstraints_(False)
    template = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 360, 24))
    template.setStringValue_(config.markdown_export.template)
    template.setPlaceholderString_("# {date}")
    template.setTranslatesAutoresizingMaskIntoConstraints_(False)
    status = ui.secondary("")
    path_label = ui.wrapping(
        ui.secondary(str(selected_directory) if selected_directory else "Папка не выбрана"),
        BODY_WIDTH,
    )
    preview = ui.wrapping(
        ui.secondary(
            "Дневной режим: 2026-09-22.md. Сохраняется только итоговый текст."
        ),
        BODY_WIDTH,
    )

    def selected_mode() -> str:
        return "daily" if export_mode.selectedSegment() == 0 else "separate"

    def selected_structure() -> str:
        return "year_month" if structure.selectedSegment() == 1 else "flat"

    def update_preview() -> None:
        relative = "2026/09/2026-09-22.md" if selected_structure() == "year_month" else "2026-09-22.md"
        preview.setStringValue_(f"Пример пути: {relative}")

    def apply_selection(enable_export: bool) -> bool:
        nonlocal selected_directory
        if enable_export:
            validation = validate_export_directory(selected_directory)
            if not validation.ok:
                enabled.setState_(0)
                status.setStringValue_(validation.message)
                return False
            selected_directory = validation.directory
        try:
            save_markdown_export(
                enable_export,
                selected_directory,
                mode=selected_mode(),
                structure=selected_structure(),
                template=str(template.stringValue()),
            )
            config_manager.reload()
        except Exception:
            logger.exception("Saving Markdown export destination failed")
            status.setStringValue_("Не удалось сохранить настройку")
            return False
        path_label.setStringValue_(
            str(selected_directory) if selected_directory else "Папка не выбрана"
        )
        status.setStringValue_(
            "Автосохранение Markdown включено" if enable_export else "Автосохранение Markdown выключено"
        )
        return True

    def on_toggle(_sender):
        apply_selection(enabled.state() == 1)

    def on_choose(_sender):
        nonlocal selected_directory
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(False)
        panel.setCanChooseDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        panel.setCanCreateDirectories_(True)
        if panel.runModal() != NSModalResponseOK:
            return
        url = panel.URL()
        if url is None:
            return
        selected_directory = Path(str(url.path()))
        path_label.setStringValue_(str(selected_directory))
        if enabled.state() == 1:
            apply_selection(True)
        else:
            status.setStringValue_("Папка выбрана. Включи автосохранение, когда будешь готов.")

    def on_check(_sender):
        validation = validate_export_directory(selected_directory)
        if validation.ok:
            status.setStringValue_("Папка готова для сохранения")
        else:
            status.setStringValue_(validation.message)

    def on_mode_change(_sender):
        update_preview()
        if enabled.state() == 1:
            apply_selection(True)
        else:
            status.setStringValue_("Режим сохранения выбран")

    ui.on_action(enabled, on_toggle)
    ui.on_action(choose, on_choose)
    ui.on_action(check, on_check)
    ui.on_action(export_mode, on_mode_change)
    ui.on_action(structure, on_mode_change)
    ui.on_action(template, on_mode_change)
    update_preview()

    body = ui.vstack([
        ui.title("Автосохранение диктовок"),
        ui.wrapping(ui.secondary(
            "После каждой завершённой обычной диктовки FlowSpeech сохранит итоговый текст в выбранной папке. "
            "Obsidian не требуется."
        ), BODY_WIDTH),
        enabled,
        ui.divider(),
        ui.secondary("Формат сохранения", size=11),
        export_mode,
        ui.secondary("Структура дневника", size=11),
        structure,
        ui.secondary("Шаблон нового дневного файла", size=11),
        template,
        ui.secondary("Папка назначения", size=11),
        path_label,
        ui.hstack([choose, check]),
        preview,
        status,
    ], spacing=ui.SECTION_SPACING, fill=True)
    ui.pin(body, item.view())
    return item


# --- Window ----------------------------------------------------------------

def show_settings(
    config: AppConfig, stats: StatsStore, config_manager: ConfigManager
) -> None:
    """Open (or focus) the settings window."""
    global _window
    if _window is not None:
        _window.makeKeyAndOrderFront_(None)
        return

    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, WIDTH, HEIGHT),
        NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        | NSWindowStyleMaskMiniaturizable,
        NSBackingStoreBuffered,
        False,
    )
    window.setTitle_("FlowSpeech — Настройки")
    window.setReleasedWhenClosed_(False)
    window.center()

    content = window.contentView()
    tabs = NSTabView.alloc().initWithFrame_(content.bounds())
    tabs.setAutoresizingMask_(1 << 1 | 1 << 4)  # width + height sizable
    for tab in (
        _general_tab(config),
        _dictionary_tab(config),
        _history_tab(config, stats),
        _keys_tab(config, config_manager),
        _markdown_export_tab(config, config_manager),
        _stats_tab(stats),
    ):
        tabs.addTabViewItem_(tab)
    content.addSubview_(tabs)

    window.makeKeyAndOrderFront_(None)

    from AppKit import NSApp

    NSApp.activateIgnoringOtherApps_(True)
    _window = window
