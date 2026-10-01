"""Run task-log inventory, backup verification, and isolated restore checks."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from backend.app.services.task_log_lifecycle import (  # noqa: E402
    TaskLogArchiveLifecycle,
    TaskLogLifecycleError,
    resolve_task_log_lifecycle_path,
)


def _history_root() -> Path:
    configured = os.getenv("CRYPTO_HISTORY_DATA_PATH", "").strip()
    path = Path(configured).expanduser() if configured else ROOT / "data" / "history"
    return path if path.is_absolute() else ROOT / path


def _configured_path(name: str, fallback: Path) -> Path:
    return resolve_task_log_lifecycle_path(os.getenv(name, "").strip(), ROOT) or fallback


def _parser() -> argparse.ArgumentParser:
    history_root = _history_root()
    parser = argparse.ArgumentParser(description="Verify and operate the task-log archive")
    parser.add_argument(
        "--archive-root",
        default=str(history_root / ".runtime" / "task-logs"),
        help="task-log archive root",
    )
    parser.add_argument(
        "--backup-path",
        default=str(
            _configured_path(
                "CRYPTO_TASK_LOG_BACKUP_PATH",
                history_root / ".runtime" / "task-log-backups" / "latest.tar",
            )
        ),
        help="portable backup tar path",
    )
    parser.add_argument(
        "--restore-path",
        default=str(
            _configured_path(
                "CRYPTO_TASK_LOG_RESTORE_PATH",
                history_root / ".runtime" / "task-log-restore-rehearsal",
            )
        ),
        help="new isolated restore directory",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="verify the current archive root")
    inspect_parser.add_argument("--include-files", action="store_true")
    subparsers.add_parser("verify-backup", help="verify the configured backup tar")
    backup_parser = subparsers.add_parser("backup", help="export and verify a backup tar")
    backup_parser.add_argument("--replace", action="store_true")
    subparsers.add_parser("restore-rehearsal", help="restore into a new isolated directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    lifecycle = TaskLogArchiveLifecycle(args.archive_root)
    try:
        if args.command == "inspect":
            result = lifecycle.inspect(
                verify_objects=True,
                include_tasks=True,
                include_files=args.include_files,
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0 if bool(result.get("backup_ready")) else 2
        if args.command == "verify-backup":
            result = lifecycle.verify_backup(args.backup_path)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "backup":
            result = lifecycle.export_backup(args.backup_path, replace=args.replace)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "restore-rehearsal":
            result = lifecycle.restore_backup(args.backup_path, args.restore_path)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        raise ValueError("unsupported task-log lifecycle command")
    except (TaskLogLifecycleError, OSError, ValueError) as error:
        print(json.dumps({"state": "FAILED", "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
