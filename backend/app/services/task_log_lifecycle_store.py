"""SQL audit records and metadata queries for task-log lifecycle actions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import desc, insert, select, update

from .task_log_archive import redact_log_value
from .task_store_codec import dump as _dump
from .task_store_codec import load as _load
from .task_store_codec import now as _now


LIFECYCLE_RUN_RUNNING = "running"
LIFECYCLE_RUN_COMPLETED = "completed"
LIFECYCLE_RUN_FAILED = "failed"
LIFECYCLE_RUN_STATES = frozenset(
    {LIFECYCLE_RUN_RUNNING, LIFECYCLE_RUN_COMPLETED, LIFECYCLE_RUN_FAILED}
)
MAX_LIFECYCLE_RESULT_BYTES = 64 * 1024
MAX_TASK_LOG_METADATA_ROWS = 100_000


class TaskLogLifecycleStoreMixin:
    """Persist bounded operator actions without storing archive contents."""

    def start_task_log_lifecycle_run(
        self,
        operation: str,
        actor: str,
        *,
        request_id: str | None = None,
        details: Any = None,
    ) -> dict[str, object]:
        """Create one auditable lifecycle run in the running state."""
        from uuid import uuid4

        self._require_engine()
        operation_value = self._bounded(operation, 80)
        actor_value = self._bounded(actor, 120) or "system"
        if not operation_value:
            raise ValueError("lifecycle operation is required")
        request_value = self._bounded(request_id, 160) if request_id else None
        result_value = self._bounded_json(details)
        run_id = uuid4().hex
        timestamp = _now()
        values = {
            "run_id": run_id,
            "operation": operation_value,
            "state": LIFECYCLE_RUN_RUNNING,
            "actor": actor_value,
            "request_id": request_value,
            "started_at": timestamp,
            "finished_at": None,
            "result_json": _dump(result_value),
            "error_json": _dump({}),
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        with self._engine.begin() as connection:
            connection.execute(insert(self._task_log_lifecycle_runs).values(**values))
        return self._lifecycle_run_record(values)

    def finish_task_log_lifecycle_run(
        self,
        run_id: str,
        *,
        state: str,
        result: Any = None,
        error: Any = None,
    ) -> dict[str, object]:
        """Finish a run once; repeated completion returns the original row."""
        self._require_engine()
        normalized_state = str(state or "").strip().lower()
        if normalized_state not in {LIFECYCLE_RUN_COMPLETED, LIFECYCLE_RUN_FAILED}:
            raise ValueError("lifecycle run state must be completed or failed")
        run_key = self._bounded(run_id, 64)
        result_value = self._bounded_json(result)
        error_value = self._bounded_json(error)
        timestamp = _now()
        with self._engine.begin() as connection:
            current = connection.execute(
                select(self._task_log_lifecycle_runs)
                .where(self._task_log_lifecycle_runs.c.run_id == run_key)
                .with_for_update()
            ).mappings().first()
            if current is None:
                raise KeyError(run_key)
            if str(current.get("state", "")).lower() != LIFECYCLE_RUN_RUNNING:
                return self._lifecycle_run_record(current)
            values = {
                "state": normalized_state,
                "finished_at": timestamp,
                "result_json": _dump(result_value),
                "error_json": _dump(error_value),
                "updated_at": timestamp,
            }
            connection.execute(
                update(self._task_log_lifecycle_runs)
                .where(self._task_log_lifecycle_runs.c.run_id == run_key)
                .where(self._task_log_lifecycle_runs.c.state == LIFECYCLE_RUN_RUNNING)
                .values(**values)
            )
            updated = dict(current)
            updated.update(values)
        return self._lifecycle_run_record(updated)

    def get_task_log_lifecycle_run(self, run_id: str) -> dict[str, object] | None:
        """Read one bounded lifecycle audit record."""
        self._require_engine()
        run_key = self._bounded(run_id, 64)
        with self._engine.connect() as connection:
            row = connection.execute(
                select(self._task_log_lifecycle_runs)
                .where(self._task_log_lifecycle_runs.c.run_id == run_key)
            ).mappings().first()
        return None if row is None else self._lifecycle_run_record(row)

    def list_task_log_lifecycle_runs(self, limit: int = 100) -> list[dict[str, object]]:
        """Return the newest lifecycle audit records in bounded order."""
        self._require_engine()
        bounded = max(1, min(int(limit), 500))
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(self._task_log_lifecycle_runs)
                .order_by(desc(self._task_log_lifecycle_runs.c.started_at))
                .limit(bounded)
            ).mappings().all()
        return [self._lifecycle_run_record(row) for row in rows]

    def list_task_log_records(self, limit: int = MAX_TASK_LOG_METADATA_ROWS) -> list[dict[str, object]]:
        """Read all task metadata needed by retention without the 500-row UI cap."""
        self._require_engine()
        bounded = max(1, min(int(limit), MAX_TASK_LOG_METADATA_ROWS))
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(
                    self._tasks.c.task_id,
                    self._tasks.c.kind,
                    self._tasks.c.status,
                    self._tasks.c.created_at,
                    self._tasks.c.updated_at,
                    self._tasks.c.finished_at,
                )
                .order_by(self._tasks.c.task_id)
                .limit(bounded + 1)
            ).mappings().all()
        if len(rows) > bounded:
            raise ValueError("task metadata exceeds the lifecycle query limit")
        return [
            {
                "task_id": str(row["task_id"]),
                "kind": str(row["kind"]),
                "status": str(row["status"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "finished_at": row["finished_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _lifecycle_run_record(row: Mapping[str, object]) -> dict[str, object]:
        return {
            "run_id": row.get("run_id"),
            "operation": row.get("operation"),
            "state": row.get("state"),
            "actor": row.get("actor"),
            "request_id": row.get("request_id"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
            "result": _load(row.get("result_json")),
            "error": _load(row.get("error_json")),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    @staticmethod
    def _bounded_json(value: Any) -> Any:
        safe = redact_log_value(value if value is not None else {})
        encoded = _dump(safe)
        if len(encoded.encode("utf-8")) > MAX_LIFECYCLE_RESULT_BYTES:
            raise ValueError("lifecycle audit result exceeds the 64 KiB limit")
        return safe
