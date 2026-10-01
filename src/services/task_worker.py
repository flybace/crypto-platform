"""Redis-backed worker for history, research, and paper-replay tasks."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal
import logging
import os
from pathlib import Path
import socket
import threading
from typing import Any

from application.candle_backtest import CandleBacktestConfig
from application.history_archive import ParquetHistoryArchive
from application.pool_catalog import PoolCatalog
from application.screening import ScreeningService
from backend.app.services.backtest_runs import BacktestDataError, BacktestRunManager
from backend.app.services.domain_state import build_domain_state
from backend.app.services.history_jobs import HistoryJobManager
from backend.app.services.history_metadata import HistoryMetadataRepository
from backend.app.services.history_service import build_history_service
from backend.app.services.paper_automation import PaperAutomationService
from backend.app.services.paper_trading import PaperTradingService
from backend.app.services.public_network_settings import (
    PublicNetworkSettingsStore,
    endpoint_defaults_from_settings,
    proxy_defaults_from_settings,
    resolve_public_network_settings_path,
    settings_with_public_network,
)
from backend.app.services.research_runs import ResearchRunService
from backend.app.services.strategy_matrix import StrategyMatrixService
from backend.app.services.strategy_registry import StrategyRegistry
from backend.app.services.task_dispatcher import (
    TASK_KINDS,
    TaskExecutionCancelled,
    TaskExecutionContext,
    TaskExecutionInterrupted,
    TaskLeaseLost,
)
from backend.app.services.task_dispatch_consistency import TaskDispatchConsistency
from backend.app.services.task_queue import RedisTaskQueue, TaskQueueError
from backend.app.services.task_quota import TaskQuota, TaskQuotaExceeded
from backend.app.services.task_result_archive import TaskResultArchive
from backend.app.services.task_log_archive import TaskLogArchive
from backend.app.services.task_store import TaskStore, TaskStoreError
from backend.app.settings import Settings
from domain.candle import CandleInterval, HistoryQuery
from domain.market import MarketType
from services.process_signals import install_shutdown_handlers


LOGGER = logging.getLogger("crypto.task_worker")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEASE_SECONDS = 900
TERMINAL_TASK_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "blocked", "interrupted", "partial", "dead_lettered"}
)


class TaskWorker:
    """Execute one queue envelope at a time with shared file and SQL state."""

    def __init__(self, settings: Settings, *, worker_id: str | None = None) -> None:
        self.network_settings = PublicNetworkSettingsStore(
            resolve_public_network_settings_path(settings, PROJECT_ROOT),
            defaults=proxy_defaults_from_settings(settings),
            endpoint_defaults=endpoint_defaults_from_settings(settings),
        )
        self.network_snapshot = self.network_settings.current()
        settings = settings_with_public_network(settings, self.network_snapshot)
        self.settings = settings
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self._shutdown_requested = threading.Event()
        self._active_task_id: str | None = None
        self.quota = TaskQuota.from_settings(settings)
        self.lease_seconds = int(settings.task_lease_seconds)
        self.recovery_grace_seconds = int(settings.task_recovery_grace_seconds)
        self.queue = RedisTaskQueue(settings.redis_url)
        history_root = self._history_root()
        runtime_root = history_root / ".runtime"
        self.store = TaskStore(
            settings.database_url,
            required=True,
            event_archive=TaskLogArchive(runtime_root / "task-logs"),
        )
        self.store.reconcile_event_archives()
        self.history_metadata = HistoryMetadataRepository(self.store)
        self.result_archive = TaskResultArchive(runtime_root / "task-results")
        self.dispatch_consistency = TaskDispatchConsistency(self.store, self.queue)
        domain_state = lambda name: build_domain_state(self.store, runtime_root, name)
        self.history_service = build_history_service(
            settings,
            data_root=history_root,
            metadata_writer=self.history_metadata,
            api_routes=self.network_snapshot.api_routes,
        )
        self.history_archive = ParquetHistoryArchive(history_root)
        self.history_jobs = HistoryJobManager(
            self.history_service,
            state_path=history_root / ".history_jobs.json",
            task_store=self.store,
            archive=self.history_archive,
            recover_interrupted=False,
        )
        self.strategy_registry = StrategyRegistry(state_store=domain_state("strategies.json"))
        self.pool_catalog = PoolCatalog(
            self.history_service.storage,
            state_store=domain_state("pools.json"),
        )
        self.screening_service = ScreeningService(
            self.history_service.storage,
            self.pool_catalog,
            state_store=domain_state("screening-runs.json"),
            task_store=self.store,
        )
        self.backtest_runs = BacktestRunManager(
            self.history_service.storage,
            state_store=domain_state("backtest-runs.json"),
            task_store=self.store,
        )
        self.research_runs = ResearchRunService(
            self.history_service.storage,
            self.pool_catalog,
            self.screening_service,
            state_store=domain_state("research-runs.json"),
            task_store=self.store,
        )
        self.paper_trading = PaperTradingService(
            self.history_service.storage,
            state_store=domain_state("paper-trading.json"),
            task_store=self.store,
        )
        self.paper_automation = PaperAutomationService(
            self.paper_trading,
            self.strategy_registry,
            state_store=domain_state("paper-automation.json"),
        )
        self.strategy_matrices = StrategyMatrixService(
            self.history_service.storage,
            self.strategy_registry,
            state_store=domain_state("strategy-matrices.json"),
            task_store=self.store,
        )

    def run_once(self, *, timeout_seconds: int = 5) -> bool:
        if self.shutdown_requested:
            return False
        self._heartbeat()
        self._reconcile_task_log_archives()
        lease_seconds = int(getattr(self, "lease_seconds", LEASE_SECONDS))
        try:
            self._recover_stale_worker_claims()
            if hasattr(self.queue, "reclaim_expired"):
                reclaimed_items = self.queue.reclaim_expired(lease_seconds=lease_seconds)
                for item in reclaimed_items:
                    self._recover_reclaimed(item)
                if reclaimed_items:
                    LOGGER.warning("reclaimed %s expired task lease(s)", len(reclaimed_items))
            else:
                reclaimed = self.queue.requeue_expired(lease_seconds=lease_seconds)
                if reclaimed:
                    LOGGER.warning("reclaimed %s expired task lease(s)", reclaimed)
        except TaskQueueError:
            LOGGER.exception("unable to reclaim expired task leases")
        self._recover_expired_sql_leases()
        if self.shutdown_requested:
            return False
        self._relay_pending_dispatches()
        if self.shutdown_requested:
            return False
        envelope = self.queue.claim(
            timeout_seconds,
            worker_id=self.worker_id,
            lease_seconds=lease_seconds,
        )
        if envelope is None:
            return False
        if self.shutdown_requested:
            try:
                requeue = getattr(self.queue, "requeue", None)
                if callable(requeue):
                    requeue(
                        envelope,
                        attempt=self._attempt(envelope.get("attempt"), default=1),
                        error={"kind": "WORKER_DRAINING", "message": "worker shutdown started before execution"},
                    )
                else:
                    raise TaskQueueError("task queue cannot return a claimed task during shutdown")
            except Exception:
                LOGGER.exception("unable to return claimed task during graceful shutdown")
            return False
        task_id = str(envelope.get("task_id", ""))
        kind = str(envelope.get("kind", "")).strip().lower()
        attempt = self._attempt(envelope.get("attempt"), default=1)
        max_attempts = max(attempt, min(self._attempt(envelope.get("max_attempts"), default=3), 10))
        lease_seconds = int(getattr(self, "lease_seconds", LEASE_SECONDS))
        try:
            claimed = self.store.mark_claimed(
                task_id,
                worker_id=self.worker_id,
                attempt=attempt,
                lease_seconds=lease_seconds,
                message_id=str(envelope.get("message_id", "")),
            )
        except Exception:
            # Keep the Redis claim visible for recovery.  A database outage
            # must not acknowledge a message whose SQL lease was never set.
            LOGGER.exception("unable to acquire SQL task lease: %s", task_id)
            return False
        if claimed is None or claimed.get("claim_acquired", True) is False:
            if claimed is not None and claimed.get("claim_rejected"):
                LOGGER.warning(
                    "rejecting task delivery before execution task=%s reason=%s",
                    task_id,
                    claimed.get("claim_rejected"),
                )
            elif claimed is not None and str(claimed.get("status", "")) in TERMINAL_TASK_STATUSES:
                LOGGER.warning("acknowledging duplicate terminal task message: %s", task_id)
            elif claimed is not None:
                LOGGER.warning("acknowledging task message already leased by another worker: %s", task_id)
            else:
                LOGGER.warning("acknowledging task message with no SQL ledger: %s", task_id)
            if claimed is not None and str(claimed.get("status", "")) == "dead_lettered":
                return self._settle_terminal_claim(envelope, claimed)
            self.queue.ack(envelope)
            return True
        if str(claimed.get("status", "")) in TERMINAL_TASK_STATUSES:
            LOGGER.warning("acknowledging duplicate terminal task message: %s", task_id)
            if str(claimed.get("status", "")) == "dead_lettered":
                return self._settle_terminal_claim(envelope, claimed)
            self.queue.ack(envelope)
            return True
        quota = getattr(self, "quota", TaskQuota())
        context = TaskExecutionContext(
            self.store,
            task_id,
            quota=quota,
            result_archive=getattr(self, "result_archive", None),
            worker_id=self.worker_id,
            shutdown_event=self._shutdown_event(),
        )
        acknowledge = True
        self._active_task_id = task_id
        try:
            self._heartbeat(active_task_id=task_id)
        except Exception:
            LOGGER.exception("unable to publish active worker state: %s", task_id)
        lease_stop, lease_thread = self._start_lease_heartbeat(
            envelope,
            task_id,
            lease_seconds=lease_seconds,
        )
        try:
            if kind == "history_download":
                raw_queries = envelope.get("payload", {}).get("queries") if isinstance(envelope.get("payload"), dict) else []
                context.start_budget(total=len(raw_queries) if isinstance(raw_queries, list) else 1)
                context.raise_if_cancelled()
                self._run_history(task_id, envelope.get("payload"))
                context.check_budget()
            elif kind in TASK_KINDS:
                context.raise_if_cancelled()
                context.started(total=self._task_total(kind, envelope.get("payload")))
                self.queue.renew(envelope, lease_seconds=lease_seconds)
                result = self._run_kind(kind, envelope.get("payload"), context)
                if context.cancelled():
                    context.cancelled_result(result)
                else:
                    context.completed(result, progress=self._result_progress(result))
            else:
                raise ValueError(f"unsupported task kind: {kind}")
        except TaskExecutionInterrupted as error:
            try:
                if context.cancelled():
                    context.cancelled_result()
                else:
                    recovered = self.store.recover_after_worker_loss(
                        task_id,
                        worker_id=self.worker_id,
                        attempt=attempt,
                        message_id=str(envelope.get("message_id", "")),
                        failure_kind="WORKER_SHUTDOWN",
                        failure_message=str(error),
                        event_type="requeued_after_shutdown",
                        event_message="Worker 正在停机，任务已通过 outbox 恢复到队列",
                    )
                    if recovered is None:
                        acknowledge = False
                        LOGGER.warning("unable to recover task during worker shutdown: %s", task_id)
            except TaskLeaseLost:
                acknowledge = False
                LOGGER.warning("task lease lost while recovering shutdown: %s", task_id)
            except (TaskStoreError, OSError):
                acknowledge = False
                LOGGER.exception("unable to persist task shutdown recovery: %s", task_id)
        except TaskExecutionCancelled:
            try:
                context.cancelled_result()
            except TaskLeaseLost:
                acknowledge = False
                LOGGER.warning("task lease lost while cancelling: %s", task_id)
            except TaskStoreError:
                LOGGER.exception("unable to persist task cancellation: %s", task_id)
        except TaskLeaseLost:
            # A newer worker may have fenced this process after a lease expiry.
            # Leave the Redis claim for its normal recovery path; acknowledging
            # it here could remove a replacement worker's claim.
            acknowledge = False
            LOGGER.warning("task lease lost; leaving claim for recovery: %s", task_id)
        except TaskQuotaExceeded as error:
            try:
                context.quota_blocked(error)
            except TaskLeaseLost:
                acknowledge = False
                LOGGER.warning("task lease lost while blocking task: %s", task_id)
            except TaskStoreError:
                LOGGER.exception("unable to persist task quota block: %s", task_id)
        except Exception as error:
            LOGGER.exception("task failed: %s", task_id)
            failure = self._failure_payload(error)
            if self._retryable(error) and attempt < max_attempts:
                try:
                    context.retry_scheduled(
                        failure,
                        attempt=attempt + 1,
                        max_attempts=max_attempts,
                    )
                except Exception:
                    acknowledge = False
                    LOGGER.exception("unable to requeue failed task: %s", task_id)
            else:
                sql_terminal = False
                dead_letter_metadata = None
                try:
                    metadata_builder = getattr(self.queue, "dead_letter_metadata", None)
                    if callable(metadata_builder):
                        dead_letter_metadata = metadata_builder(envelope, error=failure)
                    context.dead_lettered(
                        failure,
                        attempt=attempt,
                        dead_letter=dead_letter_metadata,
                    )
                    sql_terminal = True
                except Exception:
                    try:
                        current = self.store.get(task_id)
                        sql_terminal = bool(
                            current is not None
                            and str(current.get("status", "")) == "dead_lettered"
                        )
                    except Exception:
                        sql_terminal = False
                    acknowledge = False
                    LOGGER.exception("unable to persist dead-letter task: %s", task_id)
                if sql_terminal:
                    try:
                        dead_letter = getattr(self.queue, "dead_letter", None)
                        if not callable(dead_letter):
                            raise TaskQueueError("task queue cannot persist task dead letter")
                        dead_letter(envelope, error=failure)
                    except Exception:
                        acknowledge = False
                        LOGGER.exception("unable to settle dead-letter task in Redis: %s", task_id)
                        self._return_claim_for_retry(
                            envelope,
                            attempt=attempt,
                            error={
                                "kind": "DLQ_DELIVERY_PENDING",
                                "message": "SQL terminal state committed; Redis DLQ delivery will be retried",
                            },
                        )
        finally:
            lease_stop.set()
            lease_thread.join(timeout=2)
            try:
                if acknowledge:
                    self.queue.ack(envelope)
            except Exception:
                # Leave an unacknowledged claim for the normal Redis/SQL
                # recovery path instead of terminating the worker here.
                LOGGER.exception("unable to acknowledge task: %s", task_id)
            finally:
                self._active_task_id = None
                try:
                    self._heartbeat()
                except Exception:
                    LOGGER.exception("unable to publish worker lifecycle state")
        return True

    def _settle_terminal_claim(self, envelope: dict[str, object], record: dict[str, object]) -> bool:
        """Finish Redis settlement for a SQL terminal dead-letter record."""
        dead_letter = getattr(self.queue, "dead_letter", None)
        if not callable(dead_letter):
            LOGGER.error("task queue cannot settle terminal dead-letter claim")
            return False
        error = record.get("error")
        try:
            dead_letter(envelope, error=error if isinstance(error, dict) else {})
            self.queue.ack(envelope)
            return True
        except Exception:
            LOGGER.exception("unable to settle terminal dead-letter claim: %s", envelope.get("task_id"))
            self._return_claim_for_retry(
                envelope,
                attempt=self._attempt(envelope.get("attempt"), default=1),
                error={
                    "kind": "DLQ_DELIVERY_PENDING",
                    "message": "SQL terminal state committed; Redis DLQ delivery will be retried",
                },
            )
            return False

    def _return_claim_for_retry(
        self,
        envelope: dict[str, object],
        *,
        attempt: int,
        error: dict[str, object],
    ) -> None:
        requeue = getattr(self.queue, "requeue", None)
        if not callable(requeue):
            return
        try:
            requeue(envelope, attempt=attempt, error=error)
        except Exception:
            LOGGER.exception("unable to expose pending Redis DLQ delivery: %s", envelope.get("task_id"))

    def run_forever(self, *, poll_seconds: int = 5) -> None:
        poll_seconds = max(1, min(int(poll_seconds), 5))
        while not self.shutdown_requested:
            try:
                claimed = self.run_once(timeout_seconds=poll_seconds)
            except TaskQueueError:
                LOGGER.exception("task queue is unavailable; retrying after cooldown")
                claimed = False
                if self._shutdown_event().wait(max(1, poll_seconds)):
                    break
            if not claimed and self._shutdown_event().wait(poll_seconds):
                break
        LOGGER.info("worker %s stopped after graceful shutdown", self.worker_id)

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_event().is_set()

    def request_shutdown(self) -> None:
        event = self._shutdown_event()
        if not event.is_set():
            LOGGER.info("worker %s is draining and will not claim new tasks", self.worker_id)
        event.set()

    def _shutdown_event(self) -> threading.Event:
        event = getattr(self, "_shutdown_requested", None)
        if event is None:
            event = threading.Event()
            self._shutdown_requested = event
        return event

    def close(self) -> None:
        self.request_shutdown()
        try:
            history_jobs = getattr(self, "history_jobs", None)
            if history_jobs is not None:
                history_jobs.close()
        finally:
            try:
                self._heartbeat(state="stopped")
            except Exception:
                LOGGER.exception("unable to publish worker stopped state")
            store = getattr(self, "store", None)
            if store is not None:
                store.close()

    def _heartbeat(
        self,
        *,
        state: str | None = None,
        active_task_id: str | None = None,
    ) -> None:
        """Publish lifecycle metadata while keeping old queue adapters usable."""
        heartbeat = getattr(getattr(self, "queue", None), "heartbeat", None)
        if not callable(heartbeat):
            return
        lifecycle_state = state or ("draining" if self.shutdown_requested else "running")
        active = active_task_id if active_task_id is not None else getattr(self, "_active_task_id", None)
        try:
            heartbeat(
                self.worker_id,
                state=lifecycle_state,
                active_task_id=active,
            )
        except TypeError:
            # Test doubles and older adapters accepted only worker_id.
            heartbeat(self.worker_id)

    def _reconcile_task_log_archives(self) -> None:
        """Retry a bounded archive backlog before accepting more work."""
        reconcile = getattr(getattr(self, "store", None), "reconcile_event_archives", None)
        if not callable(reconcile):
            return
        try:
            outcome = reconcile(limit=100)
            if isinstance(outcome, dict) and int(outcome.get("failed", 0) or 0) > 0:
                LOGGER.warning("task log archive still has failed objects: %s", outcome["failed"])
        except Exception:
            LOGGER.exception("unable to reconcile task log archives")

    def _recover_reclaimed(self, item: Any) -> None:
        if not isinstance(item, dict):
            return
        task_id = str(item.get("task_id", "")).strip()
        if not task_id:
            return
        try:
            self.store.recover_after_lease(
                task_id,
                attempt=self._attempt(item.get("attempt"), default=1),
                message_id=str(item.get("message_id", "")),
            )
        except TaskStoreError:
            LOGGER.exception("unable to persist lease recovery: %s", task_id)

    def _recover_stale_worker_claims(self) -> None:
        """Recover claims whose worker disappeared before its lease expired."""
        find = getattr(self.queue, "stale_claims", None)
        release = getattr(self.queue, "release_claim", None)
        store = getattr(self, "store", None)
        recover = getattr(store, "recover_after_worker_loss", None)
        if not callable(find) or not callable(release) or not callable(recover):
            return
        settings = getattr(self, "settings", None)
        grace_seconds = int(
            getattr(
                self,
                "recovery_grace_seconds",
                getattr(settings, "task_recovery_grace_seconds", 60),
            )
        )
        try:
            claims = find(
                grace_seconds=grace_seconds,
                exclude_worker_id=self.worker_id,
                limit=100,
            )
        except Exception:
            LOGGER.exception("unable to inspect stale worker claims")
            return
        for claim in claims or []:
            if not isinstance(claim, dict):
                continue
            task_id = str(claim.get("task_id", "")).strip()
            if not task_id:
                continue
            try:
                record = recover(
                    task_id,
                    worker_id=str(claim.get("worker_id", "")),
                    attempt=self._attempt(claim.get("attempt"), default=1),
                    message_id=str(claim.get("message_id", "")),
                )
                # SQL is authoritative. Once it has either been requeued or
                # already advanced under another worker, the old Redis claim
                # is only a duplicate recovery marker and can be removed.
                release(claim)
                if isinstance(record, dict) and record.get("recovered_after_worker_loss"):
                    LOGGER.warning("recovered task after worker heartbeat loss: %s", task_id)
            except Exception:
                LOGGER.exception("unable to recover stale worker claim: %s", task_id)

    def _recover_expired_sql_leases(self) -> None:
        """Restore SQL-only leases when Redis lost the processing item."""
        store = getattr(self, "store", None)
        recover = getattr(store, "recover_expired_leases", None)
        if not callable(recover):
            return
        try:
            recovered = recover(limit=100)
        except Exception:
            LOGGER.exception("unable to recover expired SQL task leases")
            return
        if recovered:
            LOGGER.warning("recovered %s expired SQL task lease(s)", len(recovered))

    def _relay_pending_dispatches(self) -> None:
        """Audit and repair only safe SQL/Redis delivery states."""
        store = getattr(self, "store", None)
        stage = getattr(store, "stage_missing_dispatches", None)
        publish = getattr(store, "publish_pending_dispatches", None)
        if not callable(stage) or not callable(publish):
            return
        if not callable(getattr(self.queue, "enqueue", None)):
            return
        try:
            auditor = getattr(self, "dispatch_consistency", None)
            if not isinstance(auditor, TaskDispatchConsistency):
                auditor = TaskDispatchConsistency(store, self.queue)
            if not callable(getattr(self.queue, "inspect_delivery", None)):
                # Keep older injected queue doubles usable while the real
                # Redis adapter uses the classified recovery path below.
                stage(limit=100)
                publish(self.queue, limit=50)
                return
            outcome = auditor.audit_and_repair(limit=50)
            repair = outcome.get("repair") if isinstance(outcome, dict) else {}
            failed = repair.get("failed", 0) if isinstance(repair, dict) else 0
            if int(failed or 0) > 0:
                LOGGER.warning("task delivery consistency repair failed for %s item(s)", failed)
        except Exception:
            LOGGER.exception("unable to relay pending task dispatches")

    def _start_lease_heartbeat(
        self,
        envelope: dict[str, object],
        task_id: str,
        *,
        lease_seconds: int,
    ) -> tuple[threading.Event, threading.Thread]:
        stop = threading.Event()
        # Keep the worker heartbeat alive during long tasks; the main loop is
        # blocked in domain work until this task reaches a safe checkpoint.
        interval = max(1, min(15, max(1, int(lease_seconds) // 3)))

        def renew_loop() -> None:
            while not stop.wait(interval):
                try:
                    self._heartbeat(active_task_id=task_id)
                except Exception:
                    LOGGER.exception("unable to renew worker heartbeat: %s", self.worker_id)
                try:
                    self.queue.renew(envelope, lease_seconds=lease_seconds)
                except Exception:
                    LOGGER.exception("unable to renew Redis task lease: %s", task_id)
                try:
                    renewed = self.store.renew_claim(
                        task_id,
                        worker_id=self.worker_id,
                        lease_seconds=lease_seconds,
                    )
                    if not renewed:
                        LOGGER.warning("SQL task lease is no longer owned by worker: %s", task_id)
                except Exception:
                    LOGGER.exception("unable to renew SQL task lease: %s", task_id)

        thread = threading.Thread(
            target=renew_loop,
            name=f"crypto-lease-{str(task_id).replace(':', '-')[:80]}",
            daemon=True,
        )
        thread.start()
        return stop, thread

    @staticmethod
    def _attempt(value: Any, *, default: int) -> int:
        try:
            return max(1, int(value or default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _retryable(error: Exception) -> bool:
        return not isinstance(error, (ValueError, BacktestDataError, TaskQuotaExceeded, TaskExecutionCancelled))

    @staticmethod
    def _failure_payload(error: Exception) -> dict[str, str]:
        return {
            "kind": str(error.__class__.__name__).upper()[:80],
            "message": str(error)[:500],
        }

    def _run_kind(
        self,
        kind: str,
        raw_payload: Any,
        context: TaskExecutionContext,
    ) -> dict[str, object]:
        payload = self._payload(raw_payload)
        if kind == "backtest":
            return self.backtest_runs.run(
                venue_id=self._text(payload, "venue_id"),
                symbol=self._text(payload, "symbol"),
                interval=self._text(payload, "interval"),
                start_at=self._datetime(payload.get("start_at")),
                end_at=self._datetime(payload.get("end_at")),
                config=self._config(payload, self._text(payload, "strategy_id")),
                run_id=self._text(payload, "run_id"),
                record_task=False,
            )
        if kind == "pool_backtest":
            return self._run_pool_backtest(payload, context)
        if kind == "screening":
            return self.screening_service.run(
                pool_id=self._text(payload, "pool_id"),
                interval=payload.get("interval"),
                lookback=int(payload.get("lookback", 200)),
                min_return_pct=self._decimal(payload, "min_return_pct"),
                max_volatility_pct=self._optional_decimal(payload, "max_volatility_pct"),
                min_quote_volume=self._decimal(payload, "min_quote_volume"),
                min_momentum_pct=self._decimal(payload, "min_momentum_pct"),
                limit=int(payload.get("limit", 50)),
                run_id=self._text(payload, "run_id"),
                record_task=False,
            )
        if kind == "research":
            return self._run_research(payload)
        if kind == "paper_strategy":
            return self.paper_trading.run_strategy(
                venue_id=self._text(payload, "venue_id"),
                symbol=self._text(payload, "symbol"),
                interval=self._text(payload, "interval"),
                config=self._config(payload, self._text(payload, "strategy_id")),
                run_id=self._text(payload, "run_id"),
                record_task=False,
            )
        if kind == "paper_automation":
            config = payload.get("config")
            if not isinstance(config, dict):
                raise ValueError("paper automation task requires a config object")
            return self.paper_automation.run_now(
                run_id=self._text(payload, "run_id"),
                config=config,
                record_task=False,
            )
        if kind == "strategy_matrix":
            return self.strategy_matrices.run(
                self._text(payload, "matrix_id"),
                run_id=self._text(payload, "run_id"),
                record_task=False,
            )
        raise ValueError(f"unsupported task kind: {kind}")

    def _run_research(self, payload: dict[str, Any]) -> dict[str, object]:
        mode = self._text(payload, "mode")
        config = self._config(payload, payload.get("strategy_id"))
        run_id = self._text(payload, "run_id")
        if mode == "portfolio":
            return self.research_runs.portfolio(
                pool_id=self._text(payload, "pool_id"),
                interval=self._text(payload, "interval"),
                strategy_id=self._text(payload, "strategy_id"),
                config=config,
                screening_run_id=payload.get("screening_run_id") or None,
                max_datasets=int(payload.get("max_datasets", 20)),
                run_id=run_id,
                record_task=False,
            )
        if mode == "compare":
            strategy_ids = payload.get("strategy_ids")
            if not isinstance(strategy_ids, list):
                raise ValueError("strategy_ids must be a list")
            parameters = payload.get("strategy_parameters")
            if not isinstance(parameters, dict):
                parameters = {}
            return self.research_runs.compare(
                venue_id=self._text(payload, "venue_id"),
                symbol=self._text(payload, "symbol"),
                interval=self._text(payload, "interval"),
                strategy_ids=[str(item) for item in strategy_ids],
                config=config,
                parameters=parameters,
                run_id=run_id,
                record_task=False,
            )
        if mode == "tune":
            grid = payload.get("parameter_grid")
            if not isinstance(grid, dict):
                raise ValueError("parameter_grid must be an object")
            return self.research_runs.tune(
                venue_id=self._text(payload, "venue_id"),
                symbol=self._text(payload, "symbol"),
                interval=self._text(payload, "interval"),
                strategy_id=self._text(payload, "strategy_id"),
                config=config,
                parameter_grid=grid,
                max_runs=int(payload.get("max_runs", 30)),
                run_id=run_id,
                record_task=False,
            )
        if mode == "screen":
            return self.research_runs.strategy_screen(
                pool_id=self._text(payload, "pool_id"),
                interval=self._text(payload, "interval"),
                strategy_id=self._text(payload, "strategy_id"),
                config=config,
                lookback=int(payload.get("lookback", 200)),
                limit=int(payload.get("limit", 50)),
                run_id=run_id,
                record_task=False,
            )
        raise ValueError(f"unsupported research mode: {mode}")

    def _run_pool_backtest(
        self,
        payload: dict[str, Any],
        context: TaskExecutionContext,
    ) -> dict[str, object]:
        pool_id = self._text(payload, "pool_id")
        interval = self._text(payload, "interval")
        pool = self.pool_catalog.get(pool_id)
        if pool is None:
            raise BacktestDataError("pool was not found")
        candidate_ids: set[str] | None = None
        screening_run_id = payload.get("screening_run_id") or None
        if screening_run_id:
            screening = self.screening_service.get(str(screening_run_id))
            if screening is None:
                raise BacktestDataError("screening run was not found")
            candidate_ids = {
                str(item["dataset_id"])
                for item in screening.get("items", [])
                if isinstance(item, dict) and item.get("passed") is True
            }
        datasets = [
            dataset
            for dataset in self.pool_catalog.datasets_for(pool_id)
            if dataset.manifest.interval == interval
            and not dataset.manifest.gap_count
            and not dataset.manifest.duplicate_count
            and (candidate_ids is None or dataset.manifest.dataset_id in candidate_ids)
        ][: max(1, min(int(payload.get("max_datasets", 10)), 20))]
        if not datasets:
            raise BacktestDataError("pool contains no matching quality-passed datasets")
        config = self._config(payload, self._text(payload, "strategy_id"))
        items: list[dict[str, object]] = []
        failures: list[dict[str, str]] = []
        total = len(datasets)
        for index, dataset in enumerate(datasets, start=1):
            context.raise_if_cancelled()
            symbol = dataset.manifest.instrument_key.rsplit(":", 1)[-1]
            try:
                items.append(
                    self.backtest_runs.run(
                        venue_id=dataset.manifest.venue_id,
                        symbol=symbol,
                        interval=interval,
                        config=config,
                        record_task=False,
                        run_id=f"{self._text(payload, 'run_id')}-{dataset.manifest.dataset_id}",
                    )
                )
            except (BacktestDataError, ValueError) as error:
                failures.append({"dataset_id": dataset.manifest.dataset_id, "reason": str(error)})
            context.progress(index, total, message=f"已完成 {index}/{total} 个数据集")
        if not items:
            raise BacktestDataError("no pool dataset could produce a backtest")
        returns = [Decimal(str(item["total_return_pct"])) for item in items]
        batch_id = self._text(payload, "run_id")
        return {
            "batch_id": batch_id,
            "pool_id": pool_id,
            "screening_run_id": screening_run_id,
            "strategy_id": self._text(payload, "strategy_id"),
            "interval": interval,
            "status": "completed" if not failures else "partial",
            "count": len(items),
            "failed_count": len(failures),
            "average_return_pct": str(sum(returns, Decimal("0")) / Decimal(len(returns))),
            "best_return_pct": str(max(returns)),
            "worst_return_pct": str(min(returns)),
            "items": items,
            "failures": failures,
        }

    def _run_history(self, task_id: str, raw_payload: Any) -> None:
        self._refresh_public_network_settings()
        job_id = task_id.removeprefix("history:")
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        raw_queries = payload.get("queries")
        if not isinstance(raw_queries, list) or not raw_queries:
            raise ValueError("history task has no serialized queries")
        queries = tuple(_query(value) for value in raw_queries)
        if self.history_jobs.get(job_id) is None:
            self.history_jobs.restore_from_task_ledger(task_id, self.store.get(task_id))
        if self.history_jobs.get(job_id) is None:
            raise ValueError("history job state was not found")
        self.history_jobs.run_job(job_id, queries)

    def _refresh_public_network_settings(self) -> None:
        snapshot = self.network_settings.current()
        if snapshot.fingerprint() == self.network_snapshot.fingerprint():
            return
        self.history_service.update_network(
            snapshot.history_base_urls,
            snapshot.http_proxies,
            snapshot.api_routes,
        )
        self.network_snapshot = snapshot
        self.settings = settings_with_public_network(self.settings, snapshot)

    def _history_root(self) -> Path:
        path = Path(self.settings.history_data_path)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @staticmethod
    def _payload(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("task payload must be an object")
        return value

    @staticmethod
    def _text(payload: dict[str, Any], key: str) -> str:
        value = str(payload.get(key, "")).strip()
        if not value:
            raise ValueError(f"task payload requires {key}")
        return value

    @staticmethod
    def _decimal(payload: dict[str, Any], key: str) -> Decimal:
        try:
            value = Decimal(str(payload.get(key, "0")))
        except Exception as error:
            raise ValueError(f"{key} must be numeric") from error
        if not value.is_finite():
            raise ValueError(f"{key} must be finite")
        return value

    @classmethod
    def _optional_decimal(cls, payload: dict[str, Any], key: str) -> Decimal | None:
        return None if payload.get(key) in (None, "") else cls._decimal(payload, key)

    @staticmethod
    def _datetime(value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("task datetime must include timezone information")
        return parsed.astimezone(UTC)

    @classmethod
    def _config(cls, payload: dict[str, Any], strategy_id: Any) -> CandleBacktestConfig:
        raw = payload.get("config") if isinstance(payload.get("config"), dict) else payload
        parameters = raw.get("strategy_parameters", raw.get("parameters", {}))
        if not isinstance(parameters, dict):
            raise ValueError("strategy_parameters must be an object")
        return CandleBacktestConfig(
            strategy_id=str(strategy_id or raw.get("strategy_id", "")).strip(),
            initial_quote=cls._decimal(raw, "initial_quote"),
            initial_base=cls._decimal(raw, "initial_base"),
            fee_bps=cls._decimal(raw, "fee_bps"),
            slippage_bps=cls._decimal(raw, "slippage_bps"),
            fast_window=int(raw.get("fast_window", 10)),
            slow_window=int(raw.get("slow_window", 30)),
            allocation_ratio=cls._decimal(raw, "allocation_ratio"),
            momentum_threshold_pct=cls._decimal(raw, "momentum_threshold_pct"),
            parameters=dict(parameters),
        )

    @staticmethod
    def _task_total(kind: str, raw_payload: Any) -> int:
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        if kind == "pool_backtest":
            return max(1, min(int(payload.get("max_datasets", 10)), 20))
        if kind == "research" and payload.get("mode") == "tune":
            grid = payload.get("parameter_grid")
            if isinstance(grid, dict):
                total = 1
                for values in grid.values():
                    total *= max(1, len(values)) if isinstance(values, list) else 1
                return max(1, min(total, 100))
        return 1

    @staticmethod
    def _result_progress(result: Any) -> dict[str, object]:
        if not isinstance(result, dict):
            return {"completed": 1, "total": 1, "percent": 100.0}
        completed = result.get("completed_count", result.get("candidate_count", result.get("count", 1)))
        total = result.get("item_count", result.get("dataset_count", result.get("trial_count", result.get("count", 1))))
        try:
            completed_int = max(0, int(completed or 0))
            total_int = max(1, int(total or 1))
        except (TypeError, ValueError):
            completed_int, total_int = 1, 1
        return {
            "completed": min(completed_int, total_int),
            "total": total_int,
            "percent": 100.0,
        }


def _query(value: Any) -> HistoryQuery:
    if not isinstance(value, dict):
        raise ValueError("history task query must be an object")
    try:
        return HistoryQuery(
            venue_id=str(value["venue_id"]),
            market_type=MarketType.SPOT,
            instrument_key=str(value["instrument_key"]),
            native_symbol=str(value["native_symbol"]),
            interval=CandleInterval.parse(str(value["interval"])),
            start_at=datetime.fromisoformat(str(value["start_at"])),
            end_at=datetime.fromisoformat(str(value["end_at"])),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("history task contains an invalid query") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Crypto Redis task worker")
    parser.add_argument("--once", action="store_true", help="claim at most one task and exit")
    parser.add_argument("--poll-seconds", type=int, default=5)
    args = parser.parse_args(argv)
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
    worker = TaskWorker(Settings.from_env())
    restore_signals = install_shutdown_handlers(
        worker.request_shutdown,
        logger=LOGGER,
        process_name="task-worker",
    )
    try:
        if args.once:
            worker.run_once(timeout_seconds=max(1, min(args.poll_seconds, 60)))
        else:
            worker.run_forever(poll_seconds=max(1, min(args.poll_seconds, 5)))
    finally:
        worker.close()
        restore_signals()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
