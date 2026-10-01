"""Isolated recovery drills for the Redis task control plane."""

from __future__ import annotations

import threading

import pytest

from backend.app.services.task_dispatcher import TaskDispatcher, TaskExecutionContext
from backend.app.services.task_log_archive import TaskLogArchive
from backend.app.services.task_queue import RedisTaskQueue
from backend.app.services.task_quota import TaskQuota
from backend.app.services.task_result_archive import TaskResultArchive
from backend.app.services.task_store import TaskStore
from services.task_worker import TaskWorker


class IsolatedRedis:
    """Small list/hash Redis double; no production keys or data are touched."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {
            "drill:queue": [],
            "drill:processing": [],
            "drill:dead": [],
        }
        self.hashes: dict[str, dict[str, str]] = {}
        self.markers: set[str] = set()

    def ping(self) -> bool:
        return True

    def lpush(self, key: str, value: str) -> int:
        values = self.lists[key]
        values.insert(0, value)
        return len(values)

    def brpoplpush(self, source: str, destination: str, timeout: int) -> str | None:
        if not self.lists[source]:
            return None
        value = self.lists[source].pop()
        self.lists[destination].insert(0, value)
        return value

    def lrem(self, key: str, count: int, value: str) -> int:
        try:
            self.lists[key].remove(value)
        except ValueError:
            return 0
        return 1

    def lrange(self, key: str, start: int, stop: int) -> list[str]:
        values = self.lists[key]
        end = None if stop == -1 else stop + 1
        return values[start:end]

    def lset(self, key: str, index: int, value: str) -> bool:
        self.lists[key][index] = value
        return True

    def hset(self, key: str, mapping: dict[str, str]) -> int:
        self.hashes[key] = dict(mapping)
        return 1

    def hgetall(self, key: str) -> dict[str, str]:
        return self.hashes.get(key, {})

    def scan_iter(self, match: str):
        return iter(self.hashes)

    def delete(self, key: str) -> int:
        self.hashes.pop(key, None)
        return 1

    def expire(self, key: str, seconds: int) -> bool:
        return True

    def set(self, key: str, value: str, ex: int) -> bool:
        self.hashes[key] = {"value": value}
        return True

    def setnx(self, key: str, value: str) -> bool:
        if key in self.markers:
            return False
        self.markers.add(key)
        return True

    def exists(self, key: str) -> int:
        return int(key in self.markers)

    def llen(self, key: str) -> int:
        return len(self.lists[key])


def test_isolated_dead_letter_replay_executes_and_restores_result(monkeypatch, tmp_path) -> None:
    redis = IsolatedRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: redis)
    queue = RedisTaskQueue(
        "redis://isolated-drill",
        queue_name="drill:queue",
        processing_name="drill:processing",
        dead_letter_name="drill:dead",
    )
    store = TaskStore(
        "sqlite:///:memory:",
        event_archive=TaskLogArchive(tmp_path / "logs"),
    )
    result_archive = TaskResultArchive(tmp_path / "results")
    try:
        source_id = "research:drill-source"
        source_payload = {"run_id": "source", "mode": "compare"}
        store.create(
            source_id,
            kind="research",
            title="隔离死信演练",
            payload=source_payload,
            max_attempts=1,
        )
        queue.enqueue(source_id, "research", source_payload, max_attempts=1)
        claimed = queue.claim(timeout_seconds=1, worker_id="worker-source", lease_seconds=60)
        assert claimed is not None
        dead_letter = queue.dead_letter(
            claimed,
            error={"kind": "DRILL_FAILURE", "message": "isolated failure"},
        )
        store.dead_letter(
            source_id,
            error=dead_letter["error"],
            attempt=1,
            dead_letter=dead_letter,
        )

        dispatcher = TaskDispatcher(store, queue, mode="redis", max_attempts=1)
        first = dispatcher.replay_dead_letter(
            source_id,
            actor="admin",
            reason="isolated recovery drill",
            request_id="drill-request-1",
        )
        repeated = dispatcher.replay_dead_letter(
            source_id,
            actor="admin",
            reason="duplicate request",
            request_id="drill-request-1",
        )

        assert first["status"] == "queued"
        assert repeated["deduplicated"] is True
        assert queue.size() == {"queued": 1, "processing": 0}
        assert "payload" not in queue.dead_letters()[0]

        replay_id = str(first["replay_task_id"])
        replay_claim = queue.claim(timeout_seconds=1, worker_id="worker-replay", lease_seconds=60)
        assert replay_claim is not None
        assert replay_claim["task_id"] == replay_id
        assert store.mark_claimed(
            replay_id,
            worker_id="worker-replay",
            attempt=1,
            lease_seconds=60,
        )
        context = TaskExecutionContext(
            store,
            replay_id,
            result_archive=result_archive,
            worker_id="worker-replay",
        )
        context.started()
        context.completed({"status": "completed", "recovered": True})
        queue.ack(replay_claim)

        target = store.get(replay_id)
        assert target is not None
        assert target["status"] == "completed"
        assert target["worker_id"] is None
        assert result_archive.read(
            target["result"]["archive"],
            expected_task_id=replay_id,
        ) == {"status": "completed", "recovered": True}
        audit = store.get_dead_letter_replay("drill-request-1")
        assert audit is not None and audit["status"] == "completed"
        assert {event["event_type"] for event in store.events(source_id, limit=10)} >= {
            "dead_lettered",
            "dead_letter_replay_requested",
            "dead_letter_replayed",
            "dead_letter_replay_settled",
        }
        assert store.event_archive_status()["pending_count"] == 0
    finally:
        store.close()


def test_history_dead_letter_without_serialized_queries_fails_audit_without_delivery(
    monkeypatch,
    tmp_path,
) -> None:
    """A legacy history DLQ record must fail closed before creating a target task."""
    redis = IsolatedRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: redis)
    queue = RedisTaskQueue(
        "redis://isolated-history-missing-queries",
        queue_name="drill:queue",
        processing_name="drill:processing",
        dead_letter_name="drill:dead",
    )
    store = TaskStore(
        "sqlite:///:memory:",
        event_archive=TaskLogArchive(tmp_path / "logs"),
    )
    source_id = "history:legacy-missing-queries"
    request_id = "history-missing-queries-request"
    try:
        store.create(
            source_id,
            kind="history_download",
            title="历史数据下载",
            payload={
                "request_fingerprint": "legacy-fingerprint",
                "items": [
                    {
                        "venue_id": "okx",
                        "instrument_key": "okx:spot:BTC/USDT",
                        "native_symbol": "BTC-USDT",
                        "interval": "1d",
                        "start_at": "2026-01-01T00:00:00+00:00",
                        "end_at": "2026-01-02T00:00:00+00:00",
                        "status": "failed",
                    }
                ],
            },
            max_attempts=1,
        )
        store.dead_letter(
            source_id,
            error={"kind": "WORKER_FAILED", "message": "legacy delivery lost"},
            attempt=1,
        )

        def replay_history(source_task_id: str, replay_task_id: str, replay_request_id: str):
            worker = TaskWorker.__new__(TaskWorker)
            worker._refresh_public_network_settings = lambda: None
            source = store.get(source_task_id)
            assert source is not None
            worker._run_history(replay_task_id, source["payload"])
            raise AssertionError("history worker accepted a payload without queries")

        dispatcher = TaskDispatcher(
            store,
            queue,
            mode="redis",
            max_attempts=1,
            history_replayer=replay_history,
        )

        with pytest.raises(ValueError, match="history task has no serialized queries"):
            dispatcher.replay_dead_letter(
                source_id,
                actor="admin",
                reason="verify legacy history delivery failure",
                request_id=request_id,
            )

        audit = store.get_dead_letter_replay(request_id)
        assert audit is not None
        assert audit["status"] == "failed"
        assert audit["error"]["kind"] == "VALUEERROR"
        assert "serialized queries" in audit["error"]["message"]
        assert store.get(str(audit["replay_task_id"])) is None
        assert queue.size() == {"queued": 0, "processing": 0}
        assert store.pending_dispatches(task_id=str(audit["replay_task_id"])) == []

        repeated = dispatcher.replay_dead_letter(
            source_id,
            actor="admin",
            reason="same request must remain failed",
            request_id=request_id,
        )
        assert repeated["status"] == "failed"
        assert repeated["queued"] is False
        assert repeated["deduplicated"] is True
        assert repeated["replay_task_id"] == audit["replay_task_id"]
        assert queue.size() == {"queued": 0, "processing": 0}
        assert {event["event_type"] for event in store.events(source_id, limit=10)} >= {
            "dead_letter_replay_requested",
            "dead_letter_replay_failed",
        }
    finally:
        store.close()


def test_isolated_worker_loss_recovery_republishes_from_sql_outbox(monkeypatch, tmp_path) -> None:
    redis = IsolatedRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: redis)
    queue = RedisTaskQueue(
        "redis://isolated-worker-loss",
        queue_name="drill:queue",
        processing_name="drill:processing",
        dead_letter_name="drill:dead",
    )
    store = TaskStore(
        "sqlite:///:memory:",
        event_archive=TaskLogArchive(tmp_path / "logs"),
    )
    try:
        task_id = "research:worker-loss-drill"
        store.create(task_id, kind="research", title="Worker 失联恢复", payload={"run_id": "loss"})
        queue.enqueue(task_id, "research", {"run_id": "loss"})
        claimed = queue.claim(timeout_seconds=1, worker_id="worker-lost", lease_seconds=60)
        assert claimed is not None
        store.mark_claimed(task_id, worker_id="worker-lost", attempt=1, lease_seconds=900)

        claim_key = next(key for key in redis.hashes if ":claim:" in key)
        redis.hashes[claim_key]["claimed_at"] = "2000-01-01T00:00:00+00:00"

        worker = TaskWorker.__new__(TaskWorker)
        worker.queue = queue
        worker.store = store
        worker.worker_id = "worker-new"
        worker.recovery_grace_seconds = 30
        worker._shutdown_requested = threading.Event()

        worker._recover_stale_worker_claims()
        worker._relay_pending_dispatches()

        assert store.get(task_id)["status"] == "queued"
        assert store.pending_dispatches(task_id=task_id) == []
        assert queue.size() == {"queued": 1, "processing": 0}
        replayed = queue.claim(timeout_seconds=1, worker_id="worker-new", lease_seconds=60)
        assert replayed is not None and replayed["task_id"] == task_id
        queue.ack(replayed)
    finally:
        store.close()


def test_isolated_shutdown_recovery_republishes_and_completes_after_restart(monkeypatch, tmp_path) -> None:
    redis = IsolatedRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: redis)
    queue = RedisTaskQueue(
        "redis://isolated-shutdown",
        queue_name="drill:queue",
        processing_name="drill:processing",
        dead_letter_name="drill:dead",
    )
    store = TaskStore(
        "sqlite:///:memory:",
        event_archive=TaskLogArchive(tmp_path / "logs"),
    )
    task_id = "research:shutdown-drill"
    payload = {"run_id": "shutdown-drill"}
    try:
        store.create(
            task_id,
            kind="research",
            title="停机恢复演练",
            payload=payload,
            stage_dispatch=True,
        )

        first = TaskWorker.__new__(TaskWorker)
        first.queue = queue
        first.store = store
        first.worker_id = "worker-before-shutdown"
        first.quota = TaskQuota()
        first._shutdown_requested = threading.Event()

        def interrupt(_kind, _payload, context):
            first.request_shutdown()
            context.raise_if_cancelled()
            return {"status": "completed"}

        first._run_kind = interrupt
        assert first.run_once(timeout_seconds=1) is True
        assert store.get(task_id)["status"] == "queued"
        assert queue.size() == {"queued": 0, "processing": 0}
        assert len(store.pending_dispatches(task_id=task_id)) == 1

        second = TaskWorker.__new__(TaskWorker)
        second.queue = queue
        second.store = store
        second.worker_id = "worker-after-restart"
        second.quota = TaskQuota()
        second._shutdown_requested = threading.Event()
        second._run_kind = lambda _kind, _payload, _context: {
            "status": "completed",
            "recovered": True,
        }

        assert second.run_once(timeout_seconds=1) is True
        record = store.get(task_id)
        assert record is not None
        assert record["status"] == "completed"
        assert record["result"] == {"status": "completed", "recovered": True}
        assert queue.size() == {"queued": 0, "processing": 0}
        assert store.pending_dispatches(task_id=task_id) == []
        event_types = [event["event_type"] for event in store.events(task_id, limit=10)]
        assert event_types[0] == "completed"
        assert event_types.count("started") == 2
        assert "requeued_after_shutdown" in event_types
    finally:
        store.close()


def test_isolated_retry_recovery_commits_sql_before_republishing(monkeypatch, tmp_path) -> None:
    redis = IsolatedRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: redis)
    queue = RedisTaskQueue(
        "redis://isolated-retry",
        queue_name="drill:queue",
        processing_name="drill:processing",
        dead_letter_name="drill:dead",
    )
    store = TaskStore(
        "sqlite:///:memory:",
        event_archive=TaskLogArchive(tmp_path / "logs"),
    )
    task_id = "research:retry-drill"
    payload = {"run_id": "retry-drill"}
    try:
        store.create(
            task_id,
            kind="research",
            title="重试双写演练",
            payload=payload,
            stage_dispatch=True,
            max_attempts=2,
        )
        first = TaskWorker.__new__(TaskWorker)
        first.queue = queue
        first.store = store
        first.worker_id = "worker-before-retry"
        first.quota = TaskQuota()
        first._shutdown_requested = threading.Event()
        first._run_kind = lambda _kind, _payload, _context: (_ for _ in ()).throw(
            RuntimeError("temporary retry failure")
        )

        assert first.run_once(timeout_seconds=1) is True
        after_failure = store.get(task_id)
        assert after_failure is not None
        assert after_failure["status"] == "queued"
        assert after_failure["attempt"] == 2
        assert store.pending_dispatches(task_id=task_id)
        assert queue.size() == {"queued": 0, "processing": 0}

        second = TaskWorker.__new__(TaskWorker)
        second.queue = queue
        second.store = store
        second.worker_id = "worker-after-retry"
        second.quota = TaskQuota()
        second._shutdown_requested = threading.Event()
        second._run_kind = lambda _kind, _payload, _context: {
            "status": "completed",
            "recovered": True,
        }

        assert second.run_once(timeout_seconds=1) is True
        finished = store.get(task_id)
        assert finished is not None
        assert finished["status"] == "completed"
        assert finished["attempt"] == 2
        assert store.pending_dispatches(task_id=task_id) == []
        assert queue.size() == {"queued": 0, "processing": 0}
        assert [event["event_type"] for event in store.events(task_id)].count("retry_scheduled") == 1
    finally:
        store.close()
