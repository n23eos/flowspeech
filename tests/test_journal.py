"""Tests for safe daily-note editing."""

from datetime import date

import pytest

from flowspeech.config import MarkdownExportConfig
from flowspeech.journal import (
    JournalConflict,
    JournalIndex,
    JournalService,
    apply_voice_entry_prefix,
    upsert_daily_summary,
)


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
    conflicts = list(tmp_path.glob(".2026-09-23.md.flowspeech-conflict-*.md"))
    assert len(conflicts) == 1
    assert conflicts[0].read_text(encoding="utf-8") == "Моя правка"
    assert conflicts[0].stat().st_mode & 0o777 == 0o600


def test_save_keeps_recovery_backup_of_previous_version(tmp_path):
    path = tmp_path / "2026-09-23.md"
    path.write_text("Первая версия", encoding="utf-8")
    journal = service(tmp_path)
    opened = journal.read(DAY)

    journal.save(DAY, "Вторая версия", opened.digest)

    backup = tmp_path / ".2026-09-23.md.flowspeech-backup"
    assert backup.read_text(encoding="utf-8") == "Первая версия"


def test_year_month_structure_and_template_are_used_for_new_note(tmp_path):
    journal = JournalService(
        MarkdownExportConfig(
            True,
            tmp_path,
            "daily",
            structure="year_month",
            template="# Daily {date}\n\n",
        )
    )

    document = journal.read(DAY)

    assert document.path == tmp_path / "2026" / "09" / "2026-09-23.md"
    assert document.text == "# Daily 2026-09-23\n\n"
    assert document.exists is False


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("note", "## 10:15\n\nСтрока один\nСтрока два\n"),
        ("task", "- [ ] Строка один\n  Строка два\n"),
        ("idea", "## 10:15 · Идея\n\nСтрока один\nСтрока два\n"),
    ],
)
def test_entry_types_have_stable_markdown(kind, expected, tmp_path):
    from datetime import datetime

    journal = service(tmp_path)

    rendered = journal.format_entry(
        "Строка один\nСтрока два", kind, datetime(2026, 9, 23, 10, 15)
    )

    assert rendered == expected


def test_local_index_finds_external_changes_and_removes_deleted_files(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    path = notes / "2026-09-23.md"
    path.write_text("Первая уникальная мысль", encoding="utf-8")
    index = JournalIndex(notes, tmp_path / "data" / "journal-index.db")

    assert index.refresh() == 1
    assert index.search("УНИКАЛЬНАЯ")[0].path == path

    path.write_text("Вторая редакция", encoding="utf-8")
    index.refresh()
    assert index.search("уникальная") == ()
    assert index.search("вторая")[0].path == path

    path.unlink()
    index.refresh()
    assert index.search("вторая") == ()


def test_index_search_on_ten_thousand_notes(tmp_path):
    import time

    notes = tmp_path / "notes"
    notes.mkdir()
    for number in range(10_000):
        (notes / f"note-{number:05d}.md").write_text(
            f"Обычная запись {number}", encoding="utf-8"
        )
    target = notes / "note-09876.md"
    target.write_text("Искомая иголка", encoding="utf-8")
    index = JournalIndex(notes, tmp_path / "data" / "journal-index.db")
    index.refresh()

    started = time.perf_counter()
    results = index.search("иголка")
    elapsed = time.perf_counter() - started

    assert [result.path for result in results] == [target]
    assert elapsed < 0.5


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("задача: купить молоко", "- [ ] купить молоко"),
        ("идея: сделать быстрый поиск", "**Идея:** сделать быстрый поиск"),
        ("заметка: обычный текст", "обычный текст"),
        ("task: ship release", "- [ ] ship release"),
    ],
)
def test_explicit_voice_prefix_formats_entry(spoken, expected):
    assert apply_voice_entry_prefix(spoken, enabled=True) == expected


@pytest.mark.parametrize(
    "spoken",
    [
        "задача купить молоко",
        "моя задача: купить молоко",
        "идея для проекта: быстрый поиск",
        "task force is ready",
    ],
)
def test_similar_phrases_are_not_commands(spoken):
    assert apply_voice_entry_prefix(spoken, enabled=True) == spoken
    assert apply_voice_entry_prefix("задача: купить молоко", enabled=False) == "задача: купить молоко"


def test_daily_summary_is_replaced_without_changing_source_entries():
    source = "# День\n\n## 10:00\n\nИсходная запись\n"
    first = upsert_daily_summary(source, "- Первый итог")
    second = upsert_daily_summary(first, "- Новый итог")

    assert second.startswith(source)
    assert second.count("flowspeech_daily_summary_begin") == 1
    assert "Первый итог" not in second
    assert "Новый итог" in second
