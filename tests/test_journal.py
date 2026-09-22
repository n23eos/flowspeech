"""Tests for safe daily-note editing."""

from datetime import date

import pytest

from flowspeech.config import MarkdownExportConfig
from flowspeech.journal import JournalConflict, JournalService


DAY = date(2026, 9, 23)


def service(directory):
    return JournalService(MarkdownExportConfig(True, directory, "daily"))


def test_read_and_save_new_daily_note(tmp_path):
    journal = service(tmp_path)
    empty = journal.read(DAY)

    saved = journal.save(DAY, "# День\n\nПервая мысль\n", empty.digest)

    assert saved.path == tmp_path / "2026-09-23.md"
    assert saved.text == "# День\n\nПервая мысль\n"
    assert saved.path.stat().st_mode & 0o777 == 0o600


def test_external_change_causes_conflict_and_is_preserved(tmp_path):
    path = tmp_path / "2026-09-23.md"
    path.write_text("Исходный текст", encoding="utf-8")
    journal = service(tmp_path)
    opened = journal.read(DAY)
    path.write_text("Внешняя правка", encoding="utf-8")

    with pytest.raises(JournalConflict, match="другом редакторе"):
        journal.save(DAY, "Моя правка", opened.digest)

    assert path.read_text(encoding="utf-8") == "Внешняя правка"


def test_save_keeps_recovery_backup_of_previous_version(tmp_path):
    path = tmp_path / "2026-09-23.md"
    path.write_text("Первая версия", encoding="utf-8")
    journal = service(tmp_path)
    opened = journal.read(DAY)

    journal.save(DAY, "Вторая версия", opened.digest)

    backup = tmp_path / ".2026-09-23.md.flowspeech-backup"
    assert backup.read_text(encoding="utf-8") == "Первая версия"
