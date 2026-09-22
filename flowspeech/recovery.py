"""Offline recovery helpers for pending Markdown delivery records."""

import os
from pathlib import Path

from flowspeech.export_queue import ExportQueue


def export_pending_records(data_dir: Path, output_dir: Path) -> tuple[Path, ...]:
    """Copy pending text to standalone MD files without changing the queue."""
    queue = ExportQueue(data_dir)
    destination = Path(output_dir).expanduser()
    destination.mkdir(parents=True, exist_ok=True)
    recovered = []
    for snapshot in queue.pending():
        path = destination / f"recovered-{snapshot.session_id}.md"
        content = (
            "---\n"
            f'flowspeech_session: "{snapshot.session_id}"\n'
            f'created: "{snapshot.created_at.isoformat()}"\n'
            "recovered_from_queue: true\n"
            "---\n\n"
            f"{snapshot.final_text.rstrip()}\n"
        )
        if path.exists():
            if path.read_text(encoding="utf-8") == content:
                recovered.append(path)
                continue
            raise FileExistsError(f"Recovery target already exists: {path}")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        recovered.append(path)
    return tuple(recovered)
