"""Tests for serialized hotkey events and cancellation tokens."""

import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from flowspeech.session import HotkeyEventDispatcher, SessionController, SessionToken


def test_dispatcher_preserves_500_press_release_pairs():
    dispatcher = HotkeyEventDispatcher()
    received = []
    complete = threading.Event()

    def record(value):
        received.append(value)
        if len(received) == 1000:
            complete.set()

    for index in range(500):
        dispatcher.submit(record, ("press", index))
        dispatcher.submit(record, ("release", index))

    assert complete.wait(2)
    dispatcher.stop()
    assert received == [
        event for index in range(500) for event in (("press", index), ("release", index))
    ]


def test_cancelled_token_stays_cancelled_for_late_result():
    token = SessionToken()

    assert not token.cancelled
    token.cancel()

    assert token.cancelled


def test_session_controller_freezes_start_context_and_clears_by_identity():
    controller = SessionController()
    config = SimpleNamespace(name="initial")
    started = datetime(2026, 9, 22, 23, 59, tzinfo=timezone.utc)
    session_id = UUID("de0287c0-a0b1-4d50-9854-44e0af5f27ad")

    snapshot = controller.start(
        mode="journal",
        config=config,
        provider="none",
        cleanup_mode="Обычный",
        target_app="TextEdit",
        session_id=session_id,
        created_at=started,
        started_monotonic=12.5,
    )

    assert snapshot.session_id == session_id
    assert snapshot.created_at == started
    assert snapshot.config is config
    assert controller.current is snapshot
    assert controller.finish(UUID("a8c0c635-4076-4d6b-a948-12e7971c526a")) is False
    assert controller.finish(session_id) is True
    assert controller.current is None
