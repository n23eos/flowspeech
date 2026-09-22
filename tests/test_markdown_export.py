"""Tests for the opt-in, retry-safe Markdown export writer."""

from datetime import datetime
import os
from uuid import UUID

import pytest

from flowspeech.config import MarkdownExportConfig
from flowspeech.markdown_export import (
    DictationExport,
    ExportStatus,
    MarkdownExporter,
    validate_export_directory,
)


CREATED_AT = datetime(2026, 9, 22, 14, 35, 12)
SESSION_A = UUID("a7d69a61-9f93-4d0a-9703-f6eb3aa22fb9")
SESSION_B = UUID("c8560a38-4fc5-454f-8e4b-f25ec7d3773f")


def enabled_config(directory, mode="separate"):
    return MarkdownExportConfig(enabled=True, directory=directory, mode=mode)


def snapshot(directory, text="Привет, **мир**!\n\n- один\n- два", session_id=SESSION_A):
    return DictationExport.create(
        session_id=session_id,
        created_at=CREATED_AT,
        final_text=text,
        destination=enabled_config(directory),
    )


def test_saves_utf8_markdown_note_with_session_marker(tmp_path):
    result = MarkdownExporter(snapshot(tmp_path).destination).export(snapshot(tmp_path))

    assert result.status is ExportStatus.SAVED
    assert result.path is not None and result.path.exists()
    content = result.path.read_text(encoding="utf-8")
    assert 'flowspeech_session: "a7d69a61-9f93-4d0a-9703-f6eb3aa22fb9"' in content
    assert 'created: "2026-09-22T14:35:12"' in content
    assert content.endswith("Привет, **мир**!\n\n- один\n- два\n")


def test_same_snapshot_retry_verifies_one_existing_note(tmp_path):
    exporter = MarkdownExporter(enabled_config(tmp_path))
    item = snapshot(tmp_path)

    first = exporter.export(item)
    second = exporter.export(item)

    assert first.status is ExportStatus.SAVED
    assert second.status is ExportStatus.SAVED
    assert first.path == second.path
    assert list(tmp_path.glob("*.md")) == [first.path]


def test_different_sessions_with_same_timestamp_create_two_notes(tmp_path):
    exporter = MarkdownExporter(enabled_config(tmp_path))

    first = exporter.export(snapshot(tmp_path, session_id=SESSION_A))
    second = exporter.export(snapshot(tmp_path, session_id=SESSION_B))

    assert first.status is ExportStatus.SAVED
    assert second.status is ExportStatus.SAVED
    assert first.path != second.path
    assert len(list(tmp_path.glob("*.md"))) == 2


def test_unrelated_occupied_target_is_never_overwritten(tmp_path):
    item = snapshot(tmp_path)
    target = tmp_path / item.filename
    target.write_text("чужая заметка", encoding="utf-8")

    result = MarkdownExporter(item.destination).export(item)

    assert result.status is ExportStatus.FAILED
    assert result.retryable is True
    assert target.read_text(encoding="utf-8") == "чужая заметка"


def test_missing_destination_returns_retryable_failure(tmp_path):
    missing = tmp_path / "missing"
    item = snapshot(missing)

    result = MarkdownExporter(item.destination).export(item)

    assert result.status is ExportStatus.FAILED
    assert result.retryable is True
    assert not missing.exists()


def test_retry_saves_original_snapshot_after_destination_is_restored(tmp_path):
    missing = tmp_path / "restored"
    item = snapshot(missing)
    exporter = MarkdownExporter(item.destination)

    first = exporter.export(item)
    missing.mkdir()
    retry = exporter.export(item)

    assert first.status is ExportStatus.FAILED
    assert retry.status is ExportStatus.SAVED
    assert retry.path is not None
    assert list(missing.glob("*.md")) == [retry.path]
    assert f'flowspeech_session: "{SESSION_A}"' in retry.path.read_text(encoding="utf-8")


def test_daily_export_appends_two_dictations_to_one_note(tmp_path):
    config = enabled_config(tmp_path, mode="daily")
    exporter = MarkdownExporter(config)
    first = DictationExport.create(
        session_id=SESSION_A,
        created_at=CREATED_AT,
        final_text="Первая мысль",
        destination=config,
    )
    second = DictationExport.create(
        session_id=SESSION_B,
        created_at=CREATED_AT.replace(hour=15, minute=5),
        final_text="Вторая мысль",
        destination=config,
    )

    first_result = exporter.export(first)
    second_result = exporter.export(second)
    retry_result = exporter.export(first)

    assert first_result.status is ExportStatus.SAVED
    assert second_result.status is ExportStatus.SAVED
    assert retry_result.status is ExportStatus.SAVED
    assert first_result.path == second_result.path == retry_result.path
    assert first_result.path is not None
    content = first_result.path.read_text(encoding="utf-8")
    assert content.count("flowspeech_session:") == 2
    assert "## 14:35" in content
    assert "## 15:05" in content
    assert "Первая мысль" in content
    assert "Вторая мысль" in content
    assert content.count("flowspeech_entry_end:") == 2


def test_daily_export_recovers_partial_block_without_losing_original(tmp_path):
    config = enabled_config(tmp_path, mode="daily")
    target = tmp_path / "2026-09-22.md"
    partial = (
        "# День\n\n"
        f'<!-- flowspeech_entry_begin: "{SESSION_A}" -->\n'
        f'<!-- flowspeech_session: "{SESSION_A}" -->\n\n'
        "## 14:35\n\nОборванная"
    )
    target.write_text(partial, encoding="utf-8")
    item = DictationExport.create(
        session_id=SESSION_A,
        created_at=CREATED_AT,
        final_text="Полная запись",
        destination=config,
    )

    result = MarkdownExporter(config).export(item)

    assert result.status is ExportStatus.SAVED
    content = target.read_text(encoding="utf-8")
    assert content.count("flowspeech_entry_begin:") == 1
    assert content.count("flowspeech_entry_end:") == 1
    assert "Полная запись" in content
    assert "Оборванная" not in content
    recovery = tmp_path / f".flowspeech-recovery-{SESSION_A}.bak"
    assert recovery.read_text(encoding="utf-8") == partial


def test_daily_export_preserves_existing_note_content(tmp_path):
    config = enabled_config(tmp_path, mode="daily")
    target = tmp_path / "2026-09-22.md"
    target.write_text("# Мои заметки\n\nСуществующий текст", encoding="utf-8")
    item = DictationExport.create(
        session_id=SESSION_A,
        created_at=CREATED_AT,
        final_text="Новая диктовка",
        destination=config,
    )

    result = MarkdownExporter(config).export(item)

    assert result.status is ExportStatus.SAVED
    content = target.read_text(encoding="utf-8")
    assert content.startswith("# Мои заметки\n\nСуществующий текст")
    assert "Новая диктовка" in content


def test_disabled_export_is_skipped_without_writing(tmp_path):
    disabled = MarkdownExportConfig(enabled=False, directory=None)
    item = DictationExport.create(
        session_id=SESSION_A,
        created_at=CREATED_AT,
        final_text="текст",
        destination=disabled,
    )

    result = MarkdownExporter(disabled).export(item)

    assert result.status is ExportStatus.SKIPPED
    assert list(tmp_path.iterdir()) == []


def test_empty_final_text_cannot_create_snapshot(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        DictationExport.create(
            session_id=SESSION_A,
            created_at=CREATED_AT,
            final_text="   ",
            destination=enabled_config(tmp_path),
        )


def test_directory_validation_rejects_a_missing_path(tmp_path):
    result = validate_export_directory(tmp_path / "missing")

    assert result.ok is False
    assert "не существует" in result.message


def test_daily_export_preserves_invalid_utf8_file(tmp_path):
    config = enabled_config(tmp_path, mode="daily")
    target = tmp_path / "2026-09-22.md"
    original = b"\xff\xfeprivate"
    target.write_bytes(original)
    item = DictationExport.create(
        session_id=SESSION_A,
        created_at=CREATED_AT,
        final_text="Новая запись",
        destination=config,
    )

    result = MarkdownExporter(config).export(item)

    assert result.status is ExportStatus.FAILED
    assert "UTF-8" in result.message
    assert target.read_bytes() == original


def test_daily_export_rejects_oversized_file_without_changing_it(tmp_path):
    config = enabled_config(tmp_path, mode="daily")
    target = tmp_path / "2026-09-22.md"
    with target.open("wb") as note:
        note.truncate(33 * 1024 * 1024)
    size = target.stat().st_size
    item = DictationExport.create(
        session_id=SESSION_A,
        created_at=CREATED_AT,
        final_text="Новая запись",
        destination=config,
    )

    result = MarkdownExporter(config).export(item)

    assert result.status is ExportStatus.FAILED
    assert "слишком большой" in result.message
    assert target.stat().st_size == size


def test_daily_export_detects_inode_replacement_before_append(tmp_path, monkeypatch):
    config = enabled_config(tmp_path, mode="daily")
    target = tmp_path / "2026-09-22.md"
    target.write_text("# Исходный файл", encoding="utf-8")
    replacement = tmp_path / "replacement.md"
    replacement.write_text("# Внешняя правка", encoding="utf-8")
    item = DictationExport.create(
        session_id=SESSION_A,
        created_at=CREATED_AT,
        final_text="Новая запись",
        destination=config,
    )
    original_flock = __import__("fcntl").flock

    def replace_after_lock(fd, operation):
        original_flock(fd, operation)
        if operation == __import__("fcntl").LOCK_EX and replacement.exists():
            os.replace(replacement, target)

    monkeypatch.setattr("flowspeech.markdown_export.fcntl.flock", replace_after_lock)

    result = MarkdownExporter(config).export(item)

    assert result.status is ExportStatus.FAILED
    assert "внешним редактором" in result.message
    assert target.read_text(encoding="utf-8") == "# Внешняя правка"


def test_daily_retry_confirms_single_block_after_ambiguous_fsync_failure(
    tmp_path, monkeypatch
):
    config = enabled_config(tmp_path, mode="daily")
    item = DictationExport.create(
        session_id=SESSION_A,
        created_at=CREATED_AT,
        final_text="Новая запись",
        destination=config,
    )
    real_fsync = os.fsync
    calls = 0

    def persisted_but_uncertain(fd):
        nonlocal calls
        calls += 1
        real_fsync(fd)
        if calls == 1:
            raise OSError("simulated acknowledgement loss")

    monkeypatch.setattr("flowspeech.markdown_export.os.fsync", persisted_but_uncertain)
    first = MarkdownExporter(config).export(item)
    retry = MarkdownExporter(config).export(item)

    content = (tmp_path / "2026-09-22.md").read_text(encoding="utf-8")
    assert first.status is ExportStatus.FAILED
    assert retry.status is ExportStatus.SAVED
    assert content.count(f'flowspeech_entry_begin: "{SESSION_A}"') == 1
    assert content.count(f'flowspeech_entry_end: "{SESSION_A}"') == 1


def test_daily_export_uses_configured_structure_and_template(tmp_path):
    config = MarkdownExportConfig(
        True,
        tmp_path,
        "daily",
        structure="year_month",
        template="# Daily {date}\n\n",
    )
    item = DictationExport.create(
        session_id=SESSION_A,
        created_at=CREATED_AT,
        final_text="Структурированная запись",
        destination=config,
    )

    result = MarkdownExporter(config).export(item)

    assert result.status is ExportStatus.SAVED
    assert result.path == tmp_path / "2026" / "09" / "2026-09-22.md"
    assert result.path.read_text(encoding="utf-8").startswith(
        "# Daily 2026-09-22\n\n"
    )
