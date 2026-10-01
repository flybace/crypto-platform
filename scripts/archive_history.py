"""Create verified Parquet mirrors for the local history directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from application.history_archive import HistoryArchiveError, ParquetHistoryArchive  # noqa: E402
from application.history_storage import HistoryStorage, HistoryStorageError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Archive verified crypto history as Parquet")
    parser.add_argument("--data-dir", default=str(ROOT / "data" / "history"))
    args = parser.parse_args(argv)
    storage = HistoryStorage(args.data_dir)
    archive = ParquetHistoryArchive(storage.root)
    try:
        result = archive.archive_all(storage)
    except (HistoryArchiveError, HistoryStorageError) as error:
        print(json.dumps({"status": "failed", "message": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
