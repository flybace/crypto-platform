"""Atomic, content-addressed result objects shared by API and workers."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


CONTRACT_VERSION = "task-result-v1"


class TaskResultArchiveError(RuntimeError):
    """Raised when a task result object cannot be written or verified."""


class TaskResultArchive:
    """Persist bounded task results outside the SQL row with integrity metadata."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def write(self, task_id: str, result: Any) -> dict[str, object]:
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            raise TaskResultArchiveError("task result archive requires task_id")
        document = {
            "contract_version": CONTRACT_VERSION,
            "task_id": normalized_task_id,
            "created_at": datetime.now(UTC).isoformat(),
            "result": result if result is not None else {},
        }
        try:
            encoded = json.dumps(
                document,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise TaskResultArchiveError("task result is not JSON serializable") from error
        content_digest = hashlib.sha256(encoded).hexdigest()
        task_digest = hashlib.sha256(normalized_task_id.encode("utf-8")).hexdigest()
        relative = Path(task_digest[:2]) / task_digest / f"{content_digest}.json"
        path = self._safe_path(relative)
        temp_name: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                mode="wb",
                delete=False,
            ) as handle:
                temp_name = handle.name
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except (OSError, TypeError, ValueError) as error:
            raise TaskResultArchiveError("unable to persist task result archive") from error
        finally:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass
        return {
            "contract_version": CONTRACT_VERSION,
            "key": relative.as_posix(),
            "sha256": content_digest,
            "bytes": len(encoded),
            "state": "READY",
        }

    def read(
        self,
        reference: Mapping[str, object],
        *,
        expected_task_id: str | None = None,
    ) -> Any:
        if not isinstance(reference, Mapping):
            raise TaskResultArchiveError("task result archive reference is invalid")
        if str(reference.get("contract_version", "")) != CONTRACT_VERSION:
            raise TaskResultArchiveError("task result archive contract is unsupported")
        key = str(reference.get("key", "")).strip()
        if not key:
            raise TaskResultArchiveError("task result archive key is missing")
        path = self._safe_path(Path(key))
        try:
            encoded = path.read_bytes()
        except OSError as error:
            raise TaskResultArchiveError("task result archive object is unavailable") from error
        expected_digest = str(reference.get("sha256", "")).strip().lower()
        actual_digest = hashlib.sha256(encoded).hexdigest()
        if expected_digest != actual_digest:
            raise TaskResultArchiveError("task result archive checksum mismatch")
        try:
            expected_bytes = int(reference.get("bytes", -1))
        except (TypeError, ValueError) as error:
            raise TaskResultArchiveError("task result archive size is invalid") from error
        if expected_bytes != len(encoded):
            raise TaskResultArchiveError("task result archive size mismatch")
        try:
            document = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise TaskResultArchiveError("task result archive JSON is invalid") from error
        if not isinstance(document, dict) or document.get("contract_version") != CONTRACT_VERSION:
            raise TaskResultArchiveError("task result archive document is invalid")
        if expected_task_id is not None and document.get("task_id") != str(expected_task_id):
            raise TaskResultArchiveError("task result archive task identity mismatch")
        if "result" not in document:
            raise TaskResultArchiveError("task result archive result is missing")
        return document["result"]

    def _safe_path(self, relative: Path) -> Path:
        if relative.is_absolute() or relative.suffix.lower() != ".json":
            raise TaskResultArchiveError("task result archive key is invalid")
        root = self.root.resolve()
        path = (self.root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise TaskResultArchiveError("task result archive path escaped its root") from error
        return path
