"""Tests for durable Markdown delivery state."""

import stat
import sqlite3
from datetime import datetime, timedelta, timezone
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


def test_corrupt_queue_is_preserved_and_recreated(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "markdown-queue.db"
    path.write_bytes(b"not a sqlite database")

    queue = ExportQueue(data_dir)

    backups = list(data_dir.glob("markdown-queue.corrupt-*.db"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"not a sqlite database"
    assert queue.pending() == ()
    with sqlite3.connect(queue.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_failed_item_waits_for_backoff_and_can_be_forced(tmp_path):
    queue = ExportQueue(tmp_path / "data")
    snapshot = item(tmp_path / "notes")
    queue.enqueue(snapshot)
    now = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)

    queue.mark_failed(SESSION, "offline", now=now)

    assert queue.ready(now=now) == ()
    assert queue.ready(now=now + timedelta(seconds=2)) == (snapshot,)
    assert queue.pending() == (snapshot,)


def test_pending_destination_changes_only_by_explicit_redirect(tmp_path):
    original = tmp_path / "old"
    replacement = tmp_path / "new"
    queue = ExportQueue(tmp_path / "data")
    snapshot = item(original)
    queue.enqueue(snapshot)

    queue.redirect(SESSION, MarkdownExportConfig(True, replacement, "daily"))

    assert queue.latest().destination.directory == replacement


def test_existing_queue_schema_is_migrated_without_losing_items(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "markdown-queue.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE pending_exports (
                session_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                final_text TEXT NOT NULL,
                directory TEXT NOT NULL,
                mode TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )
            """
        )
        snapshot = item(tmp_path / "notes")
        connection.execute(
            "INSERT INTO pending_exports VALUES (?, ?, ?, ?, ?, 0, NULL)",
            (
                str(snapshot.session_id),
                snapshot.created_at.isoformat(),
                snapshot.final_text,
                str(snapshot.destination.directory),
                snapshot.destination.mode,
            ),
        )

    queue = ExportQueue(data_dir)

    assert queue.pending() == (item(tmp_path / "notes"),)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(pending_exports)")}
    assert "next_retry" in columns


def test_thousand_unique_exports_survive_retry_cycles(tmp_path):
    from flowspeech.markdown_export import ExportStatus, MarkdownExporter

    destination = tmp_path / "notes"
    destination.mkdir()
    config = MarkdownExportConfig(True, destination, "daily")
    queue = ExportQueue(tmp_path / "data")
    created = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
    session_ids = []

    for number in range(1000):
        session_id = UUID(int=number + 1)
        session_ids.append(session_id)
        snapshot = DictationExport.create(
            session_id=session_id,
            created_at=created,
            final_text=f"Запись {number}",
            destination=config,
        )
        queue.enqueue(snapshot)
        result = MarkdownExporter(config).export(snapshot)
        assert result.status is ExportStatus.SAVED
        if number % 11 == 0:
            assert MarkdownExporter(config).export(snapshot).status is ExportStatus.SAVED
        queue.mark_delivered(session_id)

    content = (destination / "2026-09-23.md").read_text(encoding="utf-8")
    assert queue.count() == 0
    assert content.count("flowspeech_entry_begin:") == 1000
    assert content.count("flowspeech_entry_end:") == 1000
    assert all(content.count(str(session_id)) == 3 for session_id in session_ids)
