import threading

from backend.app.services.task_store import TaskStore
from backend.app.services.task_quota import TaskQuota
from services.task_worker import TaskWorker


class WorkerQueue:
    def __init__(self, envelope: dict[str, object]) -> None:
        self.envelope = envelope
        self.claimed = False
        self.renewed = False
        self.acked = False
        self.requeued: list[dict[str, object]] = []

    def heartbeat(self, worker_id: str) -> dict[str, object]:
        return {"worker_id": worker_id}

    def requeue_expired(self, *, lease_seconds: int) -> int:
        return 0

    def claim(self, timeout_seconds: int, *, worker_id: str, lease_seconds: int) -> dict[str, object] | None:
        if self.claimed:
            return None
        self.claimed = True
        return dict(self.envelope)

    def renew(self, envelope: dict[str, object], *, lease_seconds: int) -> None:
        self.renewed = True

    def ack(self, envelope: dict[str, object]) -> None:
        self.acked = True

    def requeue(self, envelope: dict[str, object], *, attempt: int, error: dict[str, object]) -> None:
        self.requeued.append({"attempt": attempt, "error": error})


class FailingWorkerQueue(WorkerQueue):
    def __init__(self, envelope: dict[str, object]) -> None:
        super().__init__(envelope)
        self.requeued: list[dict[str, object]] = []
        self.dead_lettered: list[dict[str, object]] = []
        self.store = None
        self.status_at_dead_letter: str | None = None

    def requeue(self, envelope: dict[str, object], *, attempt: int, error: dict[str, object]) -> None:
        self.requeued.append({"attempt": attempt, "error": error})

    def dead_letter(self, envelope: dict[str, object], *, error: dict[str, object]) -> dict[str, object]:
        self.dead_lettered.append(error)
        if self.store is not None:
            record = self.store.get(str(envelope["task_id"]))
            self.status_at_dead_letter = None if record is None else str(record.get("status"))
        return {
            "dead_letter_id": "dlq-worker-1",
            "message_id": envelope.get("message_id", ""),
            "task_id": envelope["task_id"],
            "kind": envelope["kind"],
            "attempt": envelope.get("attempt", 1),
            "max_attempts": envelope.get("max_attempts", 3),
        }

    def dead_letter_metadata(self, envelope: dict[str, object], *, error: dict[str, object]) -> dict[str, object]:
        return {
            "dead_letter_id": "dlq-worker-1",
            "message_id": envelope.get("message_id", ""),
            "task_id": envelope["task_id"],
            "kind": envelope["kind"],
            "attempt": envelope.get("attempt", 1),
            "max_attempts": envelope.get("max_attempts", 3),
        }


class StaleClaimQueue(WorkerQueue):
    def __init__(self, claim: dict[str, object]) -> None:
        super().__init__({})
        self.stale = claim
        self.released: list[dict[str, object]] = []

    def stale_claims(self, **kwargs) -> list[dict[str, object]]:
        return [self.stale]

    def release_claim(self, claim: dict[str, object]) -> None:
        self.released.append(claim)


class PaperAutomationStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run_now(self, *, run_id: str, config: dict[str, object], record_task: bool) -> dict[str, object]:
        self.calls.append({"run_id": run_id, "config": config, "record_task": record_task})
        return {"run_id": run_id, "status": "completed", "count": 1}


def test_worker_routes_queued_kind_to_domain_service_and_persists_completion() -> None:
    payload = {"run_id": "run-1", "config": {"enabled": False, "interval": "1h"}}
    queue = WorkerQueue({
        "message_id": "message-1",
        "task_id": "paper-automation:run-1",
        "kind": "paper_automation",
        "payload": payload,
    })
    store = TaskStore("sqlite:///:memory:")
    try:
        store.create(
            "paper-automation:run-1",
            kind="paper_automation",
            title="自动回放",
            payload=payload,
        )
        worker = TaskWorker.__new__(TaskWorker)
        worker.queue = queue
        worker.store = store
        worker.worker_id = "test-worker"
        worker.paper_automation = PaperAutomationStub()

        assert worker.run_once(timeout_seconds=1) is True
        record = store.get("paper-automation:run-1")
        assert record is not None
        assert record["status"] == "completed"
        assert record["result"]["run_id"] == "run-1"
        assert worker.paper_automation.calls == [{
            "run_id": "run-1",
            "config": payload["config"],
            "record_task": False,
        }]
        assert queue.renewed is True
        assert queue.acked is True
        assert [event["event_type"] for event in store.events("paper-automation:run-1")] == [
            "completed",
            "started",
        ]
    finally:
        store.close()


def test_worker_dead_letters_non_retryable_failure_after_attempt_budget() -> None:
    payload = {"run_id": "run-2"}
    queue = FailingWorkerQueue({
        "message_id": "message-2",
        "task_id": "research:run-2",
        "kind": "research",
        "payload": payload,
        "attempt": 2,
        "max_attempts": 2,
    })
    store = TaskStore("sqlite:///:memory:")
    try:
        store.create(
            "research:run-2",
            kind="research",
            title="研究任务",
            payload=payload,
            max_attempts=2,
        )
        worker = TaskWorker.__new__(TaskWorker)
        worker.queue = queue
        worker.store = store
        queue.store = store
        worker.worker_id = "test-worker"
        worker.quota = TaskQuota()
        worker._run_kind = lambda kind, raw_payload, context: (_ for _ in ()).throw(RuntimeError("boom"))

        assert worker.run_once(timeout_seconds=1) is True
        record = store.get("research:run-2")
        assert record is not None
        assert record["status"] == "dead_lettered"
        assert record["attempt"] == 2
        assert queue.dead_lettered == [{"kind": "RUNTIMEERROR", "message": "boom"}]
        assert queue.status_at_dead_letter == "dead_lettered"
        event = store.events("research:run-2")[0]
        assert event["event_type"] == "dead_lettered"
        assert event["payload"]["dead_letter"]["dead_letter_id"] == "dlq-worker-1"
        assert "payload" not in event["payload"]["dead_letter"]
    finally:
        store.close()


def test_worker_requeues_retryable_failure_before_max_attempts() -> None:
    payload = {"run_id": "run-3"}
    queue = FailingWorkerQueue({
        "message_id": "message-3",
        "task_id": "research:run-3",
        "kind": "research",
        "payload": payload,
        "attempt": 1,
        "max_attempts": 2,
    })
    store = TaskStore("sqlite:///:memory:")
    try:
        store.create("research:run-3", kind="research", title="研究任务", payload=payload, max_attempts=2)
        worker = TaskWorker.__new__(TaskWorker)
        worker.queue = queue
        worker.store = store
        worker.worker_id = "test-worker"
        worker.quota = TaskQuota()
        worker._run_kind = lambda kind, raw_payload, context: (_ for _ in ()).throw(RuntimeError("temporary"))

        assert worker.run_once(timeout_seconds=1) is True
        record = store.get("research:run-3")
        assert record is not None
        assert record["status"] == "queued"
        assert record["attempt"] == 2
        assert queue.requeued == []
        assert queue.acked is True
        pending = store.pending_dispatches(task_id="research:run-3")
        assert len(pending) == 1
        assert pending[0]["attempt"] == 2
    finally:
        store.close()


def test_worker_keeps_redis_claim_when_sql_lease_acquisition_fails() -> None:
    queue = WorkerQueue({
        "message_id": "message-sql-down",
        "task_id": "research:sql-down",
        "kind": "research",
        "payload": {},
    })
    store = TaskStore("sqlite:///:memory:")
    try:
        store.create("research:sql-down", kind="research", title="SQL 不可用")

        def fail_claim(*args, **kwargs):
            raise RuntimeError("database unavailable")

        store.mark_claimed = fail_claim
        worker = TaskWorker.__new__(TaskWorker)
        worker.queue = queue
        worker.store = store
        worker.worker_id = "test-worker"
        worker._shutdown_requested = threading.Event()

        assert worker.run_once(timeout_seconds=1) is False
        assert queue.acked is False
    finally:
        store.close()


def test_worker_does_not_claim_after_shutdown_is_requested() -> None:
    queue = WorkerQueue({"task_id": "research:run-4", "kind": "research", "payload": {}})
    worker = TaskWorker.__new__(TaskWorker)
    worker.queue = queue
    worker.worker_id = "test-worker"
    worker._shutdown_requested = threading.Event()

    worker.request_shutdown()

    assert worker.run_once(timeout_seconds=1) is False
    assert queue.claimed is False


def test_worker_returns_a_message_claimed_during_shutdown_to_the_queue() -> None:
    queue = WorkerQueue({"task_id": "research:run-draining", "kind": "research", "payload": {}})
    worker = TaskWorker.__new__(TaskWorker)
    worker.queue = queue
    worker.worker_id = "test-worker"
    worker._shutdown_requested = threading.Event()

    original_claim = queue.claim

    def claim_then_shutdown(*args, **kwargs):
        result = original_claim(*args, **kwargs)
        worker.request_shutdown()
        return result

    queue.claim = claim_then_shutdown

    assert worker.run_once(timeout_seconds=1) is False
    assert queue.acked is False
    assert queue.requeued[0]["error"]["kind"] == "WORKER_DRAINING"


def test_worker_finishes_current_task_before_draining() -> None:
    payload = {"run_id": "run-5"}
    queue = WorkerQueue({
        "message_id": "message-5",
        "task_id": "research:run-5",
        "kind": "research",
        "payload": payload,
    })
    store = TaskStore("sqlite:///:memory:")
    try:
        store.create("research:run-5", kind="research", title="研究任务", payload=payload)
        worker = TaskWorker.__new__(TaskWorker)
        worker.queue = queue
        worker.store = store
        worker.worker_id = "test-worker"
        worker.quota = TaskQuota()
        worker._shutdown_requested = threading.Event()

        def execute(_kind, _payload, _context):
            worker.request_shutdown()
            return {"run_id": "run-5", "status": "completed"}

        worker._run_kind = execute

        assert worker.run_once(timeout_seconds=1) is True
        assert store.get("research:run-5")["status"] == "completed"
        assert queue.acked is True
        assert worker.run_once(timeout_seconds=1) is False
    finally:
        store.close()


def test_worker_requeues_task_through_sql_outbox_when_shutdown_hits_checkpoint() -> None:
    payload = {"run_id": "run-shutdown"}
    task_id = "research:run-shutdown"
    queue = WorkerQueue({
        "message_id": "message-shutdown",
        "task_id": task_id,
        "kind": "research",
        "payload": payload,
    })
    store = TaskStore("sqlite:///:memory:")
    try:
        store.create(task_id, kind="research", title="停机恢复", payload=payload)
        worker = TaskWorker.__new__(TaskWorker)
        worker.queue = queue
        worker.store = store
        worker.worker_id = "test-worker"
        worker.quota = TaskQuota()
        worker._shutdown_requested = threading.Event()

        def execute(_kind, _payload, context):
            worker.request_shutdown()
            context.raise_if_cancelled()
            return {"status": "completed"}

        worker._run_kind = execute

        assert worker.run_once(timeout_seconds=1) is True
        record = store.get(task_id)
        assert record is not None
        assert record["status"] == "queued"
        assert record["worker_id"] is None
        assert record["error"]["kind"] == "WORKER_SHUTDOWN"
        assert queue.acked is True
        pending = store.pending_dispatches(task_id=task_id)
        assert len(pending) == 1
        assert pending[0]["status"] == "pending"
        assert store.events(task_id, limit=10)[0]["event_type"] == "requeued_after_shutdown"
    finally:
        store.close()


def test_worker_recovers_sql_before_releasing_a_stale_redis_claim(tmp_path) -> None:
    task_id = "research:stale-claim"
    queue = StaleClaimQueue(
        {
            "task_id": task_id,
            "message_id": "old-message",
            "attempt": 1,
            "worker_id": "worker-lost",
            "_raw": '{"task_id":"research:stale-claim"}',
            "_claim_key": "crypto:control_tasks:claim:v1:old-message",
        }
    )
    store = TaskStore("sqlite:///:memory:")
    try:
        store.create(task_id, kind="research", title="失联恢复")
        store.mark_claimed(task_id, worker_id="worker-lost", attempt=1, lease_seconds=900)
        worker = TaskWorker.__new__(TaskWorker)
        worker.queue = queue
        worker.store = store
        worker.worker_id = "worker-new"
        worker.recovery_grace_seconds = 60

        worker._recover_stale_worker_claims()

        assert store.get(task_id)["status"] == "queued"
        assert len(queue.released) == 1
    finally:
        store.close()


def test_worker_loop_exits_after_shutdown_request() -> None:
    worker = TaskWorker.__new__(TaskWorker)
    worker.worker_id = "test-worker"
    worker._shutdown_requested = threading.Event()
    calls = 0

    def run_once(*, timeout_seconds):
        nonlocal calls
        calls += 1
        worker.request_shutdown()
        return True

    worker.run_once = run_once

    worker.run_forever(poll_seconds=1)

    assert calls == 1
