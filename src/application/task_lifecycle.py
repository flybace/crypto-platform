"""Small, failure-tolerant bridge from domain runs to the task ledger."""

from __future__ import annotations

import logging
from typing import Any, Protocol


LOGGER = logging.getLogger(__name__)


class TaskLedger(Protocol):
    """The subset of the durable task store used by domain services."""

    def get(self, task_id: str) -> dict[str, object] | None: ...

    def create(
        self,
        task_id: str,
        *,
        kind: str,
        title: str,
        payload: Any = None,
        status: str = "queued",
        progress: Any = None,
        retry_of: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, object]: ...

    def sync(self, task_id: str, *, status: str | None = None, progress: Any = None, result: Any = None, error: Any = None, cancel_requested: bool | None = None) -> dict[str, object] | None: ...

    def append_event(self, task_id: str, event_type: str, *, status: str | None = None, message: str = "", payload: Any = None) -> dict[str, object]: ...


class TaskLifecycle:
    """Project a synchronous domain run into a durable task lifecycle.

    The domain result remains authoritative. Ledger failures are logged and do
    not turn a completed research or paper run into an API error.
    """

    def __init__(
        self,
        ledger: TaskLedger | None,
        task_id: str,
        *,
        kind: str,
        title: str,
        payload: Any = None,
    ) -> None:
        self._ledger = ledger
        self.task_id = str(task_id)
        self.kind = str(kind)
        self.title = str(title)
        self.payload = payload if payload is not None else {}

    def completed(self, *, result: Any = None, progress: dict[str, object] | None = None) -> None:
        self._ensure()
        self._call(
            "sync",
            self.task_id,
            status="completed",
            progress=progress or {"completed": 1, "total": 1, "percent": 100.0},
            result=result if result is not None else {},
        )
        self._event("completed", "任务已完成", progress or {"completed": 1, "total": 1})

    def failed(self, *, error: Any, progress: dict[str, object] | None = None) -> None:
        self._ensure()
        self._call(
            "sync",
            self.task_id,
            status="failed",
            progress=progress or {"completed": 0, "total": 1, "percent": 0.0},
            error=error,
        )
        self._event("failed", "任务执行失败", error)

    def _ensure(self) -> None:
        if self._ledger is None:
            return
        try:
            if self._ledger.get(self.task_id) is None:
                self._ledger.create(
                    self.task_id,
                    kind=self.kind,
                    title=self.title,
                    payload=self.payload,
                    status="queued",
                    progress={"completed": 0, "total": 1, "percent": 0.0},
                )
        except Exception as error:  # pragma: no cover - runtime database failure
            LOGGER.warning("task ledger create failed for %s: %s", self.task_id, error)

    def _event(self, event_type: str, message: str, payload: Any) -> None:
        self._call(
            "append_event",
            self.task_id,
            event_type,
            status="completed" if event_type == "completed" else "failed",
            message=message,
            payload=payload,
        )

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if self._ledger is None:
            return None
        try:
            return getattr(self._ledger, method)(*args, **kwargs)
        except Exception as error:  # pragma: no cover - runtime database failure
            LOGGER.warning("task ledger %s failed for %s: %s", method, self.task_id, error)
            return None
