"""Failure-injection checks for fixed Redis deliveries and SQL fencing."""

from __future__ import annotations

import threading

import pytest

from backend.app.services.task_queue import RedisTaskQueue, TaskQueueError
from backend.app.services.task_store import TaskStore
from services.task_worker import TaskWorker


class AckRedis:
    def __init__(self) -> None:
        self.queued: list[str] = []
        self.processing: list[str] = []
        self.claims: dict[str, dict[str, str]] = {}

    def lpush(self, key: str, value: str) -> int:
        target = self.queued if key == "queue" else self.processing
        target.insert(0, value)
        return len(target)

    def brpoplpush(self, source: str, destination: str, timeout: int) -> str | None:
        if not self.queued:
            return None
        value = self.queued.pop()
        self.processing.insert(0, value)
        return value

    def lrem(self, key: str, count: int, value: str) -> int:
        target = self.processing
        try:
            target.remove(value)
        except ValueError:
            return 0
        return 1

    def hset(self, key: str, mapping: dict[str, str]) -> int:
        self.claims[key] = dict(mapping)
        return 1

    def expire(self, key: str, seconds: int) -> bool:
        return True

    def delete(self, key: str) -> int:
        self.claims.pop(key, None)
        return 1

def _queue(monkeypatch: pytest.MonkeyPatch, redis: AckRedis) -> RedisTaskQueue:
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: redis)
    return RedisTaskQueue("redis://fault-matrix", queue_name="queue", processing_name="processing")


def test_ack_keeps_processing_and_claim_when_redis_result_is_unknown(monkeypatch) -> None:
    redis = AckRedis()
    queue = _queue(monkeypatch, redis)
    envelope = queue.enqueue("research:ack-error", "research", {"run_id": "ack-error"})
    claimed = queue.claim(timeout_seconds=1, worker_id="worker-ack", lease_seconds=60)
    assert claimed is not None
    claim_key = next(iter(redis.claims))

    def fail_eval(*args: object, **kwargs: object) -> int:
        raise RuntimeError("connection reset")

    redis.eval = fail_eval  # type: ignore[attr-defined]

    with pytest.raises(TaskQueueError, match="unable to acknowledge"):
        queue.ack(claimed)

    assert redis.processing == [str(claimed["_raw"])]
    assert claim_key in redis.claims
    assert envelope["message_id"] == claimed["message_id"]


def test_ack_rejects_a_missing_processing_item_without_deleting_claim(monkeypatch) -> None:
    redis = AckRedis()
    queue = _queue(monkeypatch, redis)
    queue.enqueue("research:ack-missing", "research", {"run_id": "ack-missing"})
    claimed = queue.claim(timeout_seconds=1, worker_id="worker-ack", lease_seconds=60)
    assert claimed is not None
    claim_key = next(iter(redis.claims))
    redis.processing.clear()
    redis.eval = lambda *args, **kwargs: 0  # type: ignore[attr-defined]

    with pytest.raises(TaskQueueError, match="was not present"):
        queue.ack(claimed)

    assert claim_key in redis.claims


def test_sql_claim_fences_old_retry_delivery_before_execution() -> None:
    store = TaskStore("sqlite:///:memory:")
    try:
        task_id = "research:fenced-retry"
        store.create(
            task_id,
            kind="research",
            title="旧投递 fencing",
            payload={"run_id": task_id},
            max_attempts=2,
            stage_dispatch=True,
        )
        first_dispatch = store.pending_dispatches(task_id=task_id)[0]
        first_message_id = str(first_dispatch["message_id"])
        store.mark_dispatch_published(first_message_id)
        first_claim = store.mark_claimed(
            task_id,
            worker_id="worker-old",
            attempt=1,
            lease_seconds=60,
            message_id=first_message_id,
        )
        assert first_claim is not None and first_claim["claim_acquired"] is True

        retried = store.schedule_retry(
            task_id,
            attempt=2,
            error={"kind": "TEMPORARY"},
            expected_worker_id="worker-old",
        )
        assert retried is not None
        retry_dispatch = store.pending_dispatches(task_id=task_id)[0]
        retry_message_id = str(retry_dispatch["message_id"])
        assert retry_message_id != first_message_id
        store.mark_dispatch_published(retry_message_id)

        stale = store.mark_claimed(
            task_id,
            worker_id="worker-new",
            attempt=1,
            lease_seconds=60,
            message_id=first_message_id,
        )
        assert stale is not None
        assert stale["claim_acquired"] is False
        assert stale["claim_rejected"] == "stale_delivery"
        assert stale["expected_message_id"] == retry_message_id

        current = store.mark_claimed(
            task_id,
            worker_id="worker-new",
            attempt=2,
            lease_seconds=60,
            message_id=retry_message_id,
        )
        assert current is not None
        assert current["claim_acquired"] is True
        assert current["attempt"] == 2
    finally:
        store.close()


class WorkerClaimQueue:
    def __init__(self) -> None:
        self.acked = False

    def heartbeat(self, worker_id: str) -> dict[str, object]:
        return {"worker_id": worker_id}

    def reclaim_expired(self, *, lease_seconds: int) -> list[dict[str, object]]:
        return []

    def claim(self, timeout_seconds: int, *, worker_id: str, lease_seconds: int) -> dict[str, object]:
        return {
            "message_id": "worker-forwarded-message",
            "task_id": "research:worker-forwarding",
            "kind": "research",
            "payload": {},
            "attempt": 1,
            "max_attempts": 1,
        }

    def ack(self, envelope: dict[str, object]) -> None:
        self.acked = True


class RecordingClaimStore:
    def __init__(self) -> None:
        self.arguments: dict[str, object] = {}

    def mark_claimed(self, task_id: str, **kwargs: object) -> dict[str, object]:
        self.arguments = kwargs
        return {"task_id": task_id, "status": "queued", "claim_acquired": False}


def test_worker_passes_fixed_delivery_identity_to_sql_claim(monkeypatch) -> None:
    queue = WorkerClaimQueue()
    store = RecordingClaimStore()
    worker = TaskWorker.__new__(TaskWorker)
    worker.queue = queue
    worker.store = store
    worker.worker_id = "worker-forwarding"
    worker._shutdown_requested = threading.Event()

    assert worker.run_once(timeout_seconds=1) is True
    assert store.arguments["message_id"] == "worker-forwarded-message"
    assert store.arguments["attempt"] == 1
    assert queue.acked is True
