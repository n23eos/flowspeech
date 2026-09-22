#!/usr/bin/env python3
"""Copy FlowSpeech's pending delivery queue to readable Markdown files."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flowspeech.recovery import export_pending_records  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("~/.flowspeech").expanduser())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    files = export_pending_records(args.data_dir, args.output)
    print(f"Recovered {len(files)} pending Markdown record(s) into {args.output}")


if __name__ == "__main__":
    main()
