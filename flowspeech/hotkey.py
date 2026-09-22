"""Global push-to-talk hotkey listener (right option by default).

Runs a pynput listener in its own thread; fires on_press_start when the key
goes down and on_press_end when it comes back up. Requires the macOS
Accessibility permission.
"""

import logging
from collections.abc import Callable

from pynput import keyboard

logger = logging.getLogger(__name__)

HOTKEY_MAP = {
    "right_option": keyboard.Key.alt_r,
    "right_command": keyboard.Key.cmd_r,
    "right_shift": keyboard.Key.shift_r,
    "right_control": keyboard.Key.ctrl_r,
    "f13": getattr(keyboard.Key, "f13", None),
    "f14": getattr(keyboard.Key, "f14", None),
    "f15": getattr(keyboard.Key, "f15", None),
}


def is_accessibility_trusted() -> bool:
    """True if macOS lets this process observe global keyboard events."""
    try:
        import ctypes

        lib = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        return bool(lib.AXIsProcessTrusted())
    except Exception:
        logger.exception("Accessibility check failed")
        return True  # can't check — don't block startup on a false negative


def resolve_key(hotkey_name: str):
    key = HOTKEY_MAP.get(hotkey_name)
    if key is None:
        valid = ", ".join(k for k, v in HOTKEY_MAP.items() if v is not None)
        raise ValueError(f"Unknown hotkey '{hotkey_name}'; choose one of: {valid}")
    return key


class _Binding:
    __slots__ = ("key", "on_press_start", "on_press_end", "is_held")

    def __init__(self, key, on_press_start, on_press_end):
        self.key = key
        self.on_press_start = on_press_start
        self.on_press_end = on_press_end
        self.is_held = False


class MultiHotkeyListener:
    """One pynput listener serving several push-to-talk keys.

    A single OS-level event tap is cheaper and — more importantly — keeps
    ordering deterministic: two independent listeners each get every event
    and may deliver them to callbacks in a different interleaving.
    """

    def __init__(self):
        self._bindings: dict[object, _Binding] = {}
        self._listener: keyboard.Listener | None = None

    def bind(
        self,
        hotkey_name: str,
        on_press_start: Callable[[], None],
        on_press_end: Callable[[], None],
    ) -> None:
        key = resolve_key(hotkey_name)
        if key in self._bindings:
            raise ValueError(f"Hotkey '{hotkey_name}' is already bound")
        self._bindings[key] = _Binding(key, on_press_start, on_press_end)

    def start(self) -> None:
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()
        logger.info(
            "Hotkey listener started (%s)",
            ", ".join(str(b.key) for b in self._bindings.values()),
        )

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    @property
    def is_alive(self) -> bool:
        return self._listener is not None and self._listener.is_alive()

    def _on_press(self, key) -> None:
        binding = self._bindings.get(key)
        if binding is not None and not binding.is_held:
            binding.is_held = True
            self._safe_call(binding.on_press_start)

    def _on_release(self, key) -> None:
        binding = self._bindings.get(key)
        if binding is not None and binding.is_held:
            binding.is_held = False
            self._safe_call(binding.on_press_end)

    @staticmethod
    def _safe_call(callback: Callable[[], None]) -> None:
        # An exception in a callback must not kill the listener thread.
        try:
            callback()
        except Exception:
            logger.exception("Hotkey callback failed")


class PushToTalkListener(MultiHotkeyListener):
    """Single-key convenience wrapper (kept for existing call sites/tests)."""

    def __init__(
        self,
        hotkey_name: str,
        on_press_start: Callable[[], None],
        on_press_end: Callable[[], None],
    ):
        super().__init__()
        self.bind(hotkey_name, on_press_start, on_press_end)
