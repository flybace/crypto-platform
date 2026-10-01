"""Unified research and paper task view for the standalone control plane."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..auth.service import AuthenticatedUser
from ..auth.dependencies import require_user
from ..services.task_dispatcher import TaskDispatchError, TaskDispatcher
from ..services.task_log_archive import MANIFEST_CONTRACT_VERSION, TaskLogArchiveError
from ..services.task_log_lifecycle import TaskLogLifecycleError
from ..services.task_result_archive import (
    CONTRACT_VERSION as TASK_RESULT_CONTRACT_VERSION,
    TaskResultArchiveError,
)


router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

ACTIVE_TASK_STATUSES = frozenset({"queued", "running", "cancelling", "open", "accepted", "partially_filled"})
FAILED_TASK_STATUSES = frozenset({"failed", "blocked", "partial", "cancelled", "rejected", "interrupted", "dead_lettered"})


class DeadLetterReplayRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    request_id: str | None = Field(default=None, min_length=1, max_length=160)


class TaskLogLifecycleOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replace: bool = False
    include_files: bool = False
    limit: int = Field(default=1000, ge=1, le=10_000)


class TaskLogRestoreRehearsalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Rehearsals must be isolated from an existing target.  The suffix is
    # intentionally a name fragment, never an operator-supplied filesystem path.
    target_suffix: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
    )


class TaskLogRetentionPurgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: dict[str, Any]
    confirmation: str = Field(min_length=1, max_length=64)


def _task_log_lifecycle(request: Request):
    lifecycle = getattr(request.app.state, "task_log_lifecycle", None)
    if lifecycle is None:
        raise HTTPException(status_code=503, detail="task log lifecycle is unavailable")
    return lifecycle


def _configured_task_log_path(request: Request, attribute: str, label: str) -> Path:
    value = getattr(request.app.state, attribute, None)
    if not isinstance(value, Path) or not str(value):
        raise TaskLogLifecycleError(f"{label} is not configured")
    return value


def _restore_rehearsal_target(
    request: Request,
    payload: TaskLogRestoreRehearsalRequest | None,
) -> Path:
    configured = _configured_task_log_path(
        request,
        "task_log_restore_path",
        "task log restore path",
    )
    suffix = payload.target_suffix if payload is not None else None
    if not suffix:
        return configured
    parent = configured.parent.resolve()
    target = parent / f"{configured.name}-{suffix}"
    try:
        target.resolve().relative_to(parent)
    except ValueError as error:
        raise TaskLogLifecycleError("restore rehearsal target is outside the configured directory") from error
    return target


def _finish_lifecycle_failure(request: Request, run_id: str, error: Exception) -> None:
    try:
        request.app.state.task_store.finish_task_log_lifecycle_run(
            run_id,
            state="failed",
            error={
                "kind": error.__class__.__name__.upper()[:80],
                "message": str(error)[:500],
            },
        )
    except Exception:
        # The original operation failure remains the response. A missing
        # audit row is visible through the lifecycle store health check.
        pass


def _lifecycle_audit_projection(value: Any) -> object:
    """Keep lifecycle audit rows to scalar health data, never full inventories."""
    if isinstance(value, Mapping):
        allowed = {
            "status",
            "state",
            "ok",
            "backup_ready",
            "task_count",
            "event_object_count",
            "manifest_count",
            "bytes",
            "orphan_object_count",
            "corrupt_count",
            "missing_manifest_count",
            "unexpected_file_count",
            "transient_file_count",
            "inventory_sha256",
            "contract_version",
            "archive_contract_version",
            "sha256",
            "file_count",
            "target_root",
            "plan_id",
            "candidate_count",
            "sql_task_record_count",
            "event_count",
            "ready_count",
            "pending_count",
            "failed_count",
        }
        return {
            str(key): _lifecycle_audit_projection(item)
            for key, item in value.items()
            if str(key) in allowed
            and isinstance(item, (Mapping, list, tuple, str, int, float, bool, type(None)))
        }
    if isinstance(value, (list, tuple)):
        return {"count": len(value)}
    return value


def _run_task_log_lifecycle(
    request: Request,
    user: AuthenticatedUser,
    operation: str,
    action: Callable[[], dict[str, object]],
) -> dict[str, object]:
    try:
        run = request.app.state.task_store.start_task_log_lifecycle_run(
            operation,
            user.username,
        )
    except Exception as error:
        raise HTTPException(status_code=503, detail="task log lifecycle audit is unavailable") from error
    try:
        result = action()
    except (TaskLogLifecycleError, ValueError) as error:
        _finish_lifecycle_failure(request, str(run["run_id"]), error)
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OSError as error:
        _finish_lifecycle_failure(request, str(run["run_id"]), error)
        raise HTTPException(status_code=503, detail="task log lifecycle storage is unavailable") from error
    except Exception as error:
        _finish_lifecycle_failure(request, str(run["run_id"]), error)
        raise HTTPException(status_code=503, detail="task log lifecycle operation failed") from error
    try:
        completed = request.app.state.task_store.finish_task_log_lifecycle_run(
            str(run["run_id"]),
            state="completed",
            result=_lifecycle_audit_projection(result),
        )
    except Exception as error:
        raise HTTPException(status_code=503, detail="task log lifecycle audit could not be committed") from error
    return {**result, "run": completed}


def _history_task_item(job: dict[str, object]) -> dict[str, object]:
    status = str(job.get("status", "unknown"))
    total = int(job.get("total", 0) or 0)
    completed = int(job.get("completed", 0) or 0)
    return {
        "task_id": f"history:{job['job_id']}",
        "kind": "history_download",
        "status": status,
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "progress": {
            "completed": completed,
            "total": total,
            "blocked": int(job.get("blocked", 0) or 0),
            "failed": int(job.get("failed", 0) or 0),
            "interrupted": int(job.get("interrupted", 0) or 0),
            "cancelled": int(job.get("cancelled", 0) or 0),
            "percent": round(completed / max(1, total) * 100, 1),
        },
        "source_id": job["job_id"],
        "detail_url": f"/history/jobs/{job['job_id']}",
        "cancellable": status in {"queued", "running"},
        "retryable": status in {"blocked", "failed", "partial", "cancelled", "interrupted", "dead_lettered"},
        "label": "历史数据下载",
    }


def _ledger_task_item(task: dict[str, object]) -> dict[str, object]:
    """Project a task created by a sibling process into the task center."""
    task_id = str(task.get("task_id", ""))
    kind = str(task.get("kind", "task"))
    status = str(task.get("status", "unknown"))
    progress = task.get("progress") if isinstance(task.get("progress"), dict) else {}
    source_id = task_id.split(":", 1)[1] if ":" in task_id else task_id
    return {
        "task_id": task_id,
        "kind": kind,
        "status": status,
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "progress": dict(progress),
        "source_id": source_id,
        "detail_url": (
            f"/history/jobs/{source_id}"
            if kind == "history_download"
            else f"/tasks/{task_id}"
        ),
        "cancellable": status in {"queued", "running", "cancelling"},
        "retryable": status in {"blocked", "failed", "partial", "cancelled", "interrupted", "dead_lettered"},
        "label": str(task.get("title") or "后台任务"),
    }


def _items(request: Request, limit: int) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for job in request.app.state.history_jobs.list(limit):
        records.append(_history_task_item(job))
    for run in request.app.state.screening_service.list(limit):
        records.append({
            "task_id": f"screening:{run['run_id']}",
            "kind": "screening",
            "status": run["status"],
            "created_at": run["created_at"],
            "updated_at": run["created_at"],
            "progress": {"completed": run["dataset_count"], "total": run["dataset_count"], "candidates": run["candidate_count"]},
            "label": "历史指标筛选",
        })
    for run in request.app.state.backtest_runs.list(limit):
        records.append({
            "task_id": f"backtest:{run['run_id']}",
            "kind": "backtest",
            "status": run["status"],
            "created_at": run["created_at"],
            "updated_at": run["updated_at"],
            "progress": {"completed": 1, "total": 1},
            "label": "策略回测",
        })
    for run in request.app.state.research_runs.list(limit):
        records.append({
            "task_id": f"research:{run['run_id']}",
            "kind": str(run.get("kind", "research")),
            "status": run.get("status", "completed"),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "progress": {"completed": 1, "total": 1, "dataset_count": run.get("dataset_count", 0)},
            "label": "研究任务",
        })
    for order in request.app.state.paper_trading.orders(limit):
        records.append({
            "task_id": f"paper:{order['order_id']}",
            "kind": "paper_order",
            "status": str(order["status"]).lower(),
            "created_at": order["created_at"],
            "updated_at": order["created_at"],
            "progress": {"filled_quantity": order["filled_quantity"], "quantity": order["quantity"]},
            "label": "模拟盘订单",
        })
    for run in request.app.state.paper_trading.strategy_runs(limit):
        records.append({
            "task_id": f"paper-strategy:{run['run_id']}",
            "kind": "paper_strategy",
            "status": run.get("status", "completed"),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "progress": {"completed": 1, "total": 1, "orders": run.get("orders", 0)},
            "label": "模拟策略回放",
        })
    for matrix in request.app.state.strategy_matrices.list(limit):
        last_run = matrix.get("last_run") if isinstance(matrix.get("last_run"), dict) else None
        matrix_task_id = f"strategy-matrix:{last_run['run_id']}" if last_run and last_run.get("run_id") else f"strategy-matrix:{matrix['matrix_id']}"
        records.append({
            "task_id": matrix_task_id,
            "kind": "strategy_matrix",
            "status": str(matrix.get("status", "draft")),
            "created_at": matrix.get("created_at"),
            "updated_at": matrix.get("updated_at"),
            "progress": {
                "completed": last_run.get("completed_count", 0) if last_run else 0,
                "total": last_run.get("item_count", 0) if last_run else 0,
                "strategies": len(matrix.get("strategy_ids", [])),
                "datasets": len(matrix.get("dataset_ids", [])),
            },
            "label": "策略矩阵研究",
        })
    seen = {str(item.get("task_id", "")) for item in records}
    try:
        persisted_tasks = request.app.state.task_store.list(max(int(limit), 100))
    except Exception:
        persisted_tasks = []
    for persisted in persisted_tasks:
        item = _ledger_task_item(persisted)
        existing = next((record for record in records if record.get("task_id") == item["task_id"]), None)
        if existing is not None:
            # The domain projection owns result-specific fields; the durable
            # ledger owns lifecycle and operator controls across processes.
            existing.update({
                "status": item["status"],
                "created_at": item["created_at"] or existing.get("created_at"),
                "updated_at": item["updated_at"] or existing.get("updated_at"),
                "progress": item["progress"] or existing.get("progress", {}),
                "detail_url": item["detail_url"],
                "cancellable": item["cancellable"],
                "retryable": item["retryable"],
            })
        elif item["task_id"]:
            records.append(item)
            seen.add(str(item["task_id"]))
    records.sort(key=lambda item: str(item["created_at"]), reverse=True)
    return records[: max(1, min(int(limit), 200))]


@router.get("")
def tasks(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    _: object = Depends(require_user),
) -> dict[str, object]:
    items = _items(request, limit)
    return {"items": items, "count": len(items)}


@router.get("/summary")
def summary(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    items = _items(request, 200)
    active = sum(1 for item in items if item["status"] in ACTIVE_TASK_STATUSES)
    failed = sum(1 for item in items if item["status"] in FAILED_TASK_STATUSES)
    persistence = request.app.state.task_store.status()
    return {
        "total": len(items),
        "active": active,
        "failed": failed,
        "dead_lettered": sum(1 for item in items if item["status"] == "dead_lettered"),
        "completed": len(items) - active - failed,
        "persistence": persistence,
    }


@router.get("/dead-letters")
def dead_letters(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Expose bounded dead-letter metadata for operator recovery."""
    try:
        records = request.app.state.task_store.dead_letters(limit)
    except Exception as error:
        raise HTTPException(status_code=503, detail="task dead-letter store is unavailable") from error
    items = [
        {
            "task_id": record.get("task_id"),
            "kind": record.get("kind"),
            "status": record.get("status"),
            "attempt": record.get("attempt", 0),
            "max_attempts": record.get("max_attempts", 0),
            "dead_lettered_at": record.get("dead_lettered_at"),
            "error": record.get("error", {}),
            "updated_at": record.get("updated_at"),
            "replays": request.app.state.task_store.dead_letter_replays(
                str(record.get("task_id", "")),
                limit=20,
            ),
        }
        for record in records
    ]
    return {"items": items, "count": len(items)}


@router.get("/log-archive")
def task_log_archive_inventory(
    request: Request,
    verify_objects: bool = Query(default=True),
    include_files: bool = Query(default=False),
    limit: int = Query(default=1000, ge=1, le=10_000),
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Return authenticated, bounded task-log archive health and inventory."""
    lifecycle = getattr(request.app.state, "task_log_lifecycle", None)
    if lifecycle is None:
        raise HTTPException(status_code=503, detail="task log lifecycle is unavailable")
    try:
        inventory = lifecycle.inspect(
            verify_objects=verify_objects,
            include_tasks=True,
            include_files=include_files,
            limit=limit,
        )
        sql_status = request.app.state.task_store.event_archive_status()
    except (OSError, TaskLogLifecycleError, ValueError) as error:
        raise HTTPException(status_code=503, detail="task log archive inventory is unavailable") from error
    return {"archive": inventory, "sql": sql_status}


@router.post("/log-archive/verify")
def verify_task_log_archive(
    payload: TaskLogLifecycleOperationRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_user),
) -> dict[str, object]:
    """Verify every current task-log object and its SQL references."""
    lifecycle = _task_log_lifecycle(request)

    def action() -> dict[str, object]:
        inventory = lifecycle.inspect(
            verify_objects=True,
            include_tasks=True,
            include_files=payload.include_files,
            limit=payload.limit,
        )
        return {
            "archive": inventory,
            "sql": request.app.state.task_store.event_archive_status(),
        }

    return _run_task_log_lifecycle(request, user, "verify", action)


@router.post("/log-archive/backup")
def export_task_log_backup(
    payload: TaskLogLifecycleOperationRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_user),
) -> dict[str, object]:
    """Export a verified archive to the configured backup target."""
    lifecycle = _task_log_lifecycle(request)

    def action() -> dict[str, object]:
        backup_path = _configured_task_log_path(request, "task_log_backup_path", "task log backup path")
        return {"backup": lifecycle.export_backup(backup_path, replace=payload.replace)}

    return _run_task_log_lifecycle(request, user, "export_backup", action)


@router.post("/log-archive/backup/verify")
def verify_task_log_backup(
    request: Request,
    user: AuthenticatedUser = Depends(require_user),
) -> dict[str, object]:
    """Verify the backup at the configured target without restoring it."""
    lifecycle = _task_log_lifecycle(request)

    def action() -> dict[str, object]:
        backup_path = _configured_task_log_path(request, "task_log_backup_path", "task log backup path")
        return {"backup": lifecycle.verify_backup(backup_path)}

    return _run_task_log_lifecycle(request, user, "verify_backup", action)


@router.post("/log-archive/restore-rehearsal")
def restore_task_log_rehearsal(
    request: Request,
    payload: TaskLogRestoreRehearsalRequest | None = None,
    user: AuthenticatedUser = Depends(require_user),
) -> dict[str, object]:
    """Restore a verified backup into a new, isolated configured directory."""
    lifecycle = _task_log_lifecycle(request)

    def action() -> dict[str, object]:
        backup_path = _configured_task_log_path(request, "task_log_backup_path", "task log backup path")
        restore_path = _restore_rehearsal_target(request, payload)
        return {"restore": lifecycle.restore_backup(backup_path, restore_path)}

    return _run_task_log_lifecycle(request, user, "restore_rehearsal", action)


@router.post("/log-archive/retention/plan")
def plan_task_log_retention(
    payload: TaskLogLifecycleOperationRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_user),
) -> dict[str, object]:
    """Build a read-only retention plan from the complete SQL task ledger."""
    lifecycle = _task_log_lifecycle(request)

    def action() -> dict[str, object]:
        records = request.app.state.task_store.list_task_log_records()
        plan = lifecycle.plan_retention(records, limit=payload.limit)
        return {
            "plan": plan,
            "sql_task_record_count": len(records),
        }

    return _run_task_log_lifecycle(request, user, "retention_plan", action)


@router.post("/log-archive/retention/purge")
def purge_task_log_retention(
    payload: TaskLogRetentionPurgeRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_user),
) -> dict[str, object]:
    """Purge only a verified plan after explicit operator confirmation."""
    lifecycle = _task_log_lifecycle(request)

    def action() -> dict[str, object]:
        backup_path = _configured_task_log_path(request, "task_log_backup_path", "task log backup path")
        result = lifecycle.purge_retention_plan(
            payload.plan,
            backup=backup_path,
            confirmation=payload.confirmation,
        )
        return {
            "purge": result,
            "sql": request.app.state.task_store.event_archive_status(),
        }

    return _run_task_log_lifecycle(request, user, "retention_purge", action)


@router.get("/log-archive/runs")
def task_log_lifecycle_runs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Return bounded audit history for lifecycle operations."""
    try:
        items = request.app.state.task_store.list_task_log_lifecycle_runs(limit)
    except Exception as error:
        raise HTTPException(status_code=503, detail="task log lifecycle audit is unavailable") from error
    return {"items": items, "count": len(items)}


@router.post("/dead-letters/{task_id}/replay", status_code=status.HTTP_202_ACCEPTED)
def replay_dead_letter(
    task_id: str,
    payload: DeadLetterReplayRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_user),
) -> dict[str, object]:
    """Replay a dead task only through the durable, authenticated audit path."""
    try:
        result = request.app.state.task_dispatcher.replay_dead_letter(
            task_id,
            actor=user.username,
            reason=payload.reason,
            request_id=payload.request_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="task was not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except TaskDispatchError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    task = result.get("task") if isinstance(result.get("task"), dict) else None
    return {
        **result,
        "task": _ledger_task_item(task) if task is not None else None,
        "ledger": task,
    }


def _history_job_id(task_id: str) -> str:
    prefix, separator, value = str(task_id).partition(":")
    if prefix != "history" or not separator or not value.strip():
        raise HTTPException(status_code=422, detail="only history tasks support this operation")
    return value


def _task_item(request: Request, task_id: str) -> dict[str, object]:
    item = next((value for value in _items(request, 200) if value["task_id"] == task_id), None)
    if item is not None:
        return item
    if str(task_id).startswith("history:"):
        job = request.app.state.history_jobs.get(_history_job_id(task_id))
        if job is not None:
            return _history_task_item(job)
    raise HTTPException(status_code=404, detail="task was not found")


def _domain_detail(
    request: Request,
    task_id: str,
    ledger: dict[str, object] | None,
) -> dict[str, object] | None:
    """Resolve the durable task to its domain result when one exists.

    The API and Worker are separate processes in Redis mode. Bounded domain
    services persist their result to the PostgreSQL-backed snapshot table;
    legacy JSON is only a migration and best-effort recovery export. The
    file-oriented history job manager still keeps its JSON recovery projection,
    while the SQL ledger remains the source of truth for task lifecycle state.
    Keep both projections in one response so the UI can poll a task without
    guessing which store to read.
    """
    raw_task_id = str(task_id).strip()
    prefix, separator, value = raw_task_id.partition(":")
    if not separator or not value:
        return _pending_detail(raw_task_id, ledger)

    payload = ledger.get("payload") if isinstance(ledger, dict) else None
    payload = payload if isinstance(payload, dict) else {}
    result = _task_result(request, raw_task_id, ledger)
    result = result if isinstance(result, dict) else {}

    if prefix == "backtest":
        record = request.app.state.backtest_runs.get(value)
        return record or result or _pending_detail(raw_task_id, ledger)
    if prefix == "pool-backtest":
        return result or _pending_detail(raw_task_id, ledger)
    if prefix == "screening":
        record = request.app.state.screening_service.get(value)
        return record or result or _pending_detail(raw_task_id, ledger)
    if prefix == "research":
        record = request.app.state.research_runs.get(value)
        return record or result or _pending_detail(raw_task_id, ledger)
    if prefix == "paper-strategy":
        record = next(
            (
                item
                for item in request.app.state.paper_trading.strategy_runs(100)
                if str(item.get("run_id", "")) == value
            ),
            None,
        )
        return record or result or _pending_detail(raw_task_id, ledger)
    if prefix == "paper-automation":
        if result:
            return result
        return {
            "run_id": value,
            "status": str(ledger.get("status", "unknown")) if ledger else "unknown",
            "automation": payload.get("config") or request.app.state.paper_automation.get(),
            "run": None,
        }
    if prefix == "strategy-matrix":
        matrix_id = str(payload.get("matrix_id", "")).strip()
        if not matrix_id and request.app.state.strategy_matrices.get(value) is not None:
            matrix_id = value
        matrix = request.app.state.strategy_matrices.get(matrix_id) if matrix_id else None
        if result:
            return result
        last_run = matrix.get("last_run") if isinstance(matrix, dict) else None
        if isinstance(last_run, dict) and str(last_run.get("run_id", "")) == value:
            return {"matrix": matrix, "run": last_run}
        return {
            "matrix": matrix,
            "run": None,
            "run_id": value,
            "status": str(ledger.get("status", "unknown")) if ledger else "unknown",
        }
    return result or _pending_detail(raw_task_id, ledger)


def _pending_detail(task_id: str, ledger: dict[str, object] | None) -> dict[str, object]:
    """Return a bounded, non-secret detail projection for an unfinished task."""
    record = ledger if isinstance(ledger, dict) else {}
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    return {
        "task_id": task_id,
        "run_id": task_id.split(":", 1)[1] if ":" in task_id else None,
        "kind": record.get("kind", "task"),
        "status": record.get("status", "unknown"),
        "progress": record.get("progress", {}),
        "payload": payload,
        "error": record.get("error", {}),
    }


def _task_result(
    request: Request,
    task_id: str,
    ledger: dict[str, object] | None,
) -> object:
    raw_result = ledger.get("result") if isinstance(ledger, dict) else None
    if not isinstance(raw_result, dict):
        return raw_result if raw_result is not None else {}
    reference = _task_result_archive_reference(raw_result)
    archive = getattr(request.app.state, "task_result_archive", None)
    if not isinstance(reference, dict) or archive is None:
        return raw_result
    try:
        return archive.read(reference, expected_task_id=task_id)
    except TaskResultArchiveError:
        return {
            "archive": reference,
            "archive_error": {
                "kind": "TASK_RESULT_ARCHIVE_UNAVAILABLE",
                "message": "task result archive could not be verified",
            },
        }


def _task_result_archive_reference(raw_result: dict[str, object]) -> dict[str, object] | None:
    """Recognize only task-result-v1 references as external task results.

    History jobs already use ``archive`` for their Parquet/archive summary.
    Keep that legacy domain result inline instead of treating it as a task
    result object and returning a false archive failure.
    """
    reference = raw_result.get("archive")
    if (
        isinstance(reference, dict)
        and str(reference.get("contract_version", "")) == TASK_RESULT_CONTRACT_VERSION
    ):
        return reference
    return None


@router.get("/{task_id}")
def task_detail(task_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    if str(task_id).startswith("history:"):
        job = request.app.state.history_jobs.get(_history_job_id(task_id))
        if job is None:
            raise HTTPException(status_code=404, detail="task was not found")
        ledger, events = _ledger_snapshot(request, task_id)
        return {
            "task": _history_task_item(job),
            "detail": job,
            "ledger": ledger,
            "events": events,
        }
    item = _task_item(request, task_id)
    ledger, events = _ledger_snapshot(request, task_id)
    return {
        "task": item,
        "detail": _domain_detail(request, task_id, ledger),
        "ledger": ledger,
        "events": events,
    }


@router.get("/{task_id}/result")
def task_result(task_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    ledger, _ = _ledger_snapshot(request, task_id)
    if ledger is None:
        raise HTTPException(status_code=404, detail="task was not found")
    result = _task_result(request, task_id, ledger)
    if isinstance(result, dict) and result.get("archive_error"):
        raise HTTPException(status_code=503, detail="task result archive is unavailable")
    raw_result = ledger.get("result") if isinstance(ledger, dict) else None
    reference = _task_result_archive_reference(raw_result) if isinstance(raw_result, dict) else None
    return {
        "task_id": task_id,
        "status": ledger.get("status", "unknown"),
        "result": result,
        "archive": reference if isinstance(reference, dict) else None,
    }


@router.get("/{task_id}/events")
def task_events(
    task_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Read durable lifecycle events for a task without exposing secrets."""
    ledger, events = _ledger_snapshot(request, task_id, limit)
    if not events and ledger is None:
        raise HTTPException(status_code=404, detail="task was not found")
    return {"task_id": task_id, "items": events, "count": len(events)}


@router.get("/{task_id}/logs")
def task_logs(
    task_id: str,
    request: Request,
    limit: int = Query(default=500, ge=1, le=500),
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Read the verified task-log archive for an authenticated task."""
    ledger, events = _ledger_snapshot(request, task_id, limit)
    if not events and ledger is None:
        raise HTTPException(status_code=404, detail="task was not found")
    archive = getattr(request.app.state, "task_log_archive", None)
    if archive is None:
        raise HTTPException(status_code=503, detail="task log archive is unavailable")
    try:
        manifest = archive.read_manifest(task_id, verify_objects=True)
        items = archive.list(task_id, limit=limit)
        manifest_reference = archive.manifest_reference(task_id)
    except TaskLogArchiveError as error:
        raise HTTPException(status_code=503, detail="task log archive could not be verified") from error
    event_count = int(manifest.get("event_count", 0)) if manifest is not None else 0
    state = "READY" if manifest is not None else ("READY" if not events else "PENDING")
    return {
        "task_id": task_id,
        "items": items,
        "count": len(items),
        "archive": {
            "contract_version": "task-log-v1",
            "manifest_contract_version": MANIFEST_CONTRACT_VERSION,
            "state": state,
            "event_count": event_count,
            "returned_count": len(items),
            "missing_count": 0 if manifest is not None else len(events),
            "manifest": manifest_reference,
        },
    }


def _ledger_snapshot(
    request: Request,
    task_id: str,
    limit: int = 100,
) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
    try:
        store = request.app.state.task_store
        return store.get(task_id), store.events(task_id, limit)
    except Exception:
        # The file-backed domain state remains readable when optional
        # persistence is temporarily unavailable.
        return None, []


@router.post("/{task_id}/cancel")
def cancel_task(task_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    if not str(task_id).startswith("history:"):
        try:
            result = request.app.state.task_dispatcher.cancel(task_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="task was not found") from error
        except TaskDispatchError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        task = result.get("task") if isinstance(result.get("task"), dict) else {}
        return {
            "task": _ledger_task_item(task),
            "ledger": task,
        }
    try:
        job = request.app.state.history_jobs.cancel(_history_job_id(task_id))
    except KeyError as error:
        raise HTTPException(status_code=404, detail="task was not found") from error
    return {"task": _history_task_item(job), "detail": job}


@router.post("/{task_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_task(task_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    if not str(task_id).startswith("history:"):
        try:
            result = request.app.state.task_dispatcher.retry(task_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="task was not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except TaskDispatchError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        task = result.get("task") if isinstance(result.get("task"), dict) else {}
        return {
            "task": _ledger_task_item(task),
            "ledger": task,
        }
    try:
        job = request.app.state.history_jobs.retry(_history_job_id(task_id))
    except KeyError as error:
        raise HTTPException(status_code=404, detail="task was not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"task": _history_task_item(job), "detail": job}
