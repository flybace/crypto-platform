"""Bounded background jobs for public historical downloads."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import hashlib
import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from uuid import uuid4

from application.history_archive import ParquetHistoryArchive
from application.history_download import HistoryDownloadError, HistoryDownloadService
from application.history_storage import HistoryStorageError
from domain.candle import CandleInterval, HistoryQuery
from domain.market import MarketType
from ports.history import HistoryMetadataError
from ports.rest import PublicRestError

from .task_store import TaskStore
from .task_queue import RedisTaskQueue, TaskQueueError


_HISTORY_JOB_STATUSES = frozenset(
    {
        "queued",
        "running",
        "cancelling",
        "completed",
        "partial",
        "blocked",
        "failed",
        "cancelled",
        "interrupted",
        "dead_lettered",
    }
)
_HISTORY_ITEM_STATUSES = frozenset(
    {"queued", "running", "cancelling", "completed", "blocked", "failed", "cancelled", "interrupted"}
)
_ACTIVE_HISTORY_STATUSES = frozenset({"queued", "running", "cancelling"})
_TERMINAL_HISTORY_STATUSES = frozenset(
    {"completed", "partial", "blocked", "failed", "cancelled", "interrupted", "dead_lettered"}
)


class HistoryJobManager:
    def __init__(
        self,
        service: HistoryDownloadService,
        state_path: str | Path | None = None,
        *,
        task_store: TaskStore | None = None,
        task_queue: RedisTaskQueue | None = None,
        archive: ParquetHistoryArchive | None = None,
        recover_interrupted: bool = True,
    ) -> None:
        self._service = service
        self._task_store = task_store
        self._task_queue = task_queue
        self._archive = archive
        self._recover_interrupted = bool(recover_interrupted)
        self._jobs: dict[str, dict[str, object]] = {}
        self._cancel_requested: set[str] = set()
        self._ledger_issue_ids: set[str] = set()
        self._lock = RLock()
        storage_root = getattr(getattr(service, "storage", None), "root", None)
        self._state_path = Path(state_path) if state_path is not None else (
            Path(storage_root) / ".history_jobs.json" if storage_root is not None else None
        )
        self._task_ledger_available = self._probe_task_store()
        self._load()
        self._reconcile_task_ledger()
        self._sync_history_metadata()
        self._sync_loaded_tasks()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="crypto-history")

    def submit(
        self,
        queries: tuple[HistoryQuery, ...],
        *,
        job_id: str | None = None,
        retry_of: str | None = None,
        replay_source_task_id: str | None = None,
        replay_request_id: str | None = None,
    ) -> dict[str, object]:
        if not queries:
            raise ValueError("history job must contain at least one query")
        request_fingerprint = self._fingerprint(queries)
        requested_job_id = str(job_id or uuid4().hex).strip()
        if not requested_job_id:
            raise ValueError("history job id must not be empty")
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._merge_external_jobs_locked()
            existing_by_id = self._jobs.get(requested_job_id)
            if existing_by_id is not None:
                result = self._copy(existing_by_id)
                result["deduplicated"] = True
                return result
            for existing in self._jobs.values():
                if (
                    existing.get("request_fingerprint") == request_fingerprint
                    and str(existing.get("status")) in {"queued", "running", "cancelling"}
                ):
                    result = self._copy(existing)
                    result["deduplicated"] = True
                    return result
            job_id = requested_job_id
            job = {
                "job_id": job_id,
                "status": "queued",
                "created_at": now,
                "updated_at": now,
                "request_fingerprint": request_fingerprint,
                "total": len(queries),
                "completed": 0,
                "blocked": 0,
                "failed": 0,
                "interrupted": 0,
                "cancelled": 0,
                "archive": None,
                "error": {},
                "items": [self._item(query) for query in queries],
            }
            if retry_of:
                job["retry_of"] = str(retry_of)
            self._jobs[job_id] = job
            self._cancel_requested.discard(job_id)
            self._sync_task(
                job,
                event_type="queued",
                replay_source_task_id=replay_source_task_id,
                replay_request_id=replay_request_id,
            )
            self._persist_locked()
        if self._task_queue is None:
            self._executor.submit(self._run, job_id, queries)
        else:
            try:
                task_id = f"history:{job_id}"
                payload = {"queries": [self._query_dict(query) for query in queries]}
                if self._task_store is not None:
                    publication = self._task_store.publish_pending_dispatches(
                        self._task_queue,
                        task_id=task_id,
                        limit=1,
                    )
                    published = publication.get("published") if isinstance(publication, dict) else []
                    if not isinstance(published, list) or not published:
                        raise TaskQueueError("history task dispatch remains pending")
                else:
                    self._task_queue.enqueue(task_id, "history_download", payload)
            except TaskQueueError as error:
                self._update(
                    job_id,
                    status="queued",
                    error={"kind": "QUEUE_UNAVAILABLE", "message": str(error)},
                )
                raise RuntimeError("history task queue is unavailable") from error
        return self.get(job_id)

    def run_job(self, job_id: str, queries: tuple[HistoryQuery, ...]) -> None:
        """Execute a claimed job in a dedicated Worker process."""
        self._run(str(job_id), queries)

    def restore_from_task_ledger(
        self,
        task_id: str,
        ledger: dict[str, object] | None,
    ) -> dict[str, object] | None:
        """Rehydrate a job created by another API, Scheduler, or Worker process."""
        with self._lock:
            job = self._restore_from_task_ledger_locked(task_id, ledger)
            if job is None:
                return None
            self._refresh_from_task_store_locked(job)
            return self._copy(job)

    def get(self, job_id: str) -> dict[str, object] | None:
        with self._lock:
            self._merge_external_jobs_locked()
            job = self._jobs.get(str(job_id))
            if job is not None:
                self._refresh_from_task_store_locked(job)
            return None if job is None else self._copy(job)

    def list(self, limit: int = 30) -> list[dict[str, object]]:
        with self._lock:
            self._merge_external_jobs_locked()
            for job in self._jobs.values():
                self._refresh_from_task_store_locked(job)
            jobs = sorted(self._jobs.values(), key=lambda item: str(item["created_at"]), reverse=True)
            return [self._copy(job) for job in jobs[: max(1, min(limit, 100))]]

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            self._persist_locked()
        self._service.close()

    def cancel(self, job_id: str) -> dict[str, object]:
        """Request cancellation; an in-flight HTTP page finishes first."""
        with self._lock:
            self._merge_external_jobs_locked()
            job = self._jobs.get(str(job_id))
            if job is None:
                raise KeyError(job_id)
            if str(job.get("status")) in _TERMINAL_HISTORY_STATUSES:
                return self._copy(job)
            self._cancel_requested.add(str(job_id))
            job["status"] = "cancelling"
            job["updated_at"] = datetime.now(UTC).isoformat()
            self._sync_task(job, event_type="cancel_requested")
            self._persist_locked()
            return self._copy(job)

    def retry(
        self,
        job_id: str,
        *,
        replay_task_id: str | None = None,
        replay_request_id: str | None = None,
    ) -> dict[str, object]:
        """Create a new job for every item that did not complete."""
        with self._lock:
            self._merge_external_jobs_locked()
            job = self._jobs.get(str(job_id))
            if job is None:
                raise KeyError(job_id)
            if str(job.get("status")) in _ACTIVE_HISTORY_STATUSES:
                raise ValueError("active history job cannot be retried")
            if str(job.get("status")) == "dead_lettered" and not replay_task_id:
                raise ValueError("dead-lettered history job requires audited replay")
            raw_items = [dict(item) for item in job.get("items", []) if isinstance(item, dict)]
        queries: list[HistoryQuery] = []
        for item in raw_items:
            if str(item.get("status", "")).lower() == "completed":
                continue
            try:
                queries.append(
                    HistoryQuery(
                        venue_id=str(item["venue_id"]),
                        market_type=MarketType.SPOT,
                        instrument_key=str(item["instrument_key"]),
                        native_symbol=str(item["native_symbol"]),
                        interval=CandleInterval.parse(str(item["interval"])),
                        start_at=datetime.fromisoformat(str(item["start_at"])),
                        end_at=datetime.fromisoformat(str(item["end_at"])),
                    )
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("history job contains an invalid retry item") from error
        if not queries:
            raise ValueError("history job has no incomplete items to retry")
        next_job_id = None
        if replay_task_id:
            raw_target = str(replay_task_id).strip()
            if not raw_target.startswith("history:"):
                raise ValueError("history replay task id must use the history namespace")
            next_job_id = raw_target.removeprefix("history:").strip()
            if not next_job_id:
                raise ValueError("history replay task id must not be empty")
        return self.submit(
            tuple(queries),
            job_id=next_job_id,
            retry_of=f"history:{job_id}",
            replay_source_task_id=f"history:{job_id}" if replay_request_id else None,
            replay_request_id=replay_request_id,
        )

    def replay_dead_letter(
        self,
        task_id: str,
        replay_task_id: str,
        request_id: str,
    ) -> dict[str, object]:
        """Create the history job projection used by an audited DLQ replay."""
        source_id = str(task_id).removeprefix("history:").strip()
        if not source_id:
            raise ValueError("history dead-letter task id is invalid")
        return self.retry(
            source_id,
            replay_task_id=replay_task_id,
            replay_request_id=request_id,
        )

    def _run(self, job_id: str, queries: tuple[HistoryQuery, ...]) -> None:
        if self._is_cancel_requested(job_id):
            self._cancel_items(job_id, 0)
            return
        self._update(job_id, status="running")
        completed = blocked = failed = 0
        for index, query in enumerate(queries):
            if self._is_cancel_requested(job_id):
                self._cancel_items(job_id, index)
                return
            self._update_item(job_id, index, status="running")
            try:
                result = self._service.download(query)
            except PublicRestError as error:
                blocked += 1
                self._update_item(
                    job_id,
                    index,
                    status="blocked",
                    error={
                        "kind": error.kind,
                        "status_code": error.status_code,
                        "retry_after_seconds": error.retry_after_seconds,
                        "message": str(error),
                    },
                )
            except HistoryMetadataError as error:
                failed += 1
                self._update_item(
                    job_id,
                    index,
                    status="failed",
                    error={"kind": "HISTORY_METADATA_COMMIT_FAILED", "message": str(error)},
                )
            except (HistoryDownloadError, HistoryStorageError, ValueError, OSError) as error:
                failed += 1
                self._update_item(job_id, index, status="failed", error={"kind": "DOWNLOAD_FAILED", "message": str(error)})
            except Exception:
                failed += 1
                self._update_item(
                    job_id,
                    index,
                    status="failed",
                    error={"kind": "DOWNLOAD_FAILED", "message": "unexpected history download failure"},
                )
            else:
                completed += 1
                self._update_item(
                    job_id,
                    index,
                    status="completed",
                    fetched_rows=result.fetched_rows,
                    dataset={
                        "dataset_id": result.dataset.manifest.dataset_id,
                        "row_count": result.dataset.manifest.row_count,
                        "gap_count": result.dataset.manifest.gap_count,
                        "duplicate_count": result.dataset.manifest.duplicate_count,
                        "storage_key": result.dataset.storage_key,
                        "raw_archive": result.raw_archive,
                        "history_metadata": result.history_metadata,
                    },
                )
            self._update(job_id, completed=completed, blocked=blocked, failed=failed)
            if self._is_cancel_requested(job_id):
                self._cancel_items(job_id, index + 1)
                return
        if self._is_cancel_requested(job_id):
            self._cancel_items(job_id, len(queries))
            return
        status = "completed" if completed == len(queries) else "blocked" if completed == 0 and blocked else "failed" if completed == 0 else "partial"
        archive = self._archive_completed_history(completed)
        try:
            metadata = self._sync_history_metadata()
        except Exception as error:
            self._update(
                job_id,
                status="failed",
                completed=completed,
                blocked=blocked,
                failed=failed,
                archive=archive,
                history_metadata={"status": "failed", "message": str(error)},
                error={"kind": "HISTORY_METADATA_SYNC_FAILED", "message": str(error)},
            )
            return
        self._update(
            job_id,
            status=status,
            completed=completed,
            blocked=blocked,
            failed=failed,
            archive=archive,
            history_metadata=metadata,
        )

    def _archive_completed_history(self, completed: int) -> dict[str, object] | None:
        """Refresh the columnar mirror without changing download semantics."""
        if completed <= 0 or self._archive is None:
            return None
        try:
            result = self._archive.archive_all(self._service.storage)
        except Exception as error:
            failure = {
                "status": "FAILED",
                "archived_count": 0,
                "dataset_count": 0,
                "message": str(error),
            }
            self._sync_history_archive(failure)
            return failure
        normalized = {
            "status": str(result.get("status", "UNKNOWN")),
            "available": bool(result.get("available", True)),
            "archived_count": int(result.get("archived_count", 0) or 0),
            "dataset_count": int(result.get("dataset_count", 0) or 0),
            "stale_count": int(result.get("stale_count", 0) or 0),
            "missing_count": int(result.get("missing_count", 0) or 0),
            "failed_count": int(result.get("failed_count", 0) or 0),
            "message": str(result.get("error") or ""),
        }
        if isinstance(result.get("items"), list):
            normalized["items"] = [dict(item) for item in result["items"] if isinstance(item, dict)]
        if isinstance(result.get("errors"), list):
            normalized["errors"] = [dict(item) for item in result["errors"] if isinstance(item, dict)]
        self._sync_history_archive(normalized)
        return normalized

    def _sync_history_archive(self, archive: dict[str, object]) -> None:
        if self._task_store is None:
            return
        try:
            self._task_store.sync_history_archive(archive)
        except Exception:
            # The JSON job state and the next filesystem reconciliation remain
            # available when the optional archive projection is unavailable.
            return

    def _sync_history_metadata(self) -> dict[str, object] | None:
        """Mirror the current verified manifests into the relational task database."""
        if self._task_store is None:
            return None
        datasets = self._service.storage.list_datasets()
        return self._task_store.sync_history_datasets(
            [self._service.storage.dataset_dict(dataset) for dataset in datasets]
        )

    def _is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._cancel_requested:
                return True
        if self._task_store is not None:
            try:
                ledger = self._task_store.get(f"history:{job_id}")
                return bool(ledger and (ledger.get("cancel_requested") or ledger.get("status") == "cancelling"))
            except Exception:
                return False
        return False

    def _cancel_items(self, job_id: str, start_index: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            items = job.get("items", [])
            cancelled = 0
            if isinstance(items, list):
                for item in items[start_index:]:
                    if isinstance(item, dict) and str(item.get("status", "")) in {"queued", "running", "cancelling"}:
                        item["status"] = "cancelled"
                        item["error"] = {"kind": "CANCELLED", "message": "history job was cancelled by operator"}
                        cancelled += 1
            job["status"] = "cancelled"
            job["cancelled"] = cancelled
            job["updated_at"] = datetime.now(UTC).isoformat()
            self._cancel_requested.discard(job_id)
            self._sync_task(job, event_type="cancelled")
            self._persist_locked()

    @staticmethod
    def _item(query: HistoryQuery) -> dict[str, object]:
        return {
            "venue_id": query.venue_id,
            "instrument_key": query.instrument_key,
            "native_symbol": query.native_symbol,
            "interval": query.interval.value,
            "start_at": query.start_at.isoformat(),
            "end_at": query.end_at.isoformat(),
            "status": "queued",
        }

    @staticmethod
    def _dispatch_payload(job: dict[str, object]) -> dict[str, object]:
        keys = (
            "venue_id",
            "instrument_key",
            "native_symbol",
            "interval",
            "start_at",
            "end_at",
        )
        queries = []
        raw_items = job.get("items") if isinstance(job.get("items"), list) else []
        for item in raw_items:
            if isinstance(item, dict) and all(key in item for key in keys):
                queries.append({key: str(item[key]) for key in keys})
        return {"queries": queries}

    @staticmethod
    def _query_dict(query: HistoryQuery) -> dict[str, str]:
        return {
            "venue_id": query.venue_id,
            "instrument_key": query.instrument_key,
            "native_symbol": query.native_symbol,
            "interval": query.interval.value,
            "start_at": query.start_at.isoformat(),
            "end_at": query.end_at.isoformat(),
        }

    @staticmethod
    def _fingerprint(queries: tuple[HistoryQuery, ...]) -> str:
        payload = [
            {
                "venue_id": query.venue_id,
                "instrument_key": query.instrument_key,
                "native_symbol": query.native_symbol,
                "interval": query.interval.value,
                "start_at": query.start_at.isoformat(),
                "end_at": query.end_at.isoformat(),
            }
            for query in sorted(
                queries,
                key=lambda item: (item.venue_id, item.instrument_key, item.interval.value, item.start_at.isoformat()),
            )
        ]
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def _update_item(self, job_id: str, index: int, **values: object) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            items = job["items"]
            if isinstance(items, list) and index < len(items):
                items[index].update(values)
            job["updated_at"] = datetime.now(UTC).isoformat()
            self._sync_task(job, event_type="item_updated")
            self._persist_locked()

    def _update(self, job_id: str, **values: object) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.update(values)
                job["updated_at"] = datetime.now(UTC).isoformat()
                self._sync_task(job, event_type="state_updated")
                self._persist_locked()

    def _sync_loaded_tasks(self) -> None:
        if self._task_store is None:
            return
        with self._lock:
            jobs = [self._copy(job) for job in self._jobs.values()]
        for job in jobs:
            self._sync_task(job, event_type="restored")

    def _sync_task(
        self,
        job: dict[str, object],
        *,
        event_type: str,
        replay_source_task_id: str | None = None,
        replay_request_id: str | None = None,
    ) -> None:
        """Mirror the history state into the shared task ledger without blocking downloads."""
        if self._task_store is None:
            return
        task_id = f"history:{job.get('job_id', '')}"
        if task_id == "history:":
            return
        total = int(job.get("total", 0) or 0)
        progress = {
            "completed": int(job.get("completed", 0) or 0),
            "total": total,
            "blocked": int(job.get("blocked", 0) or 0),
            "failed": int(job.get("failed", 0) or 0),
            "interrupted": int(job.get("interrupted", 0) or 0),
            "cancelled": int(job.get("cancelled", 0) or 0),
            "percent": round(int(job.get("completed", 0) or 0) / max(1, total) * 100, 1),
        }
        payload = {
            "request_fingerprint": job.get("request_fingerprint", ""),
            "items": job.get("items", []),
        }
        result = {
            "completed": progress["completed"],
            "blocked": progress["blocked"],
            "failed": progress["failed"],
            "interrupted": progress["interrupted"],
            "cancelled": progress["cancelled"],
        }
        if isinstance(job.get("archive"), dict):
            result["archive"] = dict(job["archive"])
        if isinstance(job.get("history_metadata"), dict):
            result["history_metadata"] = dict(job["history_metadata"])
        error = dict(job["error"]) if isinstance(job.get("error"), dict) else {}
        error.setdefault("blocked", progress["blocked"])
        error.setdefault("failed", progress["failed"])
        dispatch_payload = self._dispatch_payload(job)
        try:
            existing = self._task_store.get(task_id)
            if existing is None:
                self._task_store.create(
                    task_id,
                    kind="history_download",
                    title="历史数据下载",
                    payload=payload,
                    status=str(job.get("status", "queued")),
                    progress=progress,
                    retry_of=str(job.get("retry_of")) if job.get("retry_of") else None,
                    stage_dispatch=self._task_queue is not None
                    and str(job.get("status", "queued")) == "queued",
                    dispatch_payload=dispatch_payload,
                    replay_source_task_id=replay_source_task_id,
                    replay_request_id=replay_request_id,
                )
            elif (
                self._task_queue is not None
                and str(job.get("status", "queued")) == "queued"
            ):
                self._task_store.stage_dispatch_for_task(
                    task_id,
                    dispatch_payload=dispatch_payload,
                    replay_source_task_id=replay_source_task_id,
                    replay_request_id=replay_request_id,
                )
            self._task_store.sync(
                task_id,
                status=str(job.get("status", "queued")),
                progress=progress,
                payload=payload,
                result=result,
                error=error,
                cancel_requested=str(job.get("status", "")) == "cancelling",
            )
            self._task_store.append_event(
                task_id,
                event_type,
                status=str(job.get("status", "queued")),
                message="历史任务状态已同步到统一任务账本",
                payload=progress,
            )
        except Exception:
            # JSON state remains the recovery fallback until all domain services
            # have moved to the relational store.
            return

    def _refresh_from_task_store_locked(self, job: dict[str, object]) -> None:
        if self._task_store is None:
            return
        task_id = f"history:{job.get('job_id', '')}"
        try:
            ledger = self._task_store.get(task_id)
        except Exception:
            return
        if not ledger:
            return
        candidate, issue = self._job_from_task_ledger(ledger)
        if candidate is None:
            if issue:
                self._handle_corrupt_task_ledger_locked(task_id, ledger, issue)
            return
        job.clear()
        job.update(candidate)

    def _probe_task_store(self) -> bool:
        """Determine whether SQL can be used before interpreting JSON state."""
        if self._task_store is None:
            return False
        try:
            self._task_store.list(1, kind="history_download")
            return True
        except TypeError:
            # Keep small test doubles and older adapters usable during rollout.
            try:
                self._task_store.list(1)
                return True
            except Exception:
                return False
        except Exception:
            return False

    def _reconcile_task_ledger(self) -> None:
        """Adopt valid SQL jobs before any JSON recovery or mirroring occurs."""
        if not self._task_ledger_available or self._task_store is None:
            return
        try:
            ledgers = self._task_store.list(500, kind="history_download")
        except TypeError:
            try:
                ledgers = [
                    item
                    for item in self._task_store.list(500)
                    if str(item.get("kind", "")) == "history_download"
                ]
            except Exception:
                self._task_ledger_available = False
                return
        except Exception:
            self._task_ledger_available = False
            return

        with self._lock:
            ledger_ids: set[str] = set()
            for ledger in ledgers:
                task_id = str(ledger.get("task_id", "")).strip()
                if not task_id.startswith("history:"):
                    continue
                job_id = task_id.removeprefix("history:").strip()
                if not job_id:
                    continue
                ledger_ids.add(job_id)
                candidate, issue = self._job_from_task_ledger(ledger)
                if candidate is not None:
                    self._jobs[job_id] = candidate
                elif issue:
                    self._handle_corrupt_task_ledger_locked(task_id, ledger, issue)

            # A filtered list shorter than the bounded limit is a complete
            # inventory for this kind, so an active JSON-only job has no
            # durable owner and must fail closed on process restart.
            if len(ledgers) < 500 and self._recover_interrupted:
                for job_id, job in self._jobs.items():
                    if job_id not in ledger_ids and str(job.get("status", "")) in _ACTIVE_HISTORY_STATUSES:
                        self._mark_interrupted(
                            job,
                            kind="TASK_LEDGER_MISSING",
                            message="history job has no durable task ledger after process restart",
                        )
            self._persist_locked()

    def _merge_external_jobs_locked(self) -> None:
        """Make jobs created by sibling processes visible without a restart."""
        if self._state_path is not None and self._state_path.exists():
            try:
                payload = json.loads(self._state_path.read_text(encoding="utf-8"))
                raw_jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
                if isinstance(raw_jobs, list):
                    for raw_job in raw_jobs:
                        if not isinstance(raw_job, dict):
                            continue
                        job_id = str(raw_job.get("job_id", "")).strip()
                        if job_id and job_id not in self._jobs:
                            job = dict(raw_job)
                            items = job.get("items", [])
                            job["items"] = [dict(item) for item in items if isinstance(item, dict)]
                            self._jobs[job_id] = job
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                # The relational ledger below remains available during an
                # atomic JSON replacement or a permission transition.
                pass
        if self._task_store is None:
            return
        try:
            ledgers = self._task_store.list(500, kind="history_download")
        except TypeError:
            try:
                ledgers = [
                    item
                    for item in self._task_store.list(500)
                    if str(item.get("kind", "")) == "history_download"
                ]
            except Exception:
                return
        except Exception:
            return
        for ledger in ledgers:
            self._restore_from_task_ledger_locked(str(ledger.get("task_id", "")), ledger)

    def _restore_from_task_ledger_locked(
        self,
        task_id: str,
        ledger: dict[str, object] | None,
    ) -> dict[str, object] | None:
        raw_task_id = str(task_id or "")
        job_id = raw_task_id.removeprefix("history:")
        if not job_id or not isinstance(ledger, dict) or str(ledger.get("kind", "")) != "history_download":
            return None
        candidate, issue = self._job_from_task_ledger(ledger)
        if candidate is None:
            if issue:
                self._handle_corrupt_task_ledger_locked(raw_task_id, ledger, issue)
            return self._jobs.get(job_id)
        current = self._jobs.get(job_id)
        if current != candidate:
            self._jobs[job_id] = candidate
            self._persist_locked()
        return self._jobs[job_id]

    @classmethod
    def _job_from_task_ledger(
        cls,
        ledger: dict[str, object],
    ) -> tuple[dict[str, object] | None, str | None]:
        """Validate and convert the SQL projection without trusting raw JSON."""
        task_id = str(ledger.get("task_id", "")).strip()
        if not task_id.startswith("history:") or not task_id.removeprefix("history:").strip():
            return None, "task_id is missing the history namespace"
        status = str(ledger.get("status", "")).strip().lower()
        if status not in _HISTORY_JOB_STATUSES:
            return None, "status is not a supported history job status"
        payload = ledger.get("payload")
        if not isinstance(payload, dict):
            return None, "payload is not an object"
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            return None, "payload.items is missing or not an array"
        result = ledger.get("result")
        if not isinstance(result, dict):
            return None, "result is not an object"
        error = ledger.get("error")
        if not isinstance(error, dict):
            return None, "error is not an object"
        empty_corrupt_failure = (
            not raw_items
            and status == "failed"
            and str(error.get("kind", "")) == "TASK_LEDGER_CORRUPT"
        )
        if not raw_items and not empty_corrupt_failure:
            return None, "payload.items is empty"
        items = [dict(item) for item in raw_items if isinstance(item, dict)]
        if len(items) != len(raw_items):
            return None, "payload.items contains a non-object item"
        for item in items:
            if str(item.get("status", "")).strip().lower() not in _HISTORY_ITEM_STATUSES:
                return None, "payload.items contains an unsupported item status"
        progress = ledger.get("progress")
        if not isinstance(progress, dict):
            return None, "progress is not an object"
        try:
            total = int(progress.get("total", len(items)) or len(items))
            counts = {
                key: int(progress.get(key, 0) or 0)
                for key in ("completed", "blocked", "failed", "interrupted", "cancelled")
            }
        except (TypeError, ValueError):
            return None, "progress contains a non-integer count"
        if (
            (total <= 0 and not empty_corrupt_failure)
            or total != len(items)
            or any(value < 0 for value in counts.values())
        ):
            return None, "progress counts do not match the item list"
        if sum(counts.values()) > total:
            return None, "progress counts exceed the item list"
        job_id = task_id.removeprefix("history:").strip()
        now = datetime.now(UTC).isoformat()
        archive = result.get("archive")
        history_metadata = result.get("history_metadata")
        return {
            "job_id": job_id,
            "status": status,
            "created_at": str(ledger.get("created_at") or now),
            "updated_at": str(ledger.get("updated_at") or now),
            "request_fingerprint": str(payload.get("request_fingerprint", "")),
            "total": total,
            **counts,
            "archive": dict(archive) if isinstance(archive, dict) else None,
            "history_metadata": dict(history_metadata) if isinstance(history_metadata, dict) else None,
            "error": dict(error),
            "items": items,
        }, None

    def _handle_corrupt_task_ledger_locked(
        self,
        task_id: str,
        ledger: dict[str, object],
        issue: str,
    ) -> None:
        job_id = str(task_id).removeprefix("history:").strip()
        if not job_id:
            return
        job = self._jobs.get(job_id)
        if job is not None:
            if str(job.get("status", "")) in _ACTIVE_HISTORY_STATUSES and self._recover_interrupted:
                self._mark_interrupted(
                    job,
                    kind="TASK_LEDGER_CORRUPT",
                    message=f"history task ledger is corrupt: {issue}",
                )
            else:
                job["recovery"] = {
                    "kind": "TASK_LEDGER_CORRUPT",
                    "message": issue,
                }
        else:
            now = datetime.now(UTC).isoformat()
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "failed",
                "created_at": str(ledger.get("created_at") or now),
                "updated_at": now,
                "request_fingerprint": "",
                # There is no trustworthy item list to count. Keep this
                # terminal guard record structurally valid for later restarts.
                "total": 0,
                "completed": 0,
                "blocked": 0,
                "failed": 0,
                "interrupted": 0,
                "cancelled": 0,
                "archive": None,
                "error": {
                    "kind": "TASK_LEDGER_CORRUPT",
                    "message": f"history task ledger is corrupt: {issue}",
                },
                "items": [],
            }
        if task_id not in self._ledger_issue_ids:
            self._ledger_issue_ids.add(task_id)
            try:
                self._task_store.append_event(
                    task_id,
                    "task_ledger_corrupt",
                    status="failed",
                    message="历史任务账本损坏，已使用恢复副本或失败状态保护",
                    payload={"kind": "TASK_LEDGER_CORRUPT", "reason": issue},
                )
            except Exception:
                pass

    @staticmethod
    def _mark_interrupted(job: dict[str, object], *, kind: str, message: str) -> None:
        job["status"] = "interrupted"
        job["updated_at"] = datetime.now(UTC).isoformat()
        interrupted = 0
        items = job.get("items", [])
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and str(item.get("status", "")) in _ACTIVE_HISTORY_STATUSES:
                    item["status"] = "interrupted"
                    item["error"] = {"kind": kind, "message": message}
                    interrupted += 1
        job["interrupted"] = interrupted
        job["error"] = {"kind": kind, "message": message}

    def _load(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            raw_jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
            if not isinstance(raw_jobs, list):
                raise ValueError("jobs must be an array")
            for raw_job in raw_jobs:
                if not isinstance(raw_job, dict) or not str(raw_job.get("job_id", "")).strip():
                    continue
                job = dict(raw_job)
                items = job.get("items", [])
                job["items"] = [dict(item) for item in items if isinstance(item, dict)]
                if (
                    self._recover_interrupted
                    and not self._task_ledger_available
                    and str(job.get("status", "")) in _ACTIVE_HISTORY_STATUSES
                ):
                    self._mark_interrupted(
                        job,
                        kind="PROCESS_RESTARTED",
                        message="history job was interrupted by process restart",
                    )
                self._jobs[str(job["job_id"])] = job
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            if self._task_ledger_available:
                # SQL is the primary store. A damaged recovery copy must not
                # prevent a valid task ledger from rebuilding it at startup.
                self._jobs.clear()
                return
            raise RuntimeError("history job state is unreadable") from error

    def _persist_locked(self) -> None:
        if self._state_path is None:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "jobs": list(self._jobs.values())}
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self._state_path.parent,
                prefix=f".{self._state_path.name}.",
                suffix=".tmp",
                mode="w",
                encoding="utf-8",
                delete=False,
            ) as handle:
                temp_name = handle.name
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self._state_path)
        finally:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _copy(job: dict[str, object]) -> dict[str, object]:
        result = dict(job)
        result["items"] = [dict(item) for item in job.get("items", []) if isinstance(item, dict)]
        return result
