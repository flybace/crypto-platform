import threading
from types import SimpleNamespace
import json

from services.task_scheduler import HistoryScheduler
from adapters.standalone.state_store import SqlStateStore
from backend.app.services.history_scheduler_state import HistorySchedulerState
from backend.app.services.task_store import TaskStore


class FakeSync:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result or {
            "status": "submitted",
            "plan": {"plan_id": "plan-1", "download_count": 1},
            "job": {"job_id": "job-1", "status": "queued"},
        }
        self.error = error
        self.calls = 0

    def submit(self, *_args, **_kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class FakeJobs:
    def __init__(self, jobs=None) -> None:
        self.jobs = dict(jobs or {})

    def get(self, job_id):
        return self.jobs.get(job_id)


def _scheduler(tmp_path, *, sync=None, jobs=None):
    scheduler = HistoryScheduler.__new__(HistoryScheduler)
    scheduler.settings = SimpleNamespace(
        scheduler_interval_seconds=900,
        scheduler_backoff_initial_seconds=300,
        scheduler_backoff_max_seconds=21600,
    )
    scheduler.scheduler_state = HistorySchedulerState(
        tmp_path / "history-scheduler.json",
        interval_seconds=900,
    )
    scheduler.sync = sync or FakeSync()
    scheduler.jobs = jobs or FakeJobs()
    scheduler.venues = ("binance",)
    scheduler.symbols = ("BTC/USDT",)
    scheduler.intervals = ("1d",)
    scheduler._shutdown_requested = threading.Event()
    return scheduler


def test_scheduler_submits_once_then_waits_for_active_job(tmp_path) -> None:
    sync = FakeSync()
    scheduler = _scheduler(tmp_path, sync=sync, jobs=FakeJobs())

    first = scheduler.run_once()
    assert first["status"] == "submitted"
    assert sync.calls == 1

    scheduler.jobs.jobs["job-1"] = {"job_id": "job-1", "status": "running"}
    second = scheduler.run_once()
    assert second["status"] == "active_job"
    assert second["job"]["job_id"] == "job-1"
    assert sync.calls == 1


def test_scheduler_persists_failure_backoff_and_does_not_requeue_during_cooldown(tmp_path) -> None:
    sync = FakeSync(error=RuntimeError("redis unavailable"))
    scheduler = _scheduler(tmp_path, sync=sync)

    first = scheduler.run_once()
    assert first["status"] == "error"
    assert sync.calls == 1
    state = scheduler.scheduler_state.snapshot()
    assert state["status"] == "backoff"
    assert state["consecutive_failures"] == 1
    assert state["next_attempt_at"]

    second = scheduler.run_once()
    assert second["status"] == "backoff"
    assert sync.calls == 1


def test_scheduler_state_round_trips_automatic_status(tmp_path) -> None:
    path = tmp_path / "history-scheduler.json"
    state = HistorySchedulerState(path, interval_seconds=900)
    reader = HistorySchedulerState(path, interval_seconds=900)
    state.patch(status="up_to_date", last_result="up_to_date", consecutive_failures=0)

    snapshot = reader.snapshot()
    assert snapshot["automatic"] is True
    assert snapshot["status"] == "up_to_date"
    assert snapshot["interval_seconds"] == 900


def test_scheduler_state_uses_sql_as_authority_and_keeps_json_recovery_copy(tmp_path) -> None:
    database = tmp_path / "control-plane.sqlite3"
    legacy = tmp_path / "history-scheduler.json"
    legacy.write_text(
        json.dumps({"version": 1, "status": "legacy", "consecutive_failures": 2}),
        encoding="utf-8",
    )
    first_store = TaskStore(f"sqlite:///{database.as_posix()}", required=True)
    first = HistorySchedulerState(
        legacy,
        interval_seconds=900,
        state_store=SqlStateStore(
            first_store,
            "crypto.runtime.history-scheduler",
            legacy_path=legacy,
        ),
    )

    assert first.snapshot()["status"] == "legacy"
    first.patch(status="up_to_date", last_result="up_to_date", consecutive_failures=0)
    legacy.write_text(json.dumps({"status": "stale-recovery-copy"}), encoding="utf-8")
    first_store.close()

    second_store = TaskStore(f"sqlite:///{database.as_posix()}", required=True)
    second = HistorySchedulerState(
        legacy,
        interval_seconds=900,
        state_store=SqlStateStore(
            second_store,
            "crypto.runtime.history-scheduler",
            legacy_path=legacy,
        ),
    )
    assert second.snapshot()["status"] == "up_to_date"
    assert second_store.domain_snapshot_status()["snapshot_count"] == 1
    second_store.close()


def test_scheduler_does_not_submit_after_shutdown_is_requested(tmp_path) -> None:
    sync = FakeSync()
    scheduler = _scheduler(tmp_path, sync=sync)

    scheduler.request_shutdown()

    result = scheduler.run_once()

    assert result["status"] == "shutdown"
    assert sync.calls == 0


def test_scheduler_loop_exits_after_shutdown_request(tmp_path) -> None:
    scheduler = _scheduler(tmp_path)
    calls = 0

    def run_once():
        nonlocal calls
        calls += 1
        scheduler.request_shutdown()
        return {"status": "submitted"}

    scheduler.run_once = run_once

    scheduler.run_forever()

    assert calls == 1
