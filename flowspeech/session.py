"""Small concurrency primitives for one dictation session at a time."""

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


class SessionToken:
    def __init__(self):
        self._cancelled = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: UUID
    created_at: datetime
    started_monotonic: float
    mode: str
    config: Any
    provider: str
    cleanup_mode: str
    target_app: str
    token: SessionToken


class SessionController:
    def __init__(self):
        self._lock = threading.Lock()
        self._current: SessionSnapshot | None = None

    @property
    def current(self) -> SessionSnapshot | None:
        with self._lock:
            return self._current

    def start(
        self,
        *,
        mode: str,
        config: Any,
        provider: str,
        cleanup_mode: str,
        target_app: str,
        session_id: UUID | None = None,
        created_at: datetime | None = None,
        started_monotonic: float | None = None,
    ) -> SessionSnapshot:
        snapshot = SessionSnapshot(
            session_id=session_id or uuid4(),
            created_at=created_at or datetime.now().astimezone(),
            started_monotonic=(
                started_monotonic if started_monotonic is not None else __import__("time").perf_counter()
            ),
            mode=mode,
            config=config,
            provider=provider,
            cleanup_mode=cleanup_mode,
            target_app=target_app,
            token=SessionToken(),
        )
        with self._lock:
            if self._current is not None:
                raise RuntimeError("A dictation session is already active")
            self._current = snapshot
        return snapshot

    def cancel(self) -> bool:
        current = self.current
        if current is None:
            return False
        current.token.cancel()
        return True

    def finish(self, session_id: UUID) -> bool:
        with self._lock:
            if self._current is None or self._current.session_id != session_id:
                return False
            self._current = None
            return True


class HotkeyEventDispatcher:
    """Run keyboard callbacks serially outside pynput's listener thread."""

    def __init__(self):
        self._events: queue.Queue[tuple[Callable | None, tuple]] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True, name="flowspeech-hotkeys")
        self._thread.start()

    def submit(self, callback: Callable, *args) -> None:
        self._events.put((callback, args))

    def stop(self) -> None:
        self._events.put((None, ()))
        self._thread.join(timeout=2)

    def _run(self) -> None:
        while True:
            callback, args = self._events.get()
            try:
                if callback is None:
                    return
                callback(*args)
            except Exception:
                logger.exception("Serialized hotkey callback failed")
            finally:
                self._events.task_done()
