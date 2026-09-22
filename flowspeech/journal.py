"""Safe read and edit operations for daily Markdown notes."""

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from flowspeech.config import MarkdownExportConfig
from flowspeech.markdown_export import validate_export_directory


class JournalError(RuntimeError):
    pass


class JournalConflict(JournalError):
    pass


@dataclass(frozen=True)
class JournalDocument:
    path: Path
    text: str
    digest: str
    exists: bool


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class JournalService:
    def __init__(self, destination: MarkdownExportConfig):
        self._destination = destination

    def path_for(self, day: date) -> Path:
        validation = validate_export_directory(self._destination.directory)
        if not self._destination.enabled or validation.directory is None:
            raise JournalError("Сначала включи Markdown и выбери доступную папку")
        if self._destination.mode != "daily":
            raise JournalError("Для дневника выбери режим «Дневной файл»")
        path = validation.directory / f"{day:%Y-%m-%d}.md"
        if path.parent != validation.directory or path.is_symlink():
            raise JournalError("Путь дневной заметки недопустим")
        return path

    def read(self, day: date) -> JournalDocument:
        path = self.path_for(day)
        if not path.exists():
            return JournalDocument(path, "", _digest(b""), False)
        try:
            data = path.read_bytes()
            return JournalDocument(path, data.decode("utf-8"), _digest(data), True)
        except (OSError, UnicodeDecodeError) as error:
            raise JournalError("Не удалось прочитать дневную заметку как UTF-8") from error

    def save(self, day: date, text: str, expected_digest: str) -> JournalDocument:
        path = self.path_for(day)
        current = path.read_bytes() if path.exists() else b""
        if _digest(current) != expected_digest:
            raise JournalConflict("Файл изменён в другом редакторе. Перезагрузи его перед сохранением")

        temporary: Path | None = None
        try:
            if current:
                backup = path.parent / f".{path.name}.flowspeech-backup"
                shutil.copy2(path, backup)
                backup.chmod(0o600)
            fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            temporary = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
            temporary = None
            return self.read(day)
        except OSError as error:
            raise JournalError("Не удалось сохранить дневную заметку") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
