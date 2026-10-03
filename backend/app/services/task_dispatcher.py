"""Shared dispatch, cancellation, retry, and execution context primitives."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import logging
import threading
from typing import Any, Callable
from uuid import uuid4

from .task_queue import RedisTaskQueue, TaskQueueError
from .task_quota import TaskQuota, TaskQuotaExceeded
from .task_result_archive import TaskResultArchive
from .task_store import TaskLeaseLost, TaskStore, TaskStoreError


TASK_KINDS = frozenset(
    {
        "backtest",
        "pool_backtest",
        "screening",
        "research",
        "paper_strategy",
        "paper_automation",
        "paper_follow",
        "strategy_matrix",
        "parameter_tune",
    }
)
TASK_PREFIXES = {
    "history_download": "history",
    "backtest": "backtest",
    "pool_backtest": "pool-backtest",
    "screening": "screening",
    "research": "research",
    "paper_strategy": "paper-strategy",
    "paper_automation": "paper-automation",
    "paper_follow": "paper-follow",
    "strategy_matrix": "strategy-matrix",
    "parameter_tune": "parameter-tune",
}
REPLAYABLE_TASK_KINDS = TASK_KINDS | {"history_download"}
TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "blocked", "interrupted", "partial", "dead_lettered"}
)
LOGGER = logging.getLogger("crypto.task_dispatcher")


class TaskDispatchError(RuntimeError):
    """The durable task could not be submitted or controlled."""


class TaskExecutionCancelled(RuntimeError):
    """Raised by a worker when an operator cancelled before execution."""


class TaskExecutionInterrupted(RuntimeError):
    """Raised when a worker is draining before a task reaches a terminal state."""


class TaskDispatcher:
    """Create one durable task and one Redis message for a long-running use case."""

    def __init__(
        self,
        store: TaskStore,
        queue: RedisTaskQueue | None,
        *,
        mode: str = "local",
        max_attempts: int = 3,
        quota: TaskQuota | None = None,
        history_replayer: Callable[[str, str, str], dict[str, object]] | None = None,
    ) -> None:
        self.store = store
        self.queue = queue
        self.mode = str(mode or "local").strip().lower()
        self.max_attempts = max(1, min(int(max_attempts), 10))
        self.quota = quota or TaskQuota()
        self.history_replayer = history_replayer

    @property
    def enabled(self) -> bool:
        return self.mode == "redis" and self.queue is not None

    @staticmethod
    def run_id() -> str:
        return uuid4().hex

    @staticmethod
    def task_id(kind: str, run_id: str) -> str:
        normalized = str(kind).strip().lower()
        prefix = TASK_PREFIXES.get(normalized, normalized.replace("_", "-"))
        return f"{prefix}:{str(run_id).strip()}"

    @staticmethod
    def idempotency_key(kind: str, payload: Any) -> str:
        value = deepcopy(payload) if isinstance(payload, dict) else payload
        if isinstance(value, dict):
            value.pop("run_id", None)
            value.pop("batch_id", None)
        encoded = json.dumps(
            {"kind": str(kind).strip().lower(), "payload": value},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"task:{sha256(encoded).hexdigest()}"

    @staticmethod
    def config_payload(config: Any) -> dict[str, Any]:
        return {
            "initial_quote": str(config.initial_quote),
            "initial_base": str(config.initial_base),
            "fee_bps": str(config.fee_bps),
            "slippage_bps": str(config.slippage_bps),
            "fast_window": int(config.fast_window),
            "slow_window": int(config.slow_window),
            "allocation_ratio": str(config.allocation_ratio),
            "momentum_threshold_pct": str(config.momentum_threshold_pct),
            "strategy_parameters": dict(config.parameters),
        }

    def dispatch(
        self,
        kind: str,
        title: str,
        payload: dict[str, Any],
        *,
        task_id: str,
        progress: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        retry_of: str | None = None,
        replay_source_task_id: str | None = None,
        replay_request_id: str | None = None,
    ) -> dict[str, object]:
        normalized_kind = str(kind).strip().lower()
        if normalized_kind not in TASK_KINDS:
            raise TaskDispatchError(f"unsupported task kind: {normalized_kind}")
        if not self.enabled:
            raise TaskDispatchError("redis task dispatch is not enabled")
        if not isinstance(payload, dict):
            raise TaskDispatchError("task payload must be an object")
        try:
            self.quota.validate_payload(payload)
        except TaskQuotaExceeded as error:
            raise TaskDispatchError(str(error)) from error
        task_id = str(task_id).strip()
        if not task_id:
            raise TaskDispatchError("task_id must not be empty")
        key = idempotency_key or self.idempotency_key(normalized_kind, payload)
        try:
            record = self.store.create(
                task_id,
                kind=normalized_kind,
                title=title,
                payload=payload,
                status="queued",
                progress=progress or {"completed": 0, "total": 1, "percent": 0.0},
                retry_of=retry_of,
                idempotency_key=key,
                max_attempts=self.max_attempts,
                stage_dispatch=True,
                replay_source_task_id=replay_source_task_id,
                replay_request_id=replay_request_id,
            )
        except (TaskStoreError, OSError, ValueError) as error:
            raise TaskDispatchError("unable to create durable task") from error

        actual_task_id = str(record.get("task_id") or task_id)
        deduplicated = bool(record.get("deduplicated") or actual_task_id != task_id)
        try:
            pending = self.store.pending_dispatches(
                task_id=actual_task_id,
                limit=1,
            )
        except (TaskStoreError, OSError) as error:
            raise TaskDispatchError("unable to read durable task dispatch") from error
        if not pending:
            if deduplicated:
                try:
                    self.store.stage_dispatch_for_task(actual_task_id)
                    pending = self.store.pending_dispatches(
                        task_id=actual_task_id,
                        limit=1,
                    )
                except (TaskStoreError, OSError) as error:
                    raise TaskDispatchError("unable to restore durable task dispatch") from error
            if not pending and deduplicated:
                return self.response(record, deduplicated=True)
            if not pending:
                raise TaskDispatchError("durable task dispatch intent is missing")
        publication = self.store.publish_pending_dispatches(
            self.queue,
            task_id=actual_task_id,
            limit=1,
        )
        published = publication.get("published") if isinstance(publication, dict) else []
        if not isinstance(published, list) or not published:
            failed = publication.get("failed") if isinstance(publication, dict) else []
            failure = failed[0].get("error") if isinstance(failed, list) and failed and isinstance(failed[0], dict) else {}
            try:
                self.store.append_event(
                    actual_task_id,
                    "dispatch_pending",
                    status="queued",
                    message="任务已持久化，等待 Redis 恢复后发布",
                    payload={"error": failure},
                )
            except Exception:
                LOGGER.exception("unable to record pending task dispatch: %s", actual_task_id)
            raise TaskDispatchError("unable to enqueue durable task")
        published_item = published[0] if isinstance(published[0], dict) else {}
        envelope = published_item.get("envelope")
        if not isinstance(envelope, dict):
            raise TaskDispatchError("task publisher returned an invalid envelope")
        try:
            self.store.append_event(
                actual_task_id,
                "queued",
                status="queued",
                message="任务已进入 Redis 队列",
                payload={
                    "message_id": envelope.get("message_id"),
                    "outbox": True,
                },
            )
        except Exception:
            # Queue delivery is already durable. A missing informational event
            # must not turn a successfully published task into a false failure.
            LOGGER.exception("unable to record queued task event: %s", actual_task_id)
        return self.response(
            self.store.get(actual_task_id) or record,
            deduplicated=deduplicated,
        )

    def _enqueue(
        self,
        task_id: str,
        kind: str,
        payload: dict[str, Any],
        *,
        max_attempts: int,
        message_id: str | None = None,
        replay_source_task_id: str | None = None,
        replay_request_id: str | None = None,
    ) -> dict[str, object]:
        if self.queue is None:
            raise TaskQueueError("redis task dispatch is not enabled")
        if replay_source_task_id and replay_request_id:
            replay = getattr(self.queue, "replay_dead_letter", None)
            if callable(replay):
                result = replay(
                    replay_source_task_id,
                    kind,
                    payload,
                    replay_task_id=task_id,
                    request_id=replay_request_id,
                    max_attempts=max_attempts,
                    message_id=message_id,
                )
                envelope = result.get("envelope") if isinstance(result, dict) else None
                return envelope if isinstance(envelope, dict) else {}
        return self.queue.enqueue(
            task_id,
            kind,
            payload,
            message_id=message_id,
            max_attempts=max_attempts,
        )

    @staticmethod
    def response(record: dict[str, object], *, deduplicated: bool = False) -> dict[str, object]:
        task_id = str(record.get("task_id", ""))
        return {
            "status": str(record.get("status", "queued")),
            "queued": True,
            "deduplicated": bool(deduplicated or record.get("deduplicated")),
            "task_id": task_id,
            "run_id": _run_id_from_task(task_id),
            "kind": str(record.get("kind", "task")),
            "task": record,
        }

    def cancel(self, task_id: str) -> dict[str, object]:
        record = self.store.get(task_id)
        if record is None:
            raise KeyError(task_id)
        current_status = str(record.get("status", ""))
        if current_status not in TERMINAL_STATUSES:
            updated = self.store.sync(task_id, status="cancelling", cancel_requested=True)
            self.store.append_event(
                task_id,
                "cancel_requested",
                status="cancelling",
                message="已请求停止任务，Worker 将在安全检查点结束",
            )
            record = updated or record
        return self.response(record)

    def retry(self, task_id: str) -> dict[str, object]:
        record = self.store.get(task_id)
        if record is None:
            raise KeyError(task_id)
        current_status = str(record.get("status", ""))
        if current_status == "dead_lettered":
            raise ValueError("dead-lettered task requires audited replay")
        if current_status not in TERMINAL_STATUSES:
            raise ValueError("active task cannot be retried")
        kind = str(record.get("kind", "")).strip().lower()
        if kind not in TASK_KINDS:
            raise ValueError("task kind cannot be retried by the generic dispatcher")
        payload = deepcopy(record.get("payload"))
        if not isinstance(payload, dict):
            raise ValueError("task payload cannot be retried")
        new_run_id = self.run_id()
        payload["run_id"] = new_run_id
        new_task_id = self.task_id(kind, new_run_id)
        return self.dispatch(
            kind,
            str(record.get("title") or "后台任务"),
            payload,
            task_id=new_task_id,
            retry_of=str(record.get("task_id")),
        )

    def replay_dead_letter(
        self,
        task_id: str,
        *,
        actor: str,
        reason: str,
        request_id: str | None = None,
    ) -> dict[str, object]:
        """Replay one terminal task through an idempotent audit reservation."""
        source_id = str(task_id).strip()
        record = self.store.get(source_id)
        if record is None:
            raise KeyError(source_id)
        if str(record.get("status", "")) != "dead_lettered":
            raise ValueError("only dead-lettered tasks can be replayed")
        kind = str(record.get("kind", "")).strip().lower()
        if kind not in REPLAYABLE_TASK_KINDS:
            raise ValueError("task kind cannot be replayed by the dead-letter dispatcher")
        request_key = str(request_id or uuid4().hex).strip()
        if not request_key or len(request_key) > 160:
            raise ValueError("replay request_id must contain between 1 and 160 characters")
        replay_run_id = f"replay-{sha256(f'{source_id}:{request_key}'.encode('utf-8')).hexdigest()[:32]}"
        replay_task_id = self.task_id(kind, replay_run_id)
        reservation = self.store.reserve_dead_letter_replay(
            source_id,
            replay_task_id=replay_task_id,
            request_id=request_key,
            actor=actor,
            reason=reason,
        )
        if str(reservation.get("status")) != "requested":
            return self._replay_response(reservation)

        existing = self.store.get(replay_task_id)
        if existing is not None:
            outcome_status = "failed" if str(existing.get("status")) in {"failed", "dead_lettered"} else "queued"
            outcome = {
                "status": str(existing.get("status", "queued")),
                "task_id": replay_task_id,
                "deduplicated": True,
            }
            audit = self.store.finish_dead_letter_replay(
                request_key,
                status=outcome_status,
                result=outcome if outcome_status == "queued" else {},
                error=existing.get("error", {}) if outcome_status == "failed" else {},
            )
            return self._replay_response(audit, task=existing)

        try:
            payload = deepcopy(record.get("payload"))
            if not isinstance(payload, dict):
                raise ValueError("task payload cannot be replayed")
            if kind == "history_download":
                if self.history_replayer is None:
                    raise TaskDispatchError("history task replay is not available")
                replay_result = self.history_replayer(source_id, replay_task_id, request_key)
            else:
                payload["run_id"] = replay_run_id
                replay_result = self.dispatch(
                    kind,
                    str(record.get("title") or "后台任务"),
                    payload,
                    task_id=replay_task_id,
                    idempotency_key=f"dead-letter-replay:{request_key}",
                    retry_of=source_id,
                    replay_source_task_id=source_id,
                    replay_request_id=request_key,
                )
            outcome = self._replay_outcome(replay_task_id, replay_result)
            audit = self.store.finish_dead_letter_replay(
                request_key,
                status="queued",
                result=outcome,
            )
            return self._replay_response(audit, task=self.store.get(replay_task_id))
        except Exception as error:
            failure = self._failure_payload(error)
            try:
                audit = self.store.finish_dead_letter_replay(
                    request_key,
                    status="failed",
                    error=failure,
                )
            except Exception as audit_error:
                raise TaskDispatchError("unable to persist dead-letter replay audit") from audit_error
            if isinstance(error, (TaskDispatchError, ValueError, TaskQueueError)):
                raise error
            raise TaskDispatchError("unable to replay dead-letter task") from error

    @staticmethod
    def _replay_outcome(task_id: str, result: object) -> dict[str, object]:
        values = result if isinstance(result, dict) else {}
        return {
            "status": str(values.get("status", "queued")),
            "task_id": task_id,
            "deduplicated": bool(values.get("deduplicated")),
        }

    @staticmethod
    def _replay_response(
        audit: dict[str, object],
        *,
        task: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "status": str(audit.get("status", "requested")),
            "queued": str(audit.get("status")) == "queued",
            "deduplicated": bool(audit.get("deduplicated")),
            "request_id": audit.get("request_id"),
            "source_task_id": audit.get("source_task_id"),
            "replay_task_id": audit.get("replay_task_id"),
            "task": task,
            "audit": audit,
        }

    @staticmethod
    def _failure_payload(error: Exception) -> dict[str, str]:
        return {
            "kind": str(error.__class__.__name__).upper()[:80],
            "message": str(error)[:500],
        }


class TaskExecutionContext:
    """Small worker-side helper for cancellation, progress, and event updates."""

    def __init__(
        self,
        store: TaskStore,
        task_id: str,
        *,
        quota: TaskQuota | None = None,
        result_archive: TaskResultArchive | None = None,
        worker_id: str | None = None,
        shutdown_event: threading.Event | None = None,
    ) -> None:
        self.store = store
        self.task_id = str(task_id)
        self.quota = quota or TaskQuota()
        self.result_archive = result_archive
        self.worker_id = str(worker_id or "").strip() or None
        self.shutdown_event = shutdown_event
        self._budget = None

    def start_budget(self, *, total: int = 1) -> None:
        self.quota.validate_items(total)
        self._budget = self.quota.budget()

    def check_budget(self) -> None:
        if self._budget is not None:
            self._budget.check()

    def cancelled(self) -> bool:
        record = self.store.get(self.task_id)
        return bool(record and (record.get("cancel_requested") or record.get("status") == "cancelling"))

    def raise_if_cancelled(self) -> None:
        self.raise_if_stopping()
        if self.cancelled():
            raise TaskExecutionCancelled("task cancellation was requested")

    def raise_if_stopping(self) -> None:
        if self.shutdown_event is not None and self.shutdown_event.is_set():
            raise TaskExecutionInterrupted("worker shutdown requested at a safe checkpoint")

    def started(self, *, total: int = 1) -> None:
        self.start_budget(total=total)
        record = self._sync(
            self.task_id,
            status="running",
            progress={"completed": 0, "total": max(1, int(total)), "percent": 0.0},
        )
        self._append_event(
            self.task_id,
            "started",
            status="running",
            message="Worker 已开始执行任务",
            record=record,
        )

    def progress(self, completed: int, total: int, *, message: str = "", **extra: Any) -> None:
        self.raise_if_stopping()
        self.quota.validate_items(total)
        self.check_budget()
        bounded_total = max(1, int(total))
        bounded_completed = max(0, min(int(completed), bounded_total))
        payload: dict[str, Any] = {
            "completed": bounded_completed,
            "total": bounded_total,
            "percent": round(bounded_completed / bounded_total * 100, 1),
            **extra,
        }
        record = self._sync(self.task_id, status="running", progress=payload)
        self._append_event(
            self.task_id,
            "progress",
            status="running",
            message=message or "任务进度已更新",
            payload=payload,
            record=record,
        )

    def completed(self, result: Any = None, *, progress: dict[str, Any] | None = None) -> None:
        self.check_budget()
        result_value = result if result is not None else {}
        self.quota.validate_result(result_value)
        result_status = "completed"
        if isinstance(result_value, dict):
            candidate = str(result_value.get("status", "completed")).strip().lower()
            if candidate in {"completed", "partial", "blocked"}:
                result_status = candidate
        stored_result: Any = result_value
        if self.result_archive is not None:
            stored_result = {"archive": self.result_archive.write(self.task_id, result_value)}
        event_payload: dict[str, Any] = {"summary": {"status": result_status}}
        if isinstance(stored_result, dict) and isinstance(stored_result.get("archive"), dict):
            event_payload["archive"] = stored_result["archive"]
        record = self._sync(
            self.task_id,
            status=result_status,
            progress=progress or {"completed": 1, "total": 1, "percent": 100.0},
            result=stored_result,
            cancel_requested=False,
        )
        self._append_event(
            self.task_id,
            result_status if result_status != "completed" else "completed",
            status=result_status,
            message="任务已完成" if result_status == "completed" else f"任务已结束：{result_status}",
            payload=event_payload,
            record=record,
        )

    def failed(self, error: Any) -> None:
        record = self._sync(
            self.task_id,
            status="failed",
            error=error,
            cancel_requested=False,
        )
        self._append_event(
            self.task_id,
            "failed",
            status="failed",
            message="Worker 执行任务失败",
            payload=error,
            record=record,
        )

    def retry_scheduled(self, error: Any, *, attempt: int, max_attempts: int) -> None:
        scheduler = getattr(self.store, "schedule_retry", None)
        if callable(scheduler):
            arguments = {"attempt": attempt, "error": error}
            if self.worker_id:
                arguments["expected_worker_id"] = self.worker_id
            record = scheduler(self.task_id, **arguments)
            if self.worker_id and record is None:
                raise TaskLeaseLost("task lease is no longer owned by worker")
            return
        record = self._sync(
            self.task_id,
            status="queued",
            error=error,
            attempt=attempt,
            worker_id=None,
            lease_expires_at=None,
            cancel_requested=False,
        )
        self._append_event(
            self.task_id,
            "retry_scheduled",
            status="queued",
            message="任务失败后已重新排队",
            payload={"attempt": attempt, "max_attempts": max_attempts, "error": error},
            record=record,
        )

    def quota_blocked(self, error: TaskQuotaExceeded) -> None:
        record = self._sync(
            self.task_id,
            status="blocked",
            error=error.as_error(),
            worker_id=None,
            lease_expires_at=None,
            cancel_requested=False,
        )
        self._append_event(
            self.task_id,
            "quota_blocked",
            status="blocked",
            message="任务超过服务端资源配额，已阻断",
            payload=error.as_error(),
            record=record,
        )

    def dead_lettered(
        self,
        error: Any,
        *,
        attempt: int,
        dead_letter: dict[str, object] | None = None,
    ) -> None:
        record = self.store.dead_letter(
            self.task_id,
            error=error,
            attempt=attempt,
            dead_letter=dead_letter,
            expected_worker_id=self.worker_id,
        )
        if self.worker_id and record is None:
            raise TaskLeaseLost("task lease is no longer owned by worker")

    def cancelled_result(self, result: Any = None) -> None:
        record = self._sync(
            self.task_id,
            status="cancelled",
            result=result if result is not None else {},
            error={"kind": "CANCELLED", "message": "任务已由操作员停止"},
            cancel_requested=False,
        )
        self._append_event(
            self.task_id,
            "cancelled",
            status="cancelled",
            message="任务已停止",
            record=record,
        )

    def _sync(self, task_id: str, **kwargs: Any) -> dict[str, object] | None:
        if self.worker_id:
            kwargs["expected_worker_id"] = self.worker_id
        record = self.store.sync(task_id, **kwargs)
        if self.worker_id and record is None:
            raise TaskLeaseLost("task lease is no longer owned by worker")
        return record

    def _append_event(
        self,
        task_id: str,
        event_type: str,
        *,
        record: dict[str, object] | None,
        **kwargs: Any,
    ) -> None:
        if self.worker_id:
            if str((record or {}).get("status", "")) in {"running", "cancelling"}:
                kwargs["expected_worker_id"] = self.worker_id
            elif record is not None:
                kwargs["expected_version"] = int(record["version"])
        self.store.append_event(task_id, event_type, **kwargs)


def _run_id_from_task(task_id: str) -> str | None:
    value = str(task_id).split(":", 1)
    return value[1] if len(value) == 2 and value[1] else None
