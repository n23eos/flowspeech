"""Native editor for today's Markdown note."""

import logging
import subprocess
from datetime import date, datetime, timedelta

from AppKit import (
    NSBackingStoreBuffered,
    NSFont,
    NSScrollView,
    NSSearchField,
    NSSegmentedControl,
    NSTextField,
    NSTextView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakeRect

from flowspeech import uikit as ui
from flowspeech.config import AppConfig
from flowspeech.journal import JournalConflict, JournalError, JournalIndex, JournalService

logger = logging.getLogger(__name__)

_window = None
_editor = None
_document = None


def show_journal(config: AppConfig) -> None:
    global _window, _editor, _document
    if _window is not None:
        _window.makeKeyAndOrderFront_(None)
        return

    service = JournalService(config.markdown_export)
    selected_day = date.today()
    try:
        _document = service.read(selected_day)
    except JournalError as error:
        import rumps

        rumps.alert("FlowSpeech: дневник", str(error))
        return

    _window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, 720, 560),
        NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable,
        NSBackingStoreBuffered,
        False,
    )
    _window.setTitle_(f"FlowSpeech - Дневник, {selected_day:%d.%m.%Y}")
    _window.setReleasedWhenClosed_(False)

    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 660, 420))
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setBorderType_(2)
    scroll.setTranslatesAutoresizingMaskIntoConstraints_(False)
    _editor = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 660, 420))
    _editor.setFont_(NSFont.userFixedPitchFontOfSize_(13))
    _editor.setRichText_(False)
    _editor.setAutomaticQuoteSubstitutionEnabled_(False)
    _editor.setString_(_document.text)
    scroll.setDocumentView_(_editor)

    status = ui.secondary(str(_document.path))
    previous = ui.push_button("Назад")
    current = ui.push_button("Сегодня")
    following = ui.push_button("Вперёд")
    save = ui.push_button("Сохранить", default=True)
    reload_button = ui.push_button("Перезагрузить")
    open_button = ui.push_button("Открыть MD-файл")
    entry_type = NSSegmentedControl.alloc().initWithFrame_(NSMakeRect(0, 0, 300, 24))
    entry_type.setSegmentCount_(3)
    for index, title in enumerate(("Заметка", "Задача", "Идея")):
        entry_type.setLabel_forSegment_(title, index)
    entry_type.setSelectedSegment_(0)
    entry_type.setTranslatesAutoresizingMaskIntoConstraints_(False)
    quick_entry = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 360, 24))
    quick_entry.setPlaceholderString_("Быстрая запись")
    quick_entry.setTranslatesAutoresizingMaskIntoConstraints_(False)
    add_entry = ui.push_button("Добавить")
    search = NSSearchField.alloc().initWithFrame_(NSMakeRect(0, 0, 360, 24))
    search.setPlaceholderString_("Поиск по дневнику")
    search.setTranslatesAutoresizingMaskIntoConstraints_(False)
    search_button = ui.push_button("Найти")
    index = JournalIndex(
        config.markdown_export.directory,
        config.data_dir / "journal-index.db",
    )

    def load_day(day: date) -> None:
        nonlocal selected_day
        global _document
        selected_day = day
        _document = service.read(selected_day)
        _editor.setString_(_document.text)
        _window.setTitle_(f"FlowSpeech - Дневник, {selected_day:%d.%m.%Y}")
        status.setStringValue_(str(_document.path))

    def on_save(_sender):
        global _document
        try:
            _document = service.save(
                selected_day, str(_editor.string()), _document.digest
            )
            index.refresh()
            status.setStringValue_("Сохранено")
        except JournalConflict as error:
            status.setStringValue_(str(error))
        except JournalError as error:
            logger.exception("Journal save failed")
            status.setStringValue_(str(error))

    def on_reload(_sender):
        global _document
        try:
            load_day(selected_day)
        except JournalError as error:
            status.setStringValue_(str(error))

    def on_open(_sender):
        on_save(_sender)
        if _document.path.exists():
            subprocess.Popen(
                ["open", str(_document.path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def on_previous(_sender):
        try:
            load_day(selected_day - timedelta(days=1))
        except JournalError as error:
            status.setStringValue_(str(error))

    def on_current(_sender):
        try:
            load_day(date.today())
        except JournalError as error:
            status.setStringValue_(str(error))

    def on_following(_sender):
        try:
            load_day(selected_day + timedelta(days=1))
        except JournalError as error:
            status.setStringValue_(str(error))

    def on_add_entry(_sender):
        value = str(quick_entry.stringValue()).strip()
        if not value:
            status.setStringValue_("Введи текст записи")
            return
        kind = ("note", "task", "idea")[entry_type.selectedSegment()]
        try:
            entry = service.format_entry(value, kind, datetime.now())
            existing = str(_editor.string()).rstrip()
            _editor.setString_(f"{existing}\n\n{entry}" if existing else entry)
            quick_entry.setStringValue_("")
            status.setStringValue_("Запись добавлена в редактор. Нажми «Сохранить»")
        except JournalError as error:
            status.setStringValue_(str(error))

    def on_search(_sender):
        try:
            index.refresh()
            results = index.search(str(search.stringValue()))
            if not results:
                status.setStringValue_("Ничего не найдено")
            else:
                status.setStringValue_(
                    f"Найдено: {len(results)}. Первый файл: {results[0].path.name}"
                )
        except (JournalError, OSError) as error:
            status.setStringValue_(f"Ошибка поиска: {error}")

    ui.on_action(save, on_save)
    ui.on_action(reload_button, on_reload)
    ui.on_action(open_button, on_open)
    ui.on_action(previous, on_previous)
    ui.on_action(current, on_current)
    ui.on_action(following, on_following)
    ui.on_action(add_entry, on_add_entry)
    ui.on_action(search, on_search)
    ui.on_action(search_button, on_search)
    body = ui.vstack([
        ui.hstack([ui.title("Дневник"), previous, current, following]),
        ui.secondary("Markdown сохраняется прямо в выбранной папке."),
        ui.hstack([entry_type, quick_entry, add_entry]),
        scroll,
        ui.hstack([save, reload_button, open_button]),
        ui.hstack([search, search_button]),
        status,
    ], spacing=ui.SECTION_SPACING, fill=True)
    ui.pin(body, _window.contentView())
    _window.center()
    _window.makeKeyAndOrderFront_(None)
