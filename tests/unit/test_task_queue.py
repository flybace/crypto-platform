from datetime import UTC, datetime, timedelta
import json

import pytest

from backend.app.services.task_queue import (
    RECEIPT_CONTRACT_VERSION,
    RedisTaskQueue,
    TaskQueueError,
)


class FakeRedis:
    def __init__(self) -> None:
        self.queued: list[str] = []
        self.processing: list[str] = []
        self.dead: list[str] = []
        self.heartbeats: dict[str, str] = {}
        self.claims: dict[str, dict[str, str]] = {}
        self.expirations: dict[str, int] = {}

    def ping(self) -> bool:
        return True

    def lpush(self, key: str, value: str) -> int:
        target = self.queued if key == "queue" else self.dead if key.endswith(":dead:v1") else self.processing
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

    def set(self, key: str, value: str, ex: int) -> bool:
        self.heartbeats[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.heartbeats.get(key)

    def llen(self, key: str) -> int:
        if key == "queue":
            return len(self.queued)
        if key.endswith(":dead:v1"):
            return len(self.dead)
        return len(self.processing)

    def lrange(self, key: str, start: int, stop: int) -> list[str]:
        if key == "queue":
            target = self.queued
        elif key.endswith(":dead:v1"):
            target = self.dead
        else:
            target = self.processing
        end = None if stop == -1 else stop + 1
        return target[start:end]

    def lset(self, key: str, index: int, value: str) -> bool:
        target = self.dead if key.endswith(":dead:v1") else self.queued
        target[index] = value
        return True

    def delete(self, key: str) -> int:
        self.claims.pop(key, None)
        return 1

    def hset(self, key: str, mapping: dict[str, str]) -> int:
        self.claims[key] = mapping
        return 1

    def expire(self, key: str, seconds: int) -> bool:
        self.expirations[key] = int(seconds)
        return True

    def scan_iter(self, match: str):
        return iter(self.claims)

    def hgetall(self, key: str) -> dict[str, str]:
        return self.claims.get(key, {})

    def exists(self, key: str) -> int:
        return int(key in self.heartbeats or key in self.claims)


class IdempotentFakeRedis(FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.markers: set[str] = set()
        self.marker_values: dict[str, str] = {}

    def setnx(self, key: str, value: str) -> bool:
        if key in self.markers:
            return False
        self.markers.add(key)
        self.marker_values[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.marker_values.get(key)

    def expire(self, key: str, seconds: int) -> bool:
        return True

    def exists(self, key: str) -> int:
        return int(key in self.markers)

    def delete(self, key: str) -> int:
        self.markers.discard(key)
        self.marker_values.pop(key, None)
        return super().delete(key)


class EvalIdempotentFakeRedis(FakeRedis):
    """Model the atomic Lua publish branch, including receipt rollback."""

    def __init__(self) -> None:
        super().__init__()
        self.markers: set[str] = set()
        self.marker_values: dict[str, str] = {}
        self.fail_lpush = False
        self.eval_calls = 0

    def lpush(self, key: str, value: str) -> int:
        if self.fail_lpush and key == "queue":
            raise RuntimeError("redis list unavailable")
        return super().lpush(key, value)

    def eval(self, script: str, numkeys: int, *args: str) -> int:
        self.eval_calls += 1
        if numkeys == 3:
            (
                queue_name,
                processing_name,
                marker_key,
                raw,
                message_needle,
                task_needle,
                kind_needle,
                attempt_needle,
                max_attempts_needle,
                receipt_value,
                ttl,
            ) = args
            for value in self.queued + self.processing:
                if message_needle not in value:
                    continue
                if all(
                    needle in value
                    for needle in (
                        task_needle,
                        kind_needle,
                        attempt_needle,
                        max_attempts_needle,
                    )
                ):
                    self.markers.add(marker_key)
                    self.marker_values[marker_key] = receipt_value
                    return 0
                return -1
            self.lpush(queue_name, raw)
            self.markers.add(marker_key)
            self.marker_values[marker_key] = receipt_value
            return 1
        queue_name, marker_key, raw, ttl, receipt_value = args
        if marker_key in self.markers:
            return 0
        self.markers.add(marker_key)
        self.marker_values[marker_key] = receipt_value
        try:
            return self.lpush(queue_name, raw)
        except Exception:
            self.markers.discard(marker_key)
            self.marker_values.pop(marker_key, None)
            raise

    def exists(self, key: str) -> int:
        return int(key in self.markers)

    def get(self, key: str) -> str | None:
        return self.marker_values.get(key)


def test_redis_task_queue_claims_and_acknowledges_envelopes(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake)
    queue = RedisTaskQueue(
        "redis://test",
        queue_name="queue",
        processing_name="processing",
    )

    envelope = queue.enqueue("history:1", "history_download", {"queries": []})
    assert queue.size() == {"queued": 1, "processing": 0}
    claimed = queue.claim(timeout_seconds=1, worker_id="worker-1", lease_seconds=60)
    assert claimed is not None
    assert claimed["task_id"] == envelope["task_id"]
    assert queue.size() == {"queued": 0, "processing": 1}
    claim_key = next(iter(fake.claims))
    assert fake.expirations[claim_key] == 60
    queue.heartbeat("worker-1")
    queue.renew(claimed, lease_seconds=90)
    assert fake.expirations[claim_key] == 90
    queue.ack(claimed)
    assert queue.size() == {"queued": 0, "processing": 0}
    assert claim_key not in fake.claims
    assert queue.status()["ok"] is True


def test_redis_task_queue_publishes_worker_lifecycle_state(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake)
    queue = RedisTaskQueue("redis://test", queue_name="queue", processing_name="processing")

    heartbeat = queue.heartbeat(
        "worker-draining",
        state="draining",
        active_task_id="research:draining",
    )

    assert heartbeat["state"] == "draining"
    stored = json.loads(fake.heartbeats["crypto:worker:heartbeat:worker-draining"])
    assert stored["state"] == "draining"
    assert stored["active_task_id"] == "research:draining"
    assert queue.worker_status("worker-draining") == stored


def test_redis_task_queue_moves_failed_envelope_to_bounded_metadata_dlq(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake)
    queue = RedisTaskQueue("redis://test", queue_name="queue", processing_name="processing")

    queue.enqueue("research:1", "research", {"mode": "compare"}, max_attempts=2)
    claimed = queue.claim(timeout_seconds=1, worker_id="worker-1", lease_seconds=60)
    assert claimed is not None
    record = queue.dead_letter(
        claimed,
        error={"kind": "WORKER_FAILED", "message": "test failure"},
    )
    repeated = queue.dead_letter(
        claimed,
        error={"kind": "WORKER_FAILED", "message": "retried settlement"},
    )

    assert record["task_id"] == "research:1"
    assert repeated["dead_letter_id"] == record["dead_letter_id"]
    assert queue.size() == {"queued": 0, "processing": 0}
    assert len(queue.dead_letters()) == 1
    assert queue.dead_letters()[0]["error"] == {"kind": "WORKER_FAILED", "message": "test failure"}


def test_redis_task_queue_reclaims_expired_claim_and_returns_task_metadata(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake)
    queue = RedisTaskQueue("redis://test", queue_name="queue", processing_name="processing")

    queue.enqueue("research:2", "research", {"mode": "compare"}, max_attempts=3)
    claimed = queue.claim(timeout_seconds=1, worker_id="worker-1", lease_seconds=60)
    assert claimed is not None
    claim_key = next(iter(fake.claims))
    fake.claims[claim_key]["claimed_at"] = (datetime.now(UTC) - timedelta(seconds=31)).isoformat()

    recovered = queue.reclaim_expired(lease_seconds=30)

    assert recovered == [{"task_id": "research:2", "message_id": claim_key.split(":")[-1], "attempt": 1}]
    assert queue.size() == {"queued": 1, "processing": 0}


def test_redis_task_queue_finds_and_releases_claim_after_worker_heartbeat_loss(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake)
    queue = RedisTaskQueue("redis://test", queue_name="queue", processing_name="processing")

    queue.heartbeat("worker-lost")
    queue.enqueue("research:lost-worker", "research", {"mode": "compare"})
    claimed = queue.claim(timeout_seconds=1, worker_id="worker-lost", lease_seconds=900)
    assert claimed is not None
    claim_key = next(iter(fake.claims))
    fake.claims[claim_key]["claimed_at"] = (datetime.now(UTC) - timedelta(seconds=31)).isoformat()

    assert queue.stale_claims(grace_seconds=30, exclude_worker_id="worker-new") == []
    fake.heartbeats.pop("crypto:worker:heartbeat:worker-lost")

    stale = queue.stale_claims(grace_seconds=30, exclude_worker_id="worker-new")

    assert len(stale) == 1
    assert stale[0]["task_id"] == "research:lost-worker"
    assert stale[0]["worker_id"] == "worker-lost"
    assert "payload" not in stale[0]
    queue.release_claim(stale[0])
    assert queue.size() == {"queued": 0, "processing": 0}


def test_redis_task_queue_reclaims_expired_processing_marker_without_claim_hash(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake)
    queue = RedisTaskQueue("redis://test", queue_name="queue", processing_name="processing")

    queue.enqueue("research:orphan-claim", "research", {"mode": "compare"})
    claimed = queue.claim(timeout_seconds=1, worker_id="worker-1", lease_seconds=60)
    assert claimed is not None
    raw = fake.processing[0]
    marker = json.loads(raw)
    marker["_claim"]["claimed_at"] = (datetime.now(UTC) - timedelta(seconds=31)).isoformat()
    fake.processing[0] = json.dumps(marker, separators=(",", ":"))
    fake.claims.clear()

    recovered = queue.reclaim_expired(lease_seconds=30)

    assert recovered == [{"task_id": "research:orphan-claim", "message_id": claimed["message_id"], "attempt": 1}]
    assert queue.size() == {"queued": 1, "processing": 0}


def test_redis_task_queue_reclaims_legacy_processing_envelope_without_claim_metadata(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake)
    queue = RedisTaskQueue("redis://test", queue_name="queue", processing_name="processing")

    queue.enqueue("research:legacy-processing", "research", {"mode": "compare"})
    claimed = queue.claim(timeout_seconds=1, worker_id="worker-1", lease_seconds=60)
    assert claimed is not None
    legacy = json.loads(fake.processing[0])
    legacy.pop("_claim", None)
    legacy["enqueued_at"] = (datetime.now(UTC) - timedelta(seconds=31)).isoformat()
    fake.processing[0] = json.dumps(legacy, separators=(",", ":"))
    fake.claims.clear()

    recovered = queue.reclaim_expired(lease_seconds=30)

    assert recovered == [{
        "task_id": "research:legacy-processing",
        "message_id": claimed["message_id"],
        "attempt": 1,
    }]
    assert queue.size() == {"queued": 1, "processing": 0}


def test_redis_task_queue_renew_refreshes_the_processing_recovery_marker(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake)
    queue = RedisTaskQueue("redis://test", queue_name="queue", processing_name="processing")

    queue.enqueue("research:renew-claim", "research", {"mode": "compare"})
    claimed = queue.claim(timeout_seconds=1, worker_id="worker-1", lease_seconds=60)
    assert claimed is not None
    before = json.loads(fake.processing[0])["_claim"]["claimed_at"]

    queue.renew(claimed, lease_seconds=60)

    after = json.loads(fake.processing[0])["_claim"]["claimed_at"]
    assert after != before
    assert claimed["_raw"] == fake.processing[0]


def test_redis_task_queue_replays_dead_letter_with_bounded_reference(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake)
    queue = RedisTaskQueue("redis://test", queue_name="queue", processing_name="processing")

    queue.enqueue("research:dead-1", "research", {"mode": "compare"}, max_attempts=2)
    claimed = queue.claim(timeout_seconds=1, worker_id="worker-1", lease_seconds=60)
    assert claimed is not None
    queue.dead_letter(claimed, error={"kind": "WORKER_FAILED", "message": "permanent"})

    result = queue.replay_dead_letter(
        "research:dead-1",
        "research",
        {"mode": "compare", "run_id": "replay-1"},
        replay_task_id="research:replay-1",
        request_id="replay-request-1",
        max_attempts=2,
    )

    assert result["envelope"]["task_id"] == "research:replay-1"
    assert queue.size() == {"queued": 1, "processing": 0}
    record = queue.dead_letters()[0]
    assert record["replay_request_id"] == "replay-request-1"
    assert record["replay_task_id"] == "research:replay-1"
    assert "payload" not in record

    duplicate = queue.replay_dead_letter(
        "research:dead-1",
        "research",
        {"mode": "compare", "run_id": "replay-1"},
        replay_task_id="research:replay-1",
        request_id="replay-request-1",
        max_attempts=2,
    )
    assert duplicate["deduplicated"] is True
    assert queue.size() == {"queued": 1, "processing": 0}


def test_redis_task_queue_repairs_a_deduplicated_replay_when_its_list_item_is_lost(monkeypatch) -> None:
    fake = IdempotentFakeRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake)
    queue = RedisTaskQueue("redis://test", queue_name="queue", processing_name="processing")

    queue.enqueue(
        "research:dead-repair",
        "research",
        {"mode": "compare"},
        message_id="source-dead-repair",
    )
    claimed = queue.claim(timeout_seconds=1, worker_id="worker-1", lease_seconds=60)
    assert claimed is not None
    queue.dead_letter(claimed, error={"kind": "WORKER_FAILED", "message": "permanent"})

    first = queue.replay_dead_letter(
        "research:dead-repair",
        "research",
        {"mode": "compare", "run_id": "replay-repair"},
        replay_task_id="research:replay-repair",
        request_id="replay-request-repair",
        max_attempts=2,
        message_id="replay-message-repair",
    )
    assert first["deduplicated"] is False
    assert queue.size() == {"queued": 1, "processing": 0}

    fake.queued.clear()
    repeated = queue.replay_dead_letter(
        "research:dead-repair",
        "research",
        {"mode": "compare", "run_id": "replay-repair"},
        replay_task_id="research:replay-repair",
        request_id="replay-request-repair",
        max_attempts=2,
        message_id="replay-message-repair",
    )

    assert repeated["deduplicated"] is True
    assert repeated["envelope"]["message_id"] == "replay-message-repair"
    assert queue.size() == {"queued": 1, "processing": 0}


def test_redis_task_queue_retries_a_fixed_message_id_without_duplicate_publish(monkeypatch) -> None:
    fake = IdempotentFakeRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake)
    queue = RedisTaskQueue("redis://test", queue_name="queue", processing_name="processing")

    first = queue.enqueue(
        "research:outbox-1",
        "research",
        {"mode": "compare"},
        message_id="outbox-message-1",
    )
    repeated = queue.enqueue(
        "research:outbox-1",
        "research",
        {"mode": "compare"},
        message_id="outbox-message-1",
    )

    assert first["message_id"] == repeated["message_id"] == "outbox-message-1"
    assert queue.size() == {"queued": 1, "processing": 0}


def test_redis_task_queue_persists_a_payload_free_receipt_contract(monkeypatch) -> None:
    fake = IdempotentFakeRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake)
    queue = RedisTaskQueue("redis://test", queue_name="queue", processing_name="processing")

    envelope = queue.enqueue(
        "research:receipt-contract",
        "research",
        {"secret_payload": "must-not-escape"},
        message_id="receipt-contract-message",
    )
    receipt = queue.get_delivery_receipt(
        str(envelope["message_id"]),
        task_id="research:receipt-contract",
        kind="research",
        attempt=1,
        max_attempts=3,
    )

    assert receipt["state"] == "REDIS_ACCEPTED"
    assert receipt["receipt_contract"] == RECEIPT_CONTRACT_VERSION
    assert receipt["receipt_sha256"] == receipt["sha256"]
    marker = json.loads(fake.marker_values["crypto:control_tasks:published:v1:receipt-contract-message"])
    assert marker["contract_version"] == RECEIPT_CONTRACT_VERSION
    assert marker["message_id"] == "receipt-contract-message"
    assert "payload" not in marker
    assert "secret_payload" not in json.dumps(marker, ensure_ascii=False)


def test_redis_task_queue_rejects_a_tampered_receipt_contract(monkeypatch) -> None:
    fake = IdempotentFakeRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake)
    queue = RedisTaskQueue("redis://test", queue_name="queue", processing_name="processing")
    queue.enqueue(
        "research:receipt-tamper",
        "research",
        {"mode": "compare"},
        message_id="receipt-tamper-message",
    )
    marker_key = "crypto:control_tasks:published:v1:receipt-tamper-message"
    marker = json.loads(fake.marker_values[marker_key])
    marker["task_id"] = "research:other-task"
    fake.marker_values[marker_key] = json.dumps(marker, separators=(",", ":"))

    receipt = queue.get_delivery_receipt(
        "receipt-tamper-message",
        task_id="research:receipt-tamper",
        kind="research",
        attempt=1,
        max_attempts=3,
    )

    assert receipt["state"] == "REDIS_MISMATCHED"
    assert receipt["verified"] is False


def test_redis_task_queue_lua_publish_clears_receipt_when_list_write_fails(monkeypatch) -> None:
    fake = EvalIdempotentFakeRedis()
    fake.fail_lpush = True
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake)
    queue = RedisTaskQueue("redis://test", queue_name="queue", processing_name="processing")

    with pytest.raises(TaskQueueError, match="unable to enqueue task"):
        queue.enqueue(
            "research:lua-rollback",
            "research",
            {"mode": "compare"},
            message_id="lua-rollback-message",
        )

    assert fake.markers == set()
    assert queue.size() == {"queued": 0, "processing": 0}

    fake.fail_lpush = False
    queue.enqueue(
        "research:lua-rollback",
        "research",
        {"mode": "compare"},
        message_id="lua-rollback-message",
    )
    queue.enqueue(
        "research:lua-rollback",
        "research",
        {"mode": "compare"},
        message_id="lua-rollback-message",
    )
    assert fake.eval_calls == 3
    assert queue.size() == {"queued": 1, "processing": 0}


def test_redis_task_queue_repairs_missing_list_item_even_when_receipt_remains(monkeypatch) -> None:
    fake = EvalIdempotentFakeRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake)
    queue = RedisTaskQueue("redis://test", queue_name="queue", processing_name="processing")

    arguments = {
        "task_id": "research:repair",
        "kind": "research",
        "payload": {"mode": "compare"},
        "attempt": 1,
        "max_attempts": 3,
        "message_id": "repair-message",
    }
    queue.enqueue(**arguments)
    fake.queued.clear()

    queue.ensure_enqueued(**arguments)
    queue.ensure_enqueued(**arguments)

    assert queue.size() == {"queued": 1, "processing": 0}
    assert fake.markers == {"crypto:control_tasks:published:v1:repair-message"}


def test_redis_task_queue_rejects_a_fixed_id_with_a_conflicting_envelope(monkeypatch) -> None:
    fake = EvalIdempotentFakeRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake)
    queue = RedisTaskQueue("redis://test", queue_name="queue", processing_name="processing")

    queue.enqueue(
        "research:original",
        "research",
        {"mode": "compare"},
        message_id="conflicting-message",
    )

    with pytest.raises(TaskQueueError, match="conflicts with an existing envelope"):
        queue.ensure_enqueued(
            "research:other",
            "research",
            {"mode": "compare"},
            message_id="conflicting-message",
        )

    assert queue.size() == {"queued": 1, "processing": 0}
