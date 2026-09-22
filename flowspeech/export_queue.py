"""Persistent delivery queue for user-owned Markdown dictations."""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from flowspeech.config import MarkdownExportConfig
from flowspeech.markdown_export import DictationExport


class ExportQueue:
    """Keep completed text until its Markdown write has been confirmed."""

    def __init__(self, data_dir: Path):
        self._data_dir = Path(data_dir).expanduser()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self._data_dir / "markdown-queue.db"
        self._initialize()
        os.chmod(self.path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        try:
            self._create_or_migrate_schema()
        except sqlite3.DatabaseError:
            if not self.path.exists():
                raise
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = self._data_dir / f"markdown-queue.corrupt-{stamp}.db"
            os.replace(self.path, backup)
            self._create_or_migrate_schema()

    def _create_or_migrate_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_exports (
                    session_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    final_text TEXT NOT NULL,
                    directory TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    next_retry TEXT
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(pending_exports)")
            }
            if "next_retry" not in columns:
                connection.execute(
                    "ALTER TABLE pending_exports ADD COLUMN next_retry TEXT"
                )
            connection.execute("PRAGMA user_version = 1")

    def enqueue(self, snapshot: DictationExport) -> None:
        destination = snapshot.destination
        if not destination.enabled or destination.directory is None:
            return
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO pending_exports (
                    session_id, created_at, final_text, directory, mode
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(snapshot.session_id),
                    snapshot.created_at.isoformat(),
                    snapshot.final_text,
                    str(destination.directory),
                    destination.mode,
                ),
            )

    def mark_failed(
        self,
        session_id: UUID,
        message: str,
        *,
        now: datetime | None = None,
    ) -> None:
        failed_at = now or datetime.now(timezone.utc)
        if failed_at.tzinfo is None:
            failed_at = failed_at.replace(tzinfo=timezone.utc)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT attempts FROM pending_exports WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
            if row is None:
                return
            attempts = int(row[0]) + 1
            delay = min(300, 2**attempts)
            connection.execute(
                """
                UPDATE pending_exports
                SET attempts = ?, last_error = ?, next_retry = ?
                WHERE session_id = ?
                """,
                (
                    attempts,
                    message,
                    (failed_at + timedelta(seconds=delay)).isoformat(),
                    str(session_id),
                ),
            )

    def mark_delivered(self, session_id: UUID) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM pending_exports WHERE session_id = ?",
                (str(session_id),),
            )

    def pending(self) -> tuple[DictationExport, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, created_at, final_text, directory, mode
                FROM pending_exports
                ORDER BY created_at, session_id
                """
            ).fetchall()
        return tuple(self._snapshot(row) for row in rows)

    def ready(self, *, now: datetime | None = None) -> tuple[DictationExport, ...]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, created_at, final_text, directory, mode
                FROM pending_exports
                WHERE next_retry IS NULL OR next_retry <= ?
                ORDER BY created_at, session_id
                """,
                (current.isoformat(),),
            ).fetchall()
        return tuple(self._snapshot(row) for row in rows)

    def redirect(
        self,
        session_id: UUID,
        destination: MarkdownExportConfig,
    ) -> None:
        if not destination.enabled or destination.directory is None:
            raise ValueError("Redirect destination must be enabled")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE pending_exports
                SET directory = ?, mode = ?, next_retry = NULL
                WHERE session_id = ?
                """,
                (str(destination.directory), destination.mode, str(session_id)),
            )

    def latest(self) -> DictationExport | None:
        items = self.pending()
        return items[-1] if items else None

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM pending_exports").fetchone()
        return int(row[0])

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> DictationExport:
        destination = MarkdownExportConfig(
            enabled=True,
            directory=Path(row["directory"]),
            mode=row["mode"],
        )
        return DictationExport.create(
            session_id=UUID(row["session_id"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            final_text=row["final_text"],
            destination=destination,
        )
