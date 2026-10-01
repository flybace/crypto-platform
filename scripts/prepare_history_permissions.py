"""Prepare history directories for the non-root runtime user."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


RUNTIME_UID = 65532
RUNTIME_GID = 65532


def prepare(root: str | Path) -> int:
    """Make only directories writable by the backend UID; leave data files unchanged."""
    history_root = Path(root)
    history_root.mkdir(parents=True, exist_ok=True)
    directories = (history_root, *(path for path in history_root.rglob("*") if path.is_dir()))
    for directory in directories:
        os.chown(directory, RUNTIME_UID, RUNTIME_GID)
    return len(directories)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare history directory permissions")
    parser.add_argument("root")
    args = parser.parse_args(argv)
    print(f"HISTORY_DIRECTORIES_PREPARED={prepare(args.root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
