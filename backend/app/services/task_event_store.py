"""SQL task-event persistence and verified file-archive integration.

The task repository owns the SQL tables, while this mixin keeps the event
lifecycle and archive reconciliation boundary in a separate module.  It is
intentionally storage-agnostic beyond the SQLAlchemy table attributes exposed
by ``TaskStore``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import desc, func, insert, select, update

from .task_log_archive import (
    CONTRACT_VERSION as TASK_LOG_CONTRACT_VERSION,
    TaskLogArchive,
    TaskLogArchiveError,
    redact_log_text,
    redact_log_value,
)
from .task_store_codec import UNSET as _UNSET
from .task_store_codec import dump as _dump
from .task_store_codec import load as _load
from .task_store_codec import now as _now


class TaskEventStoreMixin:
    """Event rows, archive references, and archive recovery operations."""

    def event_archive_status(self) -> dict[str, object]:
        """Return SQL counts plus task-manifest integrity status."""
        if self._engine is None:
            return {
                "enabled": self.event_archive is not None,
                "ok": False,
                "status": "unavailable",
                "event_count": 0,
                "ready_count": 0,
                "pending_count": 0,
                "corrupt_count": 0,
                "manifest_error_count": 0,
                "orphan_object_count": 0,
            }
        if self.event_archive is None:
            return {
                "enabled": False,
                "ok": True,
                "status": "disabled",
                "event_count": 0,
                "ready_count": 0,
                "pending_count": 0,
                "corrupt_count": 0,
                "manifest_error_count": 0,
                "orphan_object_count": 0,
            }
        try:
            self._verify_ready_event_archives(limit=100_000)
            with self._engine.connect() as connection:
                rows = connection.execute(
                    select(self._events.c.archive_state, func.count())
                    .group_by(self._events.c.archive_state)
                ).all()
                task_rows = connection.execute(
                    select(self._events.c.task_id).distinct().order_by(self._events.c.task_id)
                ).all()
            manifest_errors = 0
            orphan_objects = 0
            manifest_messages: list[str] = []
            for (task_id,) in task_rows:
                expected = self._event_archive_references(str(task_id), ready_only=True)
                checked = self.event_archive.verify_task(
                    str(task_id),
                    expected_references=expected,
                )
                if not bool(checked.get("ok")):
                    manifest_errors += 1
                    message = str(checked.get("error") or "task log manifest is incomplete")
                    if len(manifest_messages) < 3:
                        manifest_messages.append(f"{task_id}: {message[:240]}")
                orphan_objects += int(checked.get("orphan_object_count") or 0)
        except Exception as error:  # pragma: no cover - depends on runtime state
            return {
                "enabled": True,
                "ok": False,
                "status": "unreachable",
                "event_count": 0,
                "ready_count": 0,
                "pending_count": 0,
                "corrupt_count": 0,
                "manifest_error_count": 0,
                "orphan_object_count": 0,
                "message": str(error),
            }
        counts = {str(state or "PENDING").upper(): int(count or 0) for state, count in rows}
        event_count = sum(counts.values())
        ready_count = counts.get("READY", 0)
        corrupt_count = counts.get("CORRUPT", 0)
        pending_count = max(0, event_count - ready_count)
        ok = pending_count == 0 and corrupt_count == 0 and manifest_errors == 0
        result: dict[str, object] = {
            "enabled": True,
            "ok": ok,
            "status": "ready" if ok else "degraded",
            "event_count": event_count,
            "ready_count": ready_count,
            "pending_count": pending_count,
            "corrupt_count": corrupt_count,
            "manifest_error_count": manifest_errors,
            "orphan_object_count": orphan_objects,
        }
        if manifest_messages:
            result["manifest_errors"] = manifest_messages
        return result

    def append_event(
        self,
        task_id: str,
        event_type: str,
        *,
        status: str | None = None,
        message: str = "",
        payload: Any = None,
        expected_worker_id: str | None | object = _UNSET,
        expected_version: int | object = _UNSET,
    ) -> dict[str, object]:
        """Insert one lifecycle event and persist its archive reference."""
        from .task_store import TaskLeaseLost, TaskStoreError

        self._require_engine()
        with self._engine.begin() as connection:
            if expected_worker_id is not _UNSET or expected_version is not _UNSET:
                current = connection.execute(
                    select(self._tasks)
                    .where(self._tasks.c.task_id == str(task_id))
                    .with_for_update()
                ).mappings().first()
                if current is None:
                    raise TaskLeaseLost("task disappeared before lifecycle event")
                if expected_worker_id is not _UNSET:
                    expected = self._bounded(expected_worker_id, 160)
                    if (
                        str(current.get("status", "")) not in {"running", "cancelling"}
                        or str(current.get("worker_id") or "") != expected
                    ):
                        raise TaskLeaseLost("task lease is no longer owned by worker")
                if expected_version is not _UNSET:
                    try:
                        version = int(expected_version)
                    except (TypeError, ValueError) as error:
                        raise TaskStoreError("task lifecycle version is invalid") from error
                    if int(current.get("version") or 0) != version:
                        raise TaskLeaseLost("task version changed before lifecycle event")
            event = self._insert_event_row(
                connection,
                task_id,
                event_type,
                status=status,
                message=message,
                payload=payload,
            )
        return self._archive_event(event)

    def _insert_event_row(
        self,
        connection: Any,
        task_id: str,
        event_type: str,
        *,
        status: str | None,
        message: str,
        payload: Any,
    ) -> dict[str, object]:
        """Insert a redacted event row and return its archiveable projection."""
        safe_message = redact_log_text(str(message or "")[:500])
        safe_payload = redact_log_value(payload)
        values = {
            "task_id": str(task_id),
            "event_type": self._bounded(event_type, 80),
            "status": self._bounded(status, 40) if status else None,
            "message": self._bounded(safe_message, 500) if safe_message else None,
            "payload_json": _dump(safe_payload),
            "created_at": _now(),
            "archive_key": None,
            "archive_sha256": None,
            "archive_bytes": None,
            "archive_state": "PENDING" if self.event_archive is not None else "DISABLED",
            "archive_attempts": 0,
            "archive_error": None,
        }
        result = connection.execute(insert(self._events).values(**values))
        event_id = result.inserted_primary_key[0]
        return {"event_id": int(event_id), **values, "payload": safe_payload}

    def _archive_event(self, event: dict[str, object]) -> dict[str, object]:
        """Persist an event object and backfill its SQL integrity reference."""
        if self.event_archive is None:
            return {**event, "archive": None}
        try:
            reference = self.event_archive.write(event)
        except TaskLogArchiveError as error:
            attempts, failure = self._mark_event_archive_failure(int(event["event_id"]), error)
            return {
                **event,
                "archive_state": "PENDING",
                "archive_attempts": attempts,
                "archive_error": failure,
                "archive": None,
            }
        with self._engine.begin() as connection:
            current = connection.execute(
                select(self._events).where(self._events.c.event_id == int(event["event_id"]))
            ).mappings().first()
            attempts = int(current.get("archive_attempts") or 0) + 1 if current is not None else 1
            connection.execute(
                update(self._events)
                .where(self._events.c.event_id == int(event["event_id"]))
                .values(
                    archive_key=reference["key"],
                    archive_sha256=reference["sha256"],
                    archive_bytes=reference["bytes"],
                    archive_state="READY",
                    archive_attempts=attempts,
                    archive_error=None,
                )
            )
        return {
            **event,
            "archive_key": reference["key"],
            "archive_sha256": reference["sha256"],
            "archive_bytes": reference["bytes"],
            "archive_state": "READY",
            "archive_attempts": attempts,
            "archive_error": None,
            "archive": reference,
        }

    def _mark_event_archive_failure(
        self,
        event_id: int,
        error: object,
        *,
        state: str = "PENDING",
    ) -> tuple[int, str]:
        """Record a bounded archive failure without replacing the SQL event."""
        message = self._bounded(redact_log_text(str(error)), 500) or "task log archive failed"
        with self._engine.begin() as connection:
            current = connection.execute(
                select(self._events).where(self._events.c.event_id == int(event_id))
            ).mappings().first()
            if current is None:
                return 0, message
            attempts = int(current.get("archive_attempts") or 0) + 1
            connection.execute(
                update(self._events)
                .where(self._events.c.event_id == int(event_id))
                .values(
                    archive_state=self._bounded(state, 20),
                    archive_attempts=attempts,
                    archive_error=message,
                )
            )
        return attempts, message

    def _mark_event_archived(self, event_id: int, reference: dict[str, object]) -> int:
        """Commit a verified archive reference and increment its retry count."""
        with self._engine.begin() as connection:
            current = connection.execute(
                select(self._events).where(self._events.c.event_id == int(event_id))
            ).mappings().first()
            attempts = int(current.get("archive_attempts") or 0) + 1 if current is not None else 1
            connection.execute(
                update(self._events)
                .where(self._events.c.event_id == int(event_id))
                .values(
                    archive_key=reference["key"],
                    archive_sha256=reference["sha256"],
                    archive_bytes=reference["bytes"],
                    archive_state="READY",
                    archive_attempts=attempts,
                    archive_error=None,
                )
            )
        return attempts

    def reconcile_event_archives(self, limit: int = 5000) -> dict[str, int]:
        """Repair manifests, verify ready objects, and backfill a bounded backlog."""
        if self.event_archive is None or self._engine is None:
            return {"scanned": 0, "archived": 0, "failed": 0}
        bounded = max(1, min(int(limit), 10_000))
        self._reconcile_event_manifests(limit=100_000)
        self._verify_ready_event_archives(limit=bounded)
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(self._events)
                .where(
                    (self._events.c.archive_state.is_(None))
                    | (self._events.c.archive_state != "READY")
                )
                .order_by(self._events.c.event_id)
                .limit(bounded)
            ).mappings().all()
        archived = 0
        failed = 0
        for row in rows:
            event = {
                "event_id": int(row["event_id"]),
                "task_id": row["task_id"],
                "event_type": row["event_type"],
                "status": row["status"],
                "message": row["message"] or "",
                "payload": _load(row["payload_json"]),
                "created_at": row["created_at"],
            }
            try:
                reference = self.event_archive.write(event)
            except TaskLogArchiveError as error:
                try:
                    self._mark_event_archive_failure(int(event["event_id"]), error)
                except Exception:
                    pass
                failed += 1
                continue
            try:
                self._mark_event_archived(int(event["event_id"]), reference)
            except Exception:
                failed += 1
                continue
            archived += 1
        return {"scanned": len(rows), "archived": archived, "failed": failed}

    def _reconcile_event_manifests(self, *, limit: int) -> None:
        if self.event_archive is None or self._engine is None:
            return
        bounded = max(1, min(int(limit), 100_000))
        with self._engine.connect() as connection:
            task_rows = connection.execute(
                select(self._events.c.task_id).distinct().order_by(self._events.c.task_id).limit(bounded)
            ).all()
        for (task_id,) in task_rows:
            expected = self._event_archive_references(str(task_id), ready_only=True)
            try:
                self.event_archive.ensure_manifest(
                    str(task_id),
                    expected_references=expected,
                )
            except (TaskLogArchiveError, OSError):
                # The event rows remain visible and will be retried on the next
                # worker/startup reconciliation pass.
                continue

    def _verify_ready_event_archives(self, *, limit: int) -> None:
        """Mark every checked missing or tampered READY object as CORRUPT."""
        if self.event_archive is None or self._engine is None:
            return
        bounded = max(1, min(int(limit), 100_000))
        checked = 0
        last_event_id = 0
        while checked < bounded:
            batch_limit = min(500, bounded - checked)
            with self._engine.connect() as connection:
                rows = connection.execute(
                    select(self._events)
                    .where(
                        (self._events.c.archive_state == "READY")
                        & (self._events.c.event_id > last_event_id)
                    )
                    .order_by(self._events.c.event_id)
                    .limit(batch_limit)
                ).mappings().all()
            if not rows:
                break
            for row in rows:
                last_event_id = int(row["event_id"])
                checked += 1
                reference = self._event_archive_reference(row)
                valid = reference is not None
                if valid:
                    try:
                        self.event_archive.read(
                            reference,
                            expected_task_id=str(row["task_id"]),
                            expected_event_id=int(row["event_id"]),
                        )
                    except (OSError, TaskLogArchiveError, TypeError, ValueError):
                        valid = False
                if not valid:
                    try:
                        self._mark_event_archive_failure(
                            int(row["event_id"]),
                            "task log archive object is missing or corrupt",
                            state="CORRUPT",
                        )
                    except Exception:
                        pass

    def events(self, task_id: str, limit: int = 100) -> list[dict[str, object]]:
        self._require_engine()
        bounded = max(1, min(int(limit), 500))
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(self._events)
                .where(self._events.c.task_id == str(task_id))
                .order_by(desc(self._events.c.created_at), desc(self._events.c.event_id))
                .limit(bounded)
            ).mappings().all()
        return [
            {
                "event_id": int(row["event_id"]),
                "task_id": row["task_id"],
                "event_type": row["event_type"],
                "status": row["status"],
                "message": row["message"] or "",
                "payload": _load(row["payload_json"]),
                "created_at": row["created_at"],
                "archive_state": str(row.get("archive_state") or "DISABLED"),
                "archive_attempts": int(row.get("archive_attempts") or 0),
                "archive_error": row.get("archive_error"),
                "archive": self._event_archive_reference(row),
            }
            for row in rows
        ]

    def _event_archive_references(self, task_id: str, *, ready_only: bool) -> list[dict[str, object]]:
        with self._engine.connect() as connection:
            statement = select(self._events).where(self._events.c.task_id == str(task_id))
            if ready_only:
                statement = statement.where(self._events.c.archive_state == "READY")
            rows = connection.execute(statement.order_by(self._events.c.event_id)).mappings().all()
        return [
            reference
            for row in rows
            if (reference := self._event_archive_reference(row)) is not None
        ]

    @staticmethod
    def _event_archive_reference(row: Any) -> dict[str, object] | None:
        key = str(row.get("archive_key") or "").strip()
        digest = str(row.get("archive_sha256") or "").strip().lower()
        if not key or len(digest) != 64:
            return None
        return {
            "contract_version": TASK_LOG_CONTRACT_VERSION,
            "key": key,
            "sha256": digest,
            "bytes": int(row.get("archive_bytes") or 0),
            "event_id": int(row.get("event_id") or 0),
            "state": str(row.get("archive_state") or "READY"),
        }
