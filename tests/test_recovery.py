from datetime import datetime
from uuid import UUID

from flowspeech.config import MarkdownExportConfig
from flowspeech.export_queue import ExportQueue
from flowspeech.markdown_export import DictationExport
from flowspeech.recovery import export_pending_records


def test_pending_records_can_be_recovered_without_changing_queue(tmp_path):
    data_dir = tmp_path / "data"
    queue = ExportQueue(data_dir)
    snapshot = DictationExport.create(
        session_id=UUID("af1fb990-0d82-4c37-b72b-ffbd3a198cad"),
        created_at=datetime(2026, 9, 23, 12, 0),
        final_text="Текст, который нельзя потерять",
        destination=MarkdownExportConfig(True, tmp_path / "missing", "daily"),
    )
    queue.enqueue(snapshot)

    first = export_pending_records(data_dir, tmp_path / "recovered")
    second = export_pending_records(data_dir, tmp_path / "recovered")

    assert first == second
    assert "Текст, который нельзя потерять" in first[0].read_text(encoding="utf-8")
    assert first[0].stat().st_mode & 0o777 == 0o600
    assert queue.pending() == (snapshot,)
