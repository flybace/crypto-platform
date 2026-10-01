import pytest

from backend.app.services.task_dispatcher import (
    TaskDispatchError,
    TaskDispatcher,
    TaskExecutionContext,
    TaskLeaseLost,
)
from backend.app.services.task_result_archive import TaskResultArchive
from backend.app.services.task_store import TaskStore


class FakeQueue:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []
        self.replay_calls: list[dict[str, object]] = []
        self.fail = False

    def enqueue(
        self,
        task_id: str,
        kind: str,
        payload=None,
        *,
        attempt: int = 1,
        max_attempts: int = 3,
        message_id: str | None = None,
    ) -> dict[str, object]:
        if self.fail:
            raise RuntimeError("redis unavailable")
        envelope = {"message_id": message_id or f"message-{len(self.items) + 1}", "task_id": task_id, "kind": kind, "payload": payload or {}, "attempt": attempt, "max_attempts": max_attempts}
        self.items.append(envelope)
        return envelope

    def replay_dead_letter(
        self,
        source_task_id: str,
        kind: str,
        payload,
        *,
        replay_task_id: str,
        request_id: str,
        max_attempts: int,
        message_id: str | None = None,
    ) -> dict[str, object]:
        self.replay_calls.append({
            "source_task_id": source_task_id,
            "request_id": request_id,
        })
        return {
            "envelope": self.enqueue(
                replay_task_id,
                kind,
                payload,
                max_attempts=max_attempts,
                message_id=message_id,
            ),
            "deduplicated": False,
        }


def _dispatcher(store: TaskStore, queue: FakeQueue) -> TaskDispatcher:
    return TaskDispatcher(store, queue, mode="redis")


def test_dispatch_is_idempotent_for_same_task_and_payload() -> None:
    store = TaskStore("sqlite:///:memory:")
    queue = FakeQueue()
    try:
        dispatcher = _dispatcher(store, queue)
        payload = {"run_id": "run-1", "venue_id": "binance", "symbol": "BTC/USDT"}
        first = dispatcher.dispatch("backtest", "策略回测", payload, task_id="backtest:run-1")
        same_task = dispatcher.dispatch("backtest", "策略回测", payload, task_id="backtest:run-1")
        different_task = dispatcher.dispatch("backtest", "策略回测", payload, task_id="backtest:run-2")

        assert first["task_id"] == "backtest:run-1"
        assert same_task["deduplicated"] is True
        assert different_task["deduplicated"] is True
        assert different_task["task_id"] == "backtest:run-1"
        assert len(queue.items) == 1
    finally:
        store.close()


def test_dispatch_keeps_sql_task_queued_when_redis_is_temporarily_unavailable() -> None:
    store = TaskStore("sqlite:///:memory:")
    queue = FakeQueue()
    try:
        dispatcher = _dispatcher(store, queue)
        payload = {"run_id": "run-outbox", "venue_id": "binance", "symbol": "BTC/USDT"}
        queue.fail = True
        with pytest.raises(TaskDispatchError, match="unable to enqueue durable task"):
            dispatcher.dispatch("research", "研究任务", payload, task_id="research:run-outbox")

        queued = store.get("research:run-outbox")
        assert queued is not None
        assert queued["status"] == "queued"
        assert len(store.pending_dispatches(task_id="research:run-outbox")) == 1

        queue.fail = False
        recovered = dispatcher.dispatch(
            "research",
            "研究任务",
            payload,
            task_id="research:run-outbox",
        )
        assert recovered["deduplicated"] is True
        assert len(queue.items) == 1
        assert store.pending_dispatches(task_id="research:run-outbox") == []
    finally:
        store.close()


def test_cancel_and_retry_create_a_new_run_without_requeueing_the_old_task() -> None:
    store = TaskStore("sqlite:///:memory:")
    queue = FakeQueue()
    try:
        dispatcher = _dispatcher(store, queue)
        dispatcher.dispatch(
            "research",
            "研究任务",
            {"run_id": "run-1", "mode": "compare"},
            task_id="research:run-1",
        )
        cancelled = dispatcher.cancel("research:run-1")
        assert cancelled["status"] == "cancelling"
        store.sync("research:run-1", status="failed", error={"kind": "TEST"})

        retried = dispatcher.retry("research:run-1")
        assert retried["task_id"] != "research:run-1"
        assert retried["run_id"]
        new_record = store.get(str(retried["task_id"]))
        assert new_record is not None
        assert new_record["retry_of"] == "research:run-1"
        assert new_record["payload"]["run_id"] == retried["run_id"]
        assert len(queue.items) == 2
    finally:
        store.close()


def test_dead_letter_replay_is_audited_and_idempotent() -> None:
    store = TaskStore("sqlite:///:memory:")
    queue = FakeQueue()
    try:
        dispatcher = _dispatcher(store, queue)
        source_id = "research:dead-replay"
        store.create(
            source_id,
            kind="research",
            title="研究任务",
            payload={"run_id": "original", "mode": "compare"},
            max_attempts=2,
        )
        store.dead_letter(
            source_id,
            error={"kind": "WORKER_FAILED", "message": "permanent"},
            attempt=2,
        )

        replayed = dispatcher.replay_dead_letter(
            source_id,
            actor="admin",
            reason="temporary upstream failure recovered",
            request_id="replay-request-1",
        )
        repeated = dispatcher.replay_dead_letter(
            source_id,
            actor="admin",
            reason="same request",
            request_id="replay-request-1",
        )

        assert replayed["status"] == "queued"
        assert replayed["replay_task_id"] == repeated["replay_task_id"]
        assert repeated["deduplicated"] is True
        assert len(queue.items) == 1
        assert queue.replay_calls == [{
            "source_task_id": source_id,
            "request_id": "replay-request-1",
        }]
        target = store.get(str(replayed["replay_task_id"]))
        assert target is not None
        assert target["retry_of"] == source_id
        assert target["payload"]["run_id"] != "original"
        with pytest.raises(ValueError, match="requires audited replay"):
            dispatcher.retry(source_id)

        audit = store.get_dead_letter_replay("replay-request-1")
        assert audit is not None
        assert audit["status"] == "queued"
        assert store.events(source_id)[0]["event_type"] == "dead_letter_replayed"
    finally:
        store.close()


def test_execution_context_preserves_partial_terminal_status() -> None:
    store = TaskStore("sqlite:///:memory:")
    try:
        store.create("pool-backtest:run-1", kind="pool_backtest", title="币池回测")
        context = TaskExecutionContext(store, "pool-backtest:run-1")
        context.started(total=3)
        context.progress(2, 3, message="已完成 2/3 个数据集")
        context.completed({"status": "partial", "count": 2}, progress={"completed": 3, "total": 3, "percent": 100.0})

        record = store.get("pool-backtest:run-1")
        assert record is not None
        assert record["status"] == "partial"
        assert record["result"] == {"status": "partial", "count": 2}
        event = store.events("pool-backtest:run-1")[0]
        assert event["event_type"] == "partial"
        assert event["payload"] == {"summary": {"status": "partial"}}
    finally:
        store.close()


def test_execution_context_stores_archive_reference_in_completion_event(tmp_path) -> None:
    store = TaskStore("sqlite:///:memory:")
    archive = TaskResultArchive(tmp_path / "results")
    try:
        store.create("research:run-archive", kind="research", title="研究任务")
        context = TaskExecutionContext(store, "research:run-archive", result_archive=archive)
        result = {"status": "completed", "items": [{"score": 1}], "count": 1}

        context.completed(result)

        record = store.get("research:run-archive")
        assert record is not None
        reference = record["result"]["archive"]
        assert archive.read(reference, expected_task_id="research:run-archive") == result
        event = store.events("research:run-archive")[0]
        assert event["payload"] == {"summary": {"status": "completed"}, "archive": reference}
        assert "items" not in event["payload"]
    finally:
        store.close()


def test_execution_context_fences_a_stale_worker_after_lease_recovery() -> None:
    store = TaskStore("sqlite:///:memory:")
    try:
        task_id = "research:lease-fence"
        store.create(task_id, kind="research", title="研究任务", payload={"run_id": "fence"})
        store.mark_claimed(task_id, worker_id="worker-old", attempt=1, lease_seconds=60)
        with store._engine.begin() as connection:
            connection.execute(
                store._tasks.update()
                .where(store._tasks.c.task_id == task_id)
                .values(lease_expires_at="2000-01-01T00:00:00+00:00")
            )

        assert store.recover_expired_leases() == [{
            "task_id": task_id,
            "message_id": store.pending_dispatches(task_id=task_id)[0]["message_id"],
            "attempt": 1,
            "max_attempts": 3,
            "worker_id": "worker-old",
        }]
        store.mark_claimed(task_id, worker_id="worker-new", attempt=1, lease_seconds=60)
        stale = TaskExecutionContext(store, task_id, worker_id="worker-old")

        with pytest.raises(TaskLeaseLost, match="no longer owned"):
            stale.completed({"status": "completed", "value": "stale"})

        current = store.get(task_id)
        assert current is not None
        assert current["status"] == "running"
        assert current["worker_id"] == "worker-new"
        assert current["result"] == {}
    finally:
        store.close()
