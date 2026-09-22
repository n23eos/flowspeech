"""Native editor for today's Markdown note."""

import logging
import subprocess
from datetime import date

from AppKit import (
    NSBackingStoreBuffered,
    NSFont,
    NSScrollView,
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
from flowspeech.journal import JournalConflict, JournalError, JournalService

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
    today = date.today()
    try:
        _document = service.read(today)
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
    _window.setTitle_(f"FlowSpeech - Сегодня, {today:%d.%m.%Y}")
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
    save = ui.push_button("Сохранить", default=True)
    reload_button = ui.push_button("Перезагрузить")
    open_button = ui.push_button("Открыть MD-файл")

    def on_save(_sender):
        global _document
        try:
            _document = service.save(today, str(_editor.string()), _document.digest)
            status.setStringValue_("Сохранено")
        except JournalConflict as error:
            status.setStringValue_(str(error))
        except JournalError as error:
            logger.exception("Journal save failed")
            status.setStringValue_(str(error))

    def on_reload(_sender):
        global _document
        try:
            _document = service.read(today)
            _editor.setString_(_document.text)
            status.setStringValue_(str(_document.path))
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

    ui.on_action(save, on_save)
    ui.on_action(reload_button, on_reload)
    ui.on_action(open_button, on_open)
    body = ui.vstack([
        ui.title("Сегодня"),
        ui.secondary("Markdown сохраняется прямо в выбранной папке."),
        scroll,
        ui.hstack([save, reload_button, open_button]),
        status,
    ], spacing=ui.SECTION_SPACING, fill=True)
    ui.pin(body, _window.contentView())
    _window.center()
    _window.makeKeyAndOrderFront_(None)
