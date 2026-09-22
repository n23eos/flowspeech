"""Tests for durable Markdown delivery state."""

import stat
from datetime import datetime
from uuid import UUID

from flowspeech.config import MarkdownExportConfig
from flowspeech.export_queue import ExportQueue
from flowspeech.markdown_export import DictationExport


SESSION = UUID("2d506430-4f78-44ab-aad2-a0f9c43dbcbe")


def item(directory):
    return DictationExport.create(
        session_id=SESSION,
        created_at=datetime(2026, 9, 23, 9, 10, 11),
        final_text="Мысль для дневника",
        destination=MarkdownExportConfig(True, directory, "daily"),
    )


def test_pending_export_survives_queue_reopen(tmp_path):
    data_dir = tmp_path / "data"
    destination = tmp_path / "notes"
    queue = ExportQueue(data_dir)

    queue.enqueue(item(destination))
    restored = ExportQueue(data_dir).pending()

    assert len(restored) == 1
    assert restored[0] == item(destination)
    assert stat.S_IMODE(queue.path.stat().st_mode) == 0o600


def test_enqueue_is_idempotent_and_delivery_removes_text(tmp_path):
    queue = ExportQueue(tmp_path / "data")
    snapshot = item(tmp_path / "notes")

    queue.enqueue(snapshot)
    queue.enqueue(snapshot)
    assert queue.pending() == (snapshot,)
    assert queue.count() == 1

    queue.mark_delivered(SESSION)

    assert queue.pending() == ()
    assert queue.count() == 0


def test_disabled_export_is_not_queued(tmp_path):
    queue = ExportQueue(tmp_path / "data")
    snapshot = DictationExport.create(
        session_id=SESSION,
        created_at=datetime(2026, 9, 23, 9, 10, 11),
        final_text="Только вставка",
        destination=MarkdownExportConfig(),
    )

    queue.enqueue(snapshot)

    assert queue.pending() == ()
