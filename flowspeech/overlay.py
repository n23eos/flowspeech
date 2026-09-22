"""Floating on-screen indicator: a dark pill with a live waveform.

States:
  recording  — animated bars driven by the real microphone level
  processing — gentle "breathing" bars while Whisper/LLM work
  flash      — short text message (errors/notices)

All AppKit calls are dispatched to the main thread — callers may live in any
thread (the hotkey listener does).
"""

import threading

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSFont,
    NSOperationQueue,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSTextField,
    NSView,
    NSVisualEffectView,
    NSWindowStyleMaskBorderless,
)
from Foundation import NSMakeRect, NSTimer

try:
    from AppKit import NSTextAlignmentCenter
except ImportError:  # older constant name
    NSTextAlignmentCenter = 1

try:
    from AppKit import (
        NSVisualEffectBlendingModeBehindWindow,
        NSVisualEffectMaterialHUDWindow,
        NSVisualEffectStateActive,
    )
except ImportError:  # pragma: no cover - depends on the PyObjC build
    NSVisualEffectMaterialHUDWindow = 13
    NSVisualEffectBlendingModeBehindWindow = 0
    NSVisualEffectStateActive = 1

# Join all Spaces and stay visible over fullscreen apps.
CAN_JOIN_ALL_SPACES = 1 << 0
FULLSCREEN_AUXILIARY = 1 << 8

PANEL_WIDTH = 200
PANEL_HEIGHT = 40
BOTTOM_MARGIN = 96
FLASH_SECONDS = 2.5

BAR_COUNT = 24
BAR_WIDTH = 3.0
BAR_GAP = 3.0
BAR_MIN = 3.0          # bar height at silence, px
BAR_MAX = 24.0         # bar height at full level, px
FRAME_INTERVAL = 1 / 30
# Microphone RMS that maps to a full-height bar. Speech RMS is typically
# 0.02–0.2; this keeps the wave lively at normal voice volume.
LEVEL_FULL_SCALE = 0.12

PROCESSING_COLOR = (0.65, 0.60, 1.0)   # soft violet
RECORDING_COLOR = (1.0, 1.0, 1.0)


def _on_main(block) -> None:
    NSOperationQueue.mainQueue().addOperationWithBlock_(block)


class _WaveView(NSView):
    """Draws BAR_COUNT rounded bars; heights come from `levels` (0..1 each)."""

    def initWithFrame_(self, frame):
        self = objc.super(_WaveView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.levels = [0.0] * BAR_COUNT
        self.rgb = RECORDING_COLOR
        return self

    def drawRect_(self, rect):
        bounds = self.bounds()
        total = BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_GAP
        x = (bounds.size.width - total) / 2
        cy = bounds.size.height / 2
        NSColor.colorWithCalibratedRed_green_blue_alpha_(*self.rgb, 0.95).set()
        for level in self.levels:
            h = BAR_MIN + (BAR_MAX - BAR_MIN) * max(0.0, min(1.0, level))
            bar = NSMakeRect(x, cy - h / 2, BAR_WIDTH, h)
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bar, BAR_WIDTH / 2, BAR_WIDTH / 2
            ).fill()
            x += BAR_WIDTH + BAR_GAP

    def pushLevel_(self, value):
        self.levels = self.levels[1:] + [float(value)]
        self.setNeedsDisplay_(True)


class Overlay:
    def __init__(self):
        self._panel = None
        self._wave = None
        self._label = None
        self._timer = None
        self._level_source = None       # callable -> raw mic RMS
        self._mode = "idle"             # idle | recording | processing
        self._phase = 0.0               # for the processing animation
        self._generation = 0            # invalidates stale flash timers

    # --- Public API (thread-safe) ------------------------------------------

    def show_recording(self, level_source=None) -> None:
        """Show the pill with a live waveform. `level_source()` returns the
        current mic RMS; without it the wave idles at minimum height."""
        self._generation += 1
        self._level_source = level_source
        self._mode = "recording"
        _on_main(self._start_wave)

    def show_processing(self) -> None:
        self._generation += 1
        self._level_source = None
        self._mode = "processing"
        self._phase = 0.0
        _on_main(self._start_wave)

    def flash(self, text: str) -> None:
        """Show a message briefly, then hide (for errors/warnings)."""
        self._generation += 1
        generation = self._generation
        self._mode = "idle"

        def apply():
            self._ensure_panel()
            self._stop_timer()
            self._wave.setHidden_(True)
            self._label.setStringValue_(text)
            self._label.setHidden_(False)
            self._panel.orderFrontRegardless()

        _on_main(apply)
        threading.Timer(FLASH_SECONDS, lambda: self._hide_if_current(generation)).start()

    def show_preview(self, text: str) -> None:
        """Show the latest provisional text until another state replaces it."""
        self._generation += 1
        self._mode = "idle"
        preview = text.strip().replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:77] + "..."

        def apply():
            self._ensure_panel()
            self._stop_timer()
            self._wave.setHidden_(True)
            self._label.setStringValue_(preview)
            self._label.setHidden_(False)
            self._panel.orderFrontRegardless()

        _on_main(apply)

    def hide(self) -> None:
        self._generation += 1
        self._mode = "idle"

        def apply():
            self._stop_timer()
            if self._panel:
                self._panel.orderOut_(None)

        _on_main(apply)

    # --- Internals (main thread only) ---------------------------------------

    def _start_wave(self) -> None:
        self._ensure_panel()
        self._label.setHidden_(True)
        self._wave.setHidden_(False)
        self._wave.rgb = RECORDING_COLOR if self._mode == "recording" else PROCESSING_COLOR
        self._panel.orderFrontRegardless()
        if self._timer is None:
            self._timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                FRAME_INTERVAL, True, self._tick
            )

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def _tick(self, _timer) -> None:
        if self._mode == "recording":
            raw = 0.0
            if self._level_source is not None:
                try:
                    raw = float(self._level_source())
                except Exception:
                    raw = 0.0
            self._wave.pushLevel_(min(1.0, raw / LEVEL_FULL_SCALE))
        elif self._mode == "processing":
            # Gentle travelling wave so the pill feels alive while thinking.
            import math

            self._phase += 0.25
            self._wave.pushLevel_(0.18 + 0.14 * (1 + math.sin(self._phase)) / 2)
        else:
            self._stop_timer()

    def _hide_if_current(self, generation: int) -> None:
        if generation != self._generation:
            return

        def apply():
            self._stop_timer()
            if self._panel:
                self._panel.orderOut_(None)

        _on_main(apply)

    def _ensure_panel(self) -> None:
        """Build the panel lazily, always on the main thread."""
        if self._panel is not None:
            return

        screen = NSScreen.mainScreen().frame()
        x = (screen.size.width - PANEL_WIDTH) / 2
        rect = NSMakeRect(x, BOTTOM_MARGIN, PANEL_WIDTH, PANEL_HEIGHT)

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False
        )
        panel.setLevel_(NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setIgnoresMouseEvents_(True)
        panel.setCollectionBehavior_(CAN_JOIN_ALL_SPACES | FULLSCREEN_AUXILIARY)
        panel.setHidesOnDeactivate_(False)

        content = panel.contentView()

        # A translucent HUD material (the same vibrancy macOS uses for its own
        # volume/brightness overlays) instead of a flat dark layer — rounded and
        # clipped to a pill.
        effect = NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, PANEL_WIDTH, PANEL_HEIGHT)
        )
        effect.setMaterial_(NSVisualEffectMaterialHUDWindow)
        effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(NSVisualEffectStateActive)
        effect.setWantsLayer_(True)
        effect.layer().setCornerRadius_(PANEL_HEIGHT / 2)
        effect.layer().setMasksToBounds_(True)
        content.addSubview_(effect)

        wave = _WaveView.alloc().initWithFrame_(
            NSMakeRect(0, 0, PANEL_WIDTH, PANEL_HEIGHT)
        )
        effect.addSubview_(wave)

        label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(0, (PANEL_HEIGHT - 20) / 2, PANEL_WIDTH, 20)
        )
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setAlignment_(NSTextAlignmentCenter)
        label.setFont_(NSFont.systemFontOfSize_(13))
        label.setTextColor_(NSColor.labelColor())  # adapts to the HUD material
        label.setHidden_(True)
        effect.addSubview_(label)

        self._panel = panel
        self._wave = wave
        self._label = label
