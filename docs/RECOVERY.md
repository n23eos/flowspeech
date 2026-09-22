# FlowSpeech recovery guide

FlowSpeech keeps unfinished Markdown deliveries in `~/.flowspeech/markdown-queue.db`. The database has permissions `0600`. Do not delete it while the menu shows pending records.

## A folder is unavailable

1. Restore access to the original folder and choose **Retry Markdown export**.
2. To intentionally use the current folder from Settings, choose **Redirect pending Markdown**. FlowSpeech never changes an old destination silently.
3. A failed write stays in the queue with bounded backoff. A successful fsync removes that record.

## Export pending text before rollback

Run this while FlowSpeech is closed:

```bash
.venv/bin/python scripts/recover_pending_exports.py \
  --data-dir ~/.flowspeech \
  --output ~/Desktop/flowspeech-recovery
```

The command creates one `0600` Markdown file per pending UUID and does not remove anything from the queue. Repeating the command is safe when the generated files are unchanged.

## Partial or conflicting files

- An interrupted daily block is rebuilt on retry. The original partial content is kept as `.flowspeech-recovery-<UUID>.bak` beside the note.
- Saving the built-in editor after an external change shows a conflict and leaves the external version intact.
- A successful editor save keeps the previous version as `.<name>.flowspeech-backup`.
- A replaced inode, symlink, invalid UTF-8 file or file above 32 MiB is rejected and remains pending.

## Database damage

- A damaged delivery database is preserved as `markdown-queue.corrupt-<timestamp>.db` before a fresh queue is created. Keep this file for manual recovery.
- The search index is disposable. Close FlowSpeech, remove `~/.flowspeech/journal-index.db`, then open the journal to rebuild it from MD files.

## Older builds

Older daily markers and separate-note front matter remain readable. The default path stays `YYYY-MM-DD.md`; the optional `YYYY/MM/YYYY-MM-DD.md` structure applies only to new writes. Before launching an older build, export pending records with the command above and keep `markdown-queue.db` unchanged.
