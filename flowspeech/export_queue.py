"""Persistent delivery queue for user-owned Markdown dictations."""

import os
import sqlite3
from datetime import datetime
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
                    last_error TEXT
                )
                """
            )

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

    def mark_failed(self, session_id: UUID, message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE pending_exports
                SET attempts = attempts + 1, last_error = ?
                WHERE session_id = ?
                """,
                (message, str(session_id)),
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
