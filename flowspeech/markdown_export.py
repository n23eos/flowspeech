"""Opt-in, retry-safe export of final dictations as local Markdown files."""

import logging
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from uuid import UUID

import fcntl

from flowspeech.config import MarkdownExportConfig

logger = logging.getLogger(__name__)


class ExportStatus(str, Enum):
    SAVED = "saved"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class DestinationValidation:
    ok: bool
    directory: Path | None
    message: str


@dataclass(frozen=True)
class DictationExport:
    session_id: UUID
    created_at: datetime
    final_text: str
    destination: MarkdownExportConfig

    @classmethod
    def create(
        cls,
        *,
        session_id: UUID,
        created_at: datetime,
        final_text: str,
        destination: MarkdownExportConfig,
    ) -> "DictationExport":
        if not final_text or not final_text.strip():
            raise ValueError("Cannot export empty final text")
        return cls(
            session_id=session_id,
            created_at=created_at,
            final_text=final_text,
            destination=destination,
        )

    @property
    def filename(self) -> str:
        return f"{self.created_at:%Y%m%d-%H%M%S}-{self.session_id.hex}.md"

    @property
    def daily_filename(self) -> str:
        return f"{self.created_at:%Y-%m-%d}.md"


@dataclass(frozen=True)
class ExportResult:
    status: ExportStatus
    path: Path | None
    message: str
    retryable: bool = False


def validate_export_directory(directory: Path | None) -> DestinationValidation:
    """Confirm a user-selected root exists and accepts a harmless local write."""
    if directory is None:
        return DestinationValidation(False, None, "Папка для сохранения не выбрана")

    try:
        candidate = Path(directory).expanduser()
        if candidate.is_symlink():
            return DestinationValidation(False, None, "Папка для сохранения не должна быть ссылкой")
        if not candidate.exists():
            return DestinationValidation(False, None, "Выбранная папка не существует")
        if not candidate.is_dir():
            return DestinationValidation(False, None, "Выбранный путь не является папкой")
        root = candidate.resolve(strict=True)
        fd, probe_name = tempfile.mkstemp(prefix=".flowspeech-check-", dir=root)
        try:
            os.close(fd)
            Path(probe_name).unlink()
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        return DestinationValidation(True, root, "Папка готова для сохранения")
    except OSError as error:
        logger.info("Markdown export destination validation failed: %s", error)
        return DestinationValidation(False, None, "В выбранную папку нельзя записать заметку")


def _note_content(snapshot: DictationExport) -> str:
    body = snapshot.final_text.rstrip("\n")
    return (
        "---\n"
        f'flowspeech_session: "{snapshot.session_id}"\n'
        f'created: "{snapshot.created_at.isoformat()}"\n'
        "status: final\n"
        "---\n\n"
        f"{body}\n"
    )


def _daily_entry_content(snapshot: DictationExport, has_existing_text: bool) -> str:
    prefix = "\n\n" if has_existing_text else ""
    body = snapshot.final_text.rstrip("\n")
    return (
        f'{prefix}<!-- flowspeech_entry_begin: "{snapshot.session_id}" -->\n'
        f'<!-- flowspeech_session: "{snapshot.session_id}" -->\n\n'
        f"## {snapshot.created_at:%H:%M}\n\n"
        f"{body}\n\n"
        f'<!-- flowspeech_entry_end: "{snapshot.session_id}" -->\n'
    )


def _has_matching_session(path: Path, session_id: UUID) -> bool:
    """Only treat an existing note as ours when its front matter matches."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        marker = f'flowspeech_session: "{session_id}"'
        with path.open("r", encoding="utf-8") as note:
            while chunk := note.read(64 * 1024):
                if marker in chunk:
                    return True
        return False
    except OSError:
        return False


class MarkdownExporter:
    """Write one completed dictation below a validated, selected root."""

    def __init__(self, destination: MarkdownExportConfig):
        self._destination = destination

    def export(self, snapshot: DictationExport) -> ExportResult:
        if not snapshot.destination.enabled:
            return ExportResult(ExportStatus.SKIPPED, None, "Сохранение Markdown отключено")
        if snapshot.destination != self._destination:
            return ExportResult(
                ExportStatus.FAILED,
                None,
                "Назначение сохранения изменилось, повторите экспорт",
                retryable=True,
            )

        validation = validate_export_directory(snapshot.destination.directory)
        if not validation.ok or validation.directory is None:
            return ExportResult(ExportStatus.FAILED, None, validation.message, retryable=True)

        target = validation.directory / snapshot.filename
        if target.parent != validation.directory:
            return ExportResult(
                ExportStatus.FAILED,
                None,
                "Путь сохранения недопустим",
                retryable=True,
            )
        if snapshot.destination.mode == "daily":
            return self._append_daily(validation.directory, snapshot)
        if target.exists() or target.is_symlink():
            if _has_matching_session(target, snapshot.session_id):
                return ExportResult(ExportStatus.SAVED, target, "Заметка уже сохранена")
            return ExportResult(
                ExportStatus.FAILED,
                None,
                "Файл с таким именем уже существует",
                retryable=True,
            )

        temporary: Path | None = None
        try:
            fd, temp_name = tempfile.mkstemp(
                prefix=".flowspeech-", suffix=".tmp", dir=validation.directory
            )
            temporary = Path(temp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as note:
                note.write(_note_content(snapshot))
                note.flush()
                os.fsync(note.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                if _has_matching_session(target, snapshot.session_id):
                    return ExportResult(ExportStatus.SAVED, target, "Заметка уже сохранена")
                return ExportResult(
                    ExportStatus.FAILED,
                    None,
                    "Файл с таким именем уже существует",
                    retryable=True,
                )
            return ExportResult(ExportStatus.SAVED, target, "Markdown-заметка сохранена")
        except OSError as error:
            logger.info("Markdown export failed: %s", error)
            return ExportResult(
                ExportStatus.FAILED,
                None,
                "Не удалось сохранить Markdown-заметку",
                retryable=True,
            )
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove Markdown export temporary file")

    def _append_daily(
        self,
        directory: Path,
        snapshot: DictationExport,
    ) -> ExportResult:
        """Append one marker-tagged block to the selected day's Markdown file.

        A cooperative file lock serializes FlowSpeech instances. The entry is
        appended only after checking the whole note for its session marker, so
        an explicit retry cannot duplicate a successful earlier append.
        """
        target = directory / snapshot.daily_filename
        if target.parent != directory or target.is_symlink():
            return ExportResult(
                ExportStatus.FAILED,
                None,
                "Путь дневной заметки недопустим",
                retryable=True,
            )
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(target, flags, 0o600)
            with os.fdopen(fd, "r+", encoding="utf-8") as note:
                if not stat.S_ISREG(os.fstat(note.fileno()).st_mode):
                    return ExportResult(
                        ExportStatus.FAILED,
                        None,
                        "Дневная заметка не является обычным файлом",
                        retryable=True,
                    )
                fcntl.flock(note.fileno(), fcntl.LOCK_EX)
                try:
                    note.seek(0)
                    existing = note.read()
                    session = str(snapshot.session_id)
                    begin = f'<!-- flowspeech_entry_begin: "{session}" -->'
                    end = f'<!-- flowspeech_entry_end: "{session}" -->'
                    legacy = f'<!-- flowspeech_session: "{session}" -->'
                    if end in existing or (legacy in existing and begin not in existing):
                        return ExportResult(
                            ExportStatus.SAVED,
                            target,
                            "Диктовка уже есть в дневной заметке",
                        )
                    if begin in existing:
                        recovery = directory / f".flowspeech-recovery-{session}.bak"
                        recovery.write_text(existing, encoding="utf-8")
                        recovery.chmod(0o600)
                        existing = existing[: existing.index(begin)].rstrip()
                        note.seek(0)
                        note.truncate()
                        note.write(existing)
                    note.seek(0, os.SEEK_END)
                    entry = _daily_entry_content(snapshot, note.tell() > 0)
                    note.write(entry)
                    note.flush()
                    os.fsync(note.fileno())
                finally:
                    fcntl.flock(note.fileno(), fcntl.LOCK_UN)
            return ExportResult(ExportStatus.SAVED, target, "Дневная Markdown-заметка сохранена")
        except OSError as error:
            logger.info("Daily Markdown export failed: %s", error)
            return ExportResult(
                ExportStatus.FAILED,
                None,
                "Не удалось сохранить дневную Markdown-заметку",
                retryable=True,
            )
