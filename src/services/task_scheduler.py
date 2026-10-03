"""Periodic public-history scheduler.

The scheduler only plans missing or stale windows. It never sends an order and
it never treats a successful enqueue as proof that a dataset was downloaded.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import logging
import uuid
from pathlib import Path
import threading

from application.history_archive import ParquetHistoryArchive
from backend.app.services.history_jobs import HistoryJobManager
from backend.app.services.history_metadata import HistoryMetadataRepository
from backend.app.services.history_service import build_history_service
from backend.app.services.domain_state import build_domain_state, build_runtime_state
from backend.app.services.history_scheduler_state import HistorySchedulerState
from backend.app.services.history_sync import DEFAULT_INTERVALS, DEFAULT_SYMBOLS, DEFAULT_VENUES, HistorySyncService
from backend.app.services.news import NewsService
from backend.app.services.news_ingest import NewsIngestor
from backend.app.services.paper_automation import PaperAutomationService
from backend.app.services.paper_follow import PaperFollowService
from backend.app.services.paper_trading import PaperTradingService
from backend.app.services.strategy_registry import StrategyRegistry
from backend.app.services.task_queue import RedisTaskQueue
from backend.app.services.task_store import TaskStore
from backend.app.services.task_log_archive import TaskLogArchive
from backend.app.settings import Settings
from backend.app.services.public_network_settings import (
    PublicNetworkSettingsStore,
    endpoint_defaults_from_settings,
    proxy_defaults_from_settings,
    resolve_public_network_settings_path,
    settings_with_public_network,
)
from services.process_signals import install_shutdown_handlers


LOGGER = logging.getLogger("crypto.task_scheduler")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_JOB_STATUSES = frozenset({"queued", "running", "cancelling"})
RETRYABLE_JOB_STATUSES = frozenset({"blocked", "failed", "partial", "cancelled", "interrupted"})


class HistoryScheduler:
    def __init__(
        self,
        settings: Settings,
        *,
        venues: tuple[str, ...] = DEFAULT_VENUES,
        symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
        intervals: tuple[str, ...] = DEFAULT_INTERVALS,
    ) -> None:
        self.network_settings = PublicNetworkSettingsStore(
            resolve_public_network_settings_path(settings, PROJECT_ROOT),
            defaults=proxy_defaults_from_settings(settings),
            endpoint_defaults=endpoint_defaults_from_settings(settings),
        )
        self.network_snapshot = self.network_settings.current()
        settings = settings_with_public_network(settings, self.network_snapshot)
        self.settings = settings
        self._shutdown_requested = threading.Event()
        self.venues = venues
        self.symbols = symbols
        self.intervals = intervals
        history_root = self._history_root()
        self.store = TaskStore(
            settings.database_url,
            required=True,
            event_archive=TaskLogArchive(history_root / ".runtime" / "task-logs"),
        )
        self.store.reconcile_event_archives()
        self.queue = RedisTaskQueue(settings.redis_url)
        self.history_metadata = HistoryMetadataRepository(self.store)
        self.service = build_history_service(
            settings,
            data_root=history_root,
            metadata_writer=self.history_metadata,
            api_routes=self.network_snapshot.api_routes,
        )
        self.archive = ParquetHistoryArchive(history_root)
        self.jobs = HistoryJobManager(
            self.service,
            state_path=history_root / ".history_jobs.json",
            task_store=self.store,
            task_queue=self.queue,
            archive=self.archive,
            recover_interrupted=False,
        )
        self.sync = HistorySyncService(
            self.service.storage,
            state_path=history_root / ".runtime" / "history-sync.json",
            default_lookback_days=settings.history_sync_lookback_days,
        )
        self.scheduler_state = HistorySchedulerState(
            history_root / ".runtime" / "history-scheduler.json",
            interval_seconds=settings.scheduler_interval_seconds,
            state_store=build_runtime_state(
                self.store,
                history_root / ".runtime",
                "history-scheduler.json",
            ),
        )
        # Paper-follow automation: reuse the same runtime state root as the
        # worker and backend so all processes observe the same configuration.
        domain_state = lambda name: build_domain_state(self.store, history_root / ".runtime", name)
        self.strategy_registry = StrategyRegistry(state_store=domain_state("strategies.json"))
        self.paper_trading = PaperTradingService(
            self.service.storage,
            state_store=domain_state("paper-trading.json"),
            task_store=self.store,
        )
        self.paper_automation = PaperAutomationService(
            self.paper_trading,
            self.strategy_registry,
            state_store=domain_state("paper-automation.json"),
        )
        self.paper_follow = PaperFollowService(
            state_path=history_root / ".runtime" / "paper-follow.json",
        )
        # News intelligence: RSS ingestion on a fixed interval. The ingestor
        # never raises into the tick; failures are logged and retried later.
        self.news_service = NewsService(
            self.service.storage,
            state_store=domain_state("news.json"),
        )
        self.news_ingestor = NewsIngestor(self.news_service)

    def run_once(self) -> dict[str, object]:
        if self.shutdown_requested:
            return {"status": "shutdown", "scheduler": self.scheduler_state.snapshot()}
        now = datetime.now(UTC)
        try:
            self.scheduler_state.patch(
                status="checking",
                last_run_at=now.isoformat(),
                last_result="checking",
                last_error=None,
            )
            result = self._run_once(now)
            follow = self._maybe_dispatch_paper_follow(now, result)
            if follow is not None:
                result["paper_follow"] = follow
            news = self._maybe_ingest_news(now)
            if news is not None:
                result["news_ingest"] = news
        except Exception as error:
            LOGGER.exception("automatic history sync tick failed")
            result = self._record_error(now, error)
        result["scheduler"] = self.scheduler_state.snapshot()
        LOGGER.info(
            "history schedule status=%s job=%s downloads=%s scheduler=%s",
            result.get("status"),
            (result.get("job") or {}).get("job_id") if isinstance(result.get("job"), dict) else "",
            (result.get("plan") or {}).get("download_count") if isinstance(result.get("plan"), dict) else "",
            self.scheduler_state.snapshot().get("status"),
        )
        return result

    def run_forever(self) -> None:
        interval_seconds = max(30, int(self.settings.scheduler_interval_seconds))
        while not self.shutdown_requested:
            self.run_once()
            if self._shutdown_event().wait(interval_seconds):
                break
        LOGGER.info("history scheduler stopped after graceful shutdown")

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_event().is_set()

    def request_shutdown(self) -> None:
        event = self._shutdown_event()
        if not event.is_set():
            LOGGER.info("history scheduler is stopping and will not submit new tasks")
        event.set()

    def _shutdown_event(self) -> threading.Event:
        event = getattr(self, "_shutdown_requested", None)
        if event is None:
            event = threading.Event()
            self._shutdown_requested = event
        return event

    def close(self) -> None:
        self.request_shutdown()
        self.jobs.close()
        self.store.close()

    def _history_root(self) -> Path:
        path = Path(self.settings.history_data_path)
        return path if path.is_absolute() else PROJECT_ROOT / path

    def _maybe_dispatch_paper_follow(
        self, now: datetime, result: dict[str, object]
    ) -> dict[str, object] | None:
        """Enqueue a paper_follow task when the follow profile is due.

        Only fires after a successful history tick (data is up to date) and
        when both the automation profile and the follow profile are enabled.
        Replay-only: the worker runs strategy + benchmark replays, never orders.
        """
        try:
            if str(result.get("status", "")).strip().lower() != "up_to_date":
                return None
            automation = self.paper_automation.get()
            if not automation.get("enabled"):
                return None
            if not self.paper_follow.should_run(now):
                return None
            run_id = uuid.uuid4().hex
            task_id = f"paper-follow:{run_id}"
            self.queue.enqueue(task_id, "paper_follow", {"run_id": run_id})
            self.paper_follow.mark_dispatched()
            LOGGER.info("paper follow dispatched task_id=%s", task_id)
            return {"status": "dispatched", "task_id": task_id, "run_id": run_id}
        except Exception as error:
            LOGGER.warning("paper follow dispatch failed: %s", error)
            return {"status": "dispatch_failed", "error": str(error)}

    def _maybe_ingest_news(self, now: datetime) -> dict[str, object] | None:
        """Run the RSS news ingestor when its interval has elapsed.

        Runs inline in the scheduler process (a few seconds of network I/O);
        any failure is logged and the tick continues unaffected.
        """
        try:
            if not self.news_ingestor.should_run(now):
                return None
            outcome = self.news_ingestor.ingest_once()
            majors = outcome.get("major_events", [])
            if majors:
                LOGGER.warning(
                    "news ingest: %d major event(s): %s",
                    len(majors),
                    "; ".join(str(item.get("title", ""))[:60] for item in majors[:3]),
                )
            else:
                LOGGER.info(
                    "news ingest: ingested=%s skipped=%s",
                    outcome.get("ingested"), outcome.get("skipped"),
                )
            return {"status": "completed", **outcome}
        except Exception as error:
            LOGGER.warning("news ingest failed: %s", error)
            return {"status": "failed", "error": str(error)}

    def _run_once(self, now: datetime) -> dict[str, object]:
        self._refresh_public_network_settings()
        state = self.scheduler_state.snapshot()
        if self._in_backoff(now, state):
            self.scheduler_state.patch(status="backoff", last_result="backoff")
            return {
                "status": "backoff",
                "reason": "上一次自动同步失败，仍在退避冷却期",
                "next_attempt_at": state.get("next_attempt_at"),
            }

        previous = self._check_previous_job(now, state)
        if previous is not None:
            return previous

        result = self.sync.submit(
            self.jobs,
            mode="incremental",
            venues=self.venues,
            symbols=self.symbols,
            intervals=self.intervals,
        )
        plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
        job = result.get("job") if isinstance(result.get("job"), dict) else None
        if job is None:
            self.scheduler_state.patch(
                status="up_to_date",
                last_result="up_to_date",
                last_error=None,
                last_plan_id=plan.get("plan_id"),
                last_job_status="completed",
                last_failure_job_id=None,
                last_failure_status=None,
                consecutive_failures=0,
                next_attempt_at=None,
            )
        else:
            job_status = str(job.get("status", "queued"))
            self.scheduler_state.patch(
                status="active" if job_status in ACTIVE_JOB_STATUSES else "submitted",
                last_result="deduplicated" if job.get("deduplicated") else "submitted",
                last_error=None,
                last_plan_id=plan.get("plan_id"),
                last_job_id=job.get("job_id"),
                last_job_status=job_status,
                next_attempt_at=None,
            )
        return result

    def _refresh_public_network_settings(self) -> None:
        # Some callers inject the scheduler dependencies through ``__new__``
        # for an isolated scheduling test. Keep that seam compatible while
        # the production constructor always installs the settings store.
        network_settings = getattr(self, "network_settings", None)
        network_snapshot = getattr(self, "network_snapshot", None)
        if network_settings is None or network_snapshot is None:
            return
        snapshot = network_settings.current()
        if snapshot.fingerprint() == network_snapshot.fingerprint():
            return
        self.service.update_network(
            snapshot.history_base_urls,
            snapshot.http_proxies,
            snapshot.api_routes,
        )
        self.network_snapshot = snapshot
        self.settings = settings_with_public_network(self.settings, snapshot)

    def _check_previous_job(
        self,
        now: datetime,
        state: dict[str, object],
    ) -> dict[str, object] | None:
        job_id = str(state.get("last_job_id") or "").strip()
        if not job_id:
            return None
        job = self.jobs.get(job_id)
        if job is None:
            return self._start_backoff(
                now,
                reason="自动同步的最近任务状态不可读，为避免重复下载暂缓下一次投递",
                job_id=job_id,
                job_status="unknown",
            )
        job_status = str(job.get("status", "")).lower()
        self.scheduler_state.patch(last_job_status=job_status)
        if job_status in ACTIVE_JOB_STATUSES:
            self.scheduler_state.patch(status="active", last_result="active_job")
            return {"status": "active_job", "job": job}
        if job_status in RETRYABLE_JOB_STATUSES:
            same_failure = (
                state.get("last_failure_job_id") == job_id
                and state.get("last_failure_status") == job_status
            )
            if not same_failure:
                return self._start_backoff(
                    now,
                    reason=f"最近自动同步任务状态为 {job_status}，已进入退避重试",
                    job_id=job_id,
                    job_status=job_status,
                    job=job,
                )
        if job_status == "completed":
            self.scheduler_state.patch(
                status="checking",
                last_result="previous_job_completed",
                last_error=None,
                last_failure_job_id=None,
                last_failure_status=None,
                consecutive_failures=0,
                next_attempt_at=None,
            )
        return None

    def _record_error(self, now: datetime, error: Exception) -> dict[str, object]:
        result = self._start_backoff(
            now,
            reason="自动同步本轮执行失败，已记录并进入退避重试",
            error=error,
        )
        result["status"] = "error"
        result["error"] = {"kind": "SCHEDULER_TICK_FAILED", "message": str(error)[:500]}
        return result

    def _start_backoff(
        self,
        now: datetime,
        *,
        reason: str,
        job_id: str | None = None,
        job_status: str | None = None,
        job: dict[str, object] | None = None,
        error: Exception | None = None,
    ) -> dict[str, object]:
        state = self.scheduler_state.snapshot()
        failures = int(state.get("consecutive_failures", 0) or 0) + 1
        delay = min(
            int(self.settings.scheduler_backoff_max_seconds),
            int(self.settings.scheduler_backoff_initial_seconds) * (2 ** max(0, failures - 1)),
        )
        next_attempt_at = now + timedelta(seconds=delay)
        error_payload: dict[str, object] = {"kind": "HISTORY_SYNC_BACKOFF", "message": reason}
        if error is not None:
            error_payload = {"kind": "SCHEDULER_TICK_FAILED", "message": str(error)[:500]}
        self.scheduler_state.patch(
            status="backoff",
            last_result="backoff",
            last_error=error_payload,
            last_failure_job_id=job_id or state.get("last_job_id"),
            last_failure_status=job_status,
            consecutive_failures=failures,
            next_attempt_at=next_attempt_at.isoformat(),
        )
        result: dict[str, object] = {
            "status": "backoff",
            "reason": reason,
            "next_attempt_at": next_attempt_at.isoformat(),
        }
        if job is not None:
            result["job"] = job
        return result

    @staticmethod
    def _in_backoff(now: datetime, state: dict[str, object]) -> bool:
        raw = str(state.get("next_attempt_at") or "").strip()
        if not raw:
            return False
        try:
            return datetime.fromisoformat(raw).astimezone(UTC) > now
        except ValueError:
            return False


def _csv(value: str, default: tuple[str, ...]) -> tuple[str, ...]:
    items = tuple(item.strip() for item in str(value or "").split(",") if item.strip())
    return items or default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Crypto public-history scheduler")
    parser.add_argument("--once", action="store_true", help="plan and enqueue one incremental sync")
    parser.add_argument("--venues", default=",".join(DEFAULT_VENUES))
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--intervals", default=",".join(DEFAULT_INTERVALS))
    args = parser.parse_args(argv)
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    scheduler = HistoryScheduler(
        settings,
        venues=_csv(args.venues, DEFAULT_VENUES),
        symbols=_csv(args.symbols, DEFAULT_SYMBOLS),
        intervals=_csv(args.intervals, DEFAULT_INTERVALS),
    )
    restore_signals = install_shutdown_handlers(
        scheduler.request_shutdown,
        logger=LOGGER,
        process_name="task-scheduler",
    )
    try:
        if args.once:
            result = scheduler.run_once()
            return 1 if result.get("status") == "error" else 0
        else:
            scheduler.run_forever()
    finally:
        scheduler.close()
        restore_signals()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
