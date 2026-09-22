"""Safe read and edit operations for daily Markdown notes."""

import hashlib
import os
import shutil
import sqlite3
import tempfile
from string import Formatter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from flowspeech.config import MarkdownExportConfig
from flowspeech.markdown_export import ensure_safe_subdirectory, validate_export_directory


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


@dataclass(frozen=True)
class JournalSearchResult:
    path: Path
    text: str


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


VOICE_ENTRY_PREFIXES = {
    "задача: ": "task",
    "идея: ": "idea",
    "заметка: ": "note",
    "task: ": "task",
    "idea: ": "idea",
    "note: ": "note",
}
SUMMARY_BEGIN = "<!-- flowspeech_daily_summary_begin -->"
SUMMARY_END = "<!-- flowspeech_daily_summary_end -->"


def apply_voice_entry_prefix(text: str, *, enabled: bool) -> str:
    """Apply only an exact opt-in prefix at the beginning of a journal entry."""
    if not enabled:
        return text
    folded = text.casefold()
    for prefix, kind in VOICE_ENTRY_PREFIXES.items():
        if not folded.startswith(prefix):
            continue
        body = text[len(prefix):].strip()
        if not body:
            return text
        if kind == "task":
            lines = body.splitlines()
            return f"- [ ] {lines[0]}" + "".join(f"\n  {line}" for line in lines[1:])
        if kind == "idea":
            return f"**Идея:** {body}"
        return body
    return text


def upsert_daily_summary(markdown: str, summary: str) -> str:
    """Replace only FlowSpeech's summary block and preserve every source entry."""
    source = markdown.rstrip()
    begin = source.find(SUMMARY_BEGIN)
    if begin >= 0:
        end = source.find(SUMMARY_END, begin)
        if end >= 0:
            source = (source[:begin] + source[end + len(SUMMARY_END):]).rstrip()
    block = f"{SUMMARY_BEGIN}\n\n## Итог дня\n\n{summary.strip()}\n\n{SUMMARY_END}"
    return f"{source}\n\n{block}\n" if source else f"{block}\n"


class JournalService:
    def __init__(self, destination: MarkdownExportConfig):
        self._destination = destination

    def path_for(self, day: date) -> Path:
        validation = validate_export_directory(self._destination.directory)
        if not self._destination.enabled or validation.directory is None:
            raise JournalError("Сначала включи Markdown и выбери доступную папку")
        if self._destination.mode != "daily":
            raise JournalError("Для дневника выбери режим «Дневной файл»")
        relative = Path(f"{day:%Y-%m-%d}.md")
        if self._destination.structure == "year_month":
            relative = Path(f"{day:%Y}") / f"{day:%m}" / relative
        path = validation.directory / relative
        if not path.is_relative_to(validation.directory) or path.is_symlink():
            raise JournalError("Путь дневной заметки недопустим")
        return path

    def _template_for(self, day: date) -> str:
        values = {
            "date": day.isoformat(),
            "year": f"{day:%Y}",
            "month": f"{day:%m}",
            "day": f"{day:%d}",
        }
        try:
            fields = {
                name
                for _, name, _, _ in Formatter().parse(self._destination.template)
                if name
            }
            if not fields.issubset(values):
                raise KeyError
            return self._destination.template.format(**values)
        except (KeyError, ValueError) as error:
            raise JournalError("Шаблон дневника содержит неизвестное поле") from error

    def read(self, day: date) -> JournalDocument:
        path = self.path_for(day)
        if not path.exists():
            return JournalDocument(path, self._template_for(day), _digest(b""), False)
        try:
            data = path.read_bytes()
            return JournalDocument(path, data.decode("utf-8"), _digest(data), True)
        except (OSError, UnicodeDecodeError) as error:
            raise JournalError("Не удалось прочитать дневную заметку как UTF-8") from error

    def save(self, day: date, text: str, expected_digest: str) -> JournalDocument:
        path = self.path_for(day)
        current = path.read_bytes() if path.exists() else b""
        if _digest(current) != expected_digest:
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
            conflict = path.parent / f".{path.name}.flowspeech-conflict-{stamp}.md"
            fd = os.open(conflict, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            raise JournalConflict("Файл изменён в другом редакторе. Перезагрузи его перед сохранением")

        temporary: Path | None = None
        try:
            root = Path(self._destination.directory).expanduser().resolve(strict=True)
            ensure_safe_subdirectory(root, path.relative_to(root).parent)
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

    @staticmethod
    def format_entry(text: str, kind: str, created_at: datetime) -> str:
        body = text.strip()
        if not body:
            raise JournalError("Пустую запись нельзя добавить в дневник")
        if kind == "task":
            lines = body.splitlines()
            return f"- [ ] {lines[0]}\n" + "".join(
                f"  {line}\n" for line in lines[1:]
            )
        if kind == "idea":
            return f"## {created_at:%H:%M} · Идея\n\n{body}\n"
        if kind == "note":
            return f"## {created_at:%H:%M}\n\n{body}\n"
        raise JournalError("Неизвестный тип записи")


class JournalIndex:
    """Rebuildable local index whose source of truth remains the MD files."""

    def __init__(self, root: Path, database: Path):
        self.root = Path(root).expanduser().resolve(strict=True)
        self.database = Path(database).expanduser()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        os.chmod(self.database, 0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS journal_files (
                        path TEXT PRIMARY KEY,
                        mtime_ns INTEGER NOT NULL,
                        size INTEGER NOT NULL,
                        text TEXT NOT NULL,
                        folded TEXT NOT NULL
                    )
                    """
                )
                connection.execute("PRAGMA user_version = 1")
        except sqlite3.DatabaseError as error:
            corruption_codes = {
                getattr(sqlite3, "SQLITE_CORRUPT", 11),
                getattr(sqlite3, "SQLITE_NOTADB", 26),
            }
            if getattr(error, "sqlite_errorcode", None) not in corruption_codes:
                raise
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
            if self.database.exists():
                os.replace(
                    self.database,
                    self.database.with_name(f"{self.database.stem}.corrupt-{stamp}.db"),
                )
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE journal_files (
                        path TEXT PRIMARY KEY,
                        mtime_ns INTEGER NOT NULL,
                        size INTEGER NOT NULL,
                        text TEXT NOT NULL,
                        folded TEXT NOT NULL
                    )
                    """
                )
                connection.execute("PRAGMA user_version = 1")

    def refresh(self) -> int:
        seen: set[str] = set()
        changed = 0
        with self._connect() as connection:
            known = {
                row["path"]: (row["mtime_ns"], row["size"])
                for row in connection.execute(
                    "SELECT path, mtime_ns, size FROM journal_files"
                )
            }
            for path in self.root.rglob("*.md"):
                if path.is_symlink() or not path.is_file():
                    continue
                relative = str(path.relative_to(self.root))
                seen.add(relative)
                stat_result = path.stat()
                signature = (stat_result.st_mtime_ns, stat_result.st_size)
                if known.get(relative) == signature:
                    continue
                if stat_result.st_size > 32 * 1024 * 1024:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                connection.execute(
                    """
                    INSERT INTO journal_files(path, mtime_ns, size, text, folded)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        mtime_ns=excluded.mtime_ns,
                        size=excluded.size,
                        text=excluded.text,
                        folded=excluded.folded
                    """,
                    (relative, *signature, text, text.casefold()),
                )
                changed += 1
            missing = set(known) - seen
            if missing:
                connection.executemany(
                    "DELETE FROM journal_files WHERE path = ?",
                    ((path,) for path in missing),
                )
                changed += len(missing)
        return changed

    def search(self, query: str, *, limit: int = 100) -> tuple[JournalSearchResult, ...]:
        needle = query.strip().casefold()
        if not needle:
            return ()
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT path, text FROM journal_files
                WHERE folded LIKE ? ESCAPE '\\'
                ORDER BY path DESC
                LIMIT ?
                """,
                (f"%{escaped}%", limit),
            ).fetchall()
        return tuple(
            JournalSearchResult(self.root / row["path"], row["text"])
            for row in rows
        )
