import json

from backend.app.services.task_dispatch_consistency import TaskDispatchConsistency
from backend.app.services.task_queue import PUBLISHED_PREFIX, RedisTaskQueue
from backend.app.services.task_store import TaskStore


class ConsistencyRedis:
    def __init__(self) -> None:
        self.lists = {"queue": [], "processing": [], "dead": []}
        self.markers: set[str] = set()
        self.marker_values: dict[str, str] = {}

    def lpush(self, key: str, value: str) -> int:
        target = self.lists["dead"] if key == "dead" else self.lists[key]
        target.insert(0, value)
        return len(target)

    def lrange(self, key: str, start: int, stop: int) -> list[str]:
        values = self.lists["dead"] if key == "dead" else self.lists[key]
        end = None if stop == -1 else stop + 1
        return values[start:end]

    def llen(self, key: str) -> int:
        return len(self.lists["dead"] if key == "dead" else self.lists[key])

    def setnx(self, key: str, value: str) -> bool:
        if key in self.markers:
            return False
        self.markers.add(key)
        self.marker_values[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.marker_values.get(key) if key in self.markers else None

    def expire(self, key: str, seconds: int) -> bool:
        return True

    def exists(self, key: str) -> int:
        return int(key in self.markers)

    def ping(self) -> bool:
        return True


def _queue(monkeypatch, redis: ConsistencyRedis) -> RedisTaskQueue:
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: redis)
    return RedisTaskQueue(
        "redis://consistency-test",
        queue_name="queue",
        processing_name="processing",
        dead_letter_name="dead",
    )


def _create(store: TaskStore, task_id: str) -> None:
    store.create(
        task_id,
        kind="research",
        title="一致性测试",
        payload={"secret_payload": "must-not-escape"},
        stage_dispatch=True,
    )


def test_consistency_repairs_pending_outbox_without_returning_payload(monkeypatch) -> None:
    redis = ConsistencyRedis()
    queue = _queue(monkeypatch, redis)
    store = TaskStore("sqlite:///:memory:")
    try:
        task_id = "research:consistency-pending"
        _create(store, task_id)

        report = TaskDispatchConsistency(store, queue).audit_and_repair(limit=10)

        assert report["status"] == "ready"
        assert report["counts"]["QUEUED"] == 1
        assert report["repair"]["repaired"] == 1
        assert store.pending_dispatches(task_id=task_id) == []
        assert "must-not-escape" not in json.dumps(report, ensure_ascii=False)
    finally:
        store.close()


def test_consistency_audits_and_settles_terminal_pending_outbox(monkeypatch) -> None:
    redis = ConsistencyRedis()
    queue = _queue(monkeypatch, redis)
    store = TaskStore("sqlite:///:memory:")
    try:
        task_id = "research:consistency-terminal-pending"
        _create(store, task_id)
        finished = store.sync(task_id, status="completed", result={"status": "completed"})
        assert finished is not None

        before = TaskDispatchConsistency(store, queue).audit(limit=10)
        assert before["counts"]["PENDING"] == 1
        assert before["status"] == "degraded"

        report = TaskDispatchConsistency(store, queue).audit_and_repair(limit=10)
        item = report["items"][0]

        assert report["status"] == "ready"
        assert report["counts"]["SETTLED"] == 1
        assert item["pre_state"] == "PENDING"
        assert item["state"] == "SETTLED"
        assert item["repair"]["status"] == "repaired"
        assert store.pending_dispatches(task_id=task_id) == []
    finally:
        store.close()


def test_consistency_repairs_published_receipt_when_queue_item_is_missing(monkeypatch) -> None:
    redis = ConsistencyRedis()
    queue = _queue(monkeypatch, redis)
    store = TaskStore("sqlite:///:memory:")
    try:
        task_id = "research:consistency-receipt-split"
        _create(store, task_id)
        store.publish_pending_dispatches(queue)
        redis.lists["queue"].clear()

        report = TaskDispatchConsistency(store, queue).audit_and_repair(limit=10)
        item = report["items"][0]

        assert report["counts"]["QUEUED"] == 1
        assert item["pre_state"] == "MISSING"
        assert item["repair"]["action"] == "ensure_enqueued"
        assert item["repair"]["status"] == "repaired"
        assert redis.llen("queue") == 1
        assert report["receipt_counts"]["VERIFIED"] == 1
    finally:
        store.close()


def test_consistency_refreshes_missing_receipt_without_duplicating_queue_item(monkeypatch) -> None:
    redis = ConsistencyRedis()
    queue = _queue(monkeypatch, redis)
    store = TaskStore("sqlite:///:memory:")
    try:
        task_id = "research:consistency-receipt-marker"
        _create(store, task_id)
        store.publish_pending_dispatches(queue)
        dispatch = store.dispatch_consistency_candidates(limit=10)[0]["dispatch"]
        assert isinstance(dispatch, dict)
        marker_key = f"{PUBLISHED_PREFIX}{dispatch['message_id']}"
        redis.markers.remove(marker_key)
        redis.marker_values.pop(marker_key, None)

        before = TaskDispatchConsistency(store, queue).audit(limit=10)
        assert before["status"] == "degraded"
        assert before["receipt_counts"]["UNVERIFIED"] == 1
        assert before["items"][0]["redis_receipt_state"] == "REDIS_RECEIPT_MISSING"

        report = TaskDispatchConsistency(store, queue).audit_and_repair(limit=10)
        item = report["items"][0]

        assert report["status"] == "ready"
        assert item["pre_state"] == "QUEUED"
        assert item["repair"]["action"] == "refresh_delivery_receipt"
        assert item["repair"]["status"] == "repaired"
        assert item["redis_receipt_state"] == "REDIS_ACCEPTED"
        assert report["receipt_counts"]["VERIFIED"] == 1
        assert redis.llen("queue") == 1
        assert f"{PUBLISHED_PREFIX}{dispatch['message_id']}" in redis.markers
    finally:
        store.close()


def test_consistency_fails_closed_on_a_tampered_receipt_contract(monkeypatch) -> None:
    redis = ConsistencyRedis()
    queue = _queue(monkeypatch, redis)
    store = TaskStore("sqlite:///:memory:")
    try:
        task_id = "research:consistency-receipt-mismatch"
        _create(store, task_id)
        store.publish_pending_dispatches(queue)
        dispatch = store.dispatch_consistency_candidates(limit=10)[0]["dispatch"]
        assert isinstance(dispatch, dict)
        marker_key = f"{PUBLISHED_PREFIX}{dispatch['message_id']}"
        marker = json.loads(redis.marker_values[marker_key])
        marker["task_id"] = "research:other-task"
        redis.marker_values[marker_key] = json.dumps(marker, separators=(",", ":"))

        report = TaskDispatchConsistency(store, queue).audit_and_repair(limit=10)
        item = report["items"][0]

        assert report["status"] == "degraded"
        assert report["counts"]["MISMATCHED"] == 1
        assert item["redis_receipt_state"] == "REDIS_MISMATCHED"
        assert item["repair"]["status"] == "skipped_unsafe_state"
        assert redis.llen("queue") == 1
    finally:
        store.close()


def test_consistency_does_not_reactivate_dead_letter_or_duplicate_delivery(monkeypatch) -> None:
    redis = ConsistencyRedis()
    queue = _queue(monkeypatch, redis)
    store = TaskStore("sqlite:///:memory:")
    try:
        dead_task = "research:consistency-dead"
        _create(store, dead_task)
        store.publish_pending_dispatches(queue)
        redis.lists["dead"].append(redis.lists["queue"].pop())

        duplicate_task = "research:consistency-duplicate"
        _create(store, duplicate_task)
        store.publish_pending_dispatches(queue)
        redis.lists["queue"].append(redis.lists["queue"][0])

        report = TaskDispatchConsistency(store, queue).audit_and_repair(limit=10)
        by_task = {str(item["task_id"]): item for item in report["items"]}

        assert by_task[dead_task]["state"] == "DLQ"
        assert by_task[dead_task]["repair"]["status"] == "skipped_unsafe_state"
        assert by_task[duplicate_task]["state"] == "DUPLICATE"
        assert by_task[duplicate_task]["repair"]["status"] == "skipped_unsafe_state"
        assert redis.llen("dead") == 1
        assert redis.llen("queue") == 2
    finally:
        store.close()


def test_consistency_does_not_repair_a_mismatched_message_id(monkeypatch) -> None:
    redis = ConsistencyRedis()
    queue = _queue(monkeypatch, redis)
    store = TaskStore("sqlite:///:memory:")
    try:
        task_id = "research:consistency-mismatch"
        _create(store, task_id)
        dispatch = store.pending_dispatches(task_id=task_id)[0]
        redis.lpush(
            "queue",
            json.dumps(
                {
                    "message_id": "wrong-message-id",
                    "task_id": task_id,
                    "kind": "research",
                    "payload": {"secret_payload": "must-not-escape"},
                },
                separators=(",", ":"),
            ),
        )
        store.mark_dispatch_published(str(dispatch["message_id"]))

        report = TaskDispatchConsistency(store, queue).audit_and_repair(limit=10)

        item = report["items"][0]
        assert item["state"] == "MISMATCHED"
        assert item["repair"]["status"] == "skipped_unsafe_state"
        assert redis.llen("queue") == 1
    finally:
        store.close()


def test_consistency_detects_a_stale_attempt_in_a_surviving_redis_delivery(monkeypatch) -> None:
    redis = ConsistencyRedis()
    queue = _queue(monkeypatch, redis)
    store = TaskStore("sqlite:///:memory:")
    try:
        task_id = "research:consistency-stale-attempt"
        _create(store, task_id)
        dispatch = store.pending_dispatches(task_id=task_id)[0]
        store.publish_pending_dispatches(queue)

        with store._engine.begin() as connection:
            connection.execute(
                store._task_dispatch_outbox.update()
                .where(
                    store._task_dispatch_outbox.c.message_id
                    == str(dispatch["message_id"])
                )
                .values(attempt=2)
            )

        report = TaskDispatchConsistency(store, queue).audit_and_repair(limit=10)

        item = report["items"][0]
        assert item["state"] == "MISMATCHED"
        assert item["reason"] == "redis_envelope_does_not_match_fixed_delivery"
        assert item["repair"]["status"] == "skipped_unsafe_state"
        assert redis.llen("queue") == 1
        assert store.pending_dispatches(task_id=task_id) == []
    finally:
        store.close()


def test_redis_delivery_receipt_is_bounded_and_hashes_the_envelope(monkeypatch) -> None:
    redis = ConsistencyRedis()
    queue = _queue(monkeypatch, redis)
    envelope = queue.enqueue(
        "research:receipt-contract",
        "research",
        {"secret_payload": "must-not-escape"},
        attempt=2,
        max_attempts=3,
        message_id="receipt-contract-message",
    )

    receipt = queue.get_delivery_receipt(
        str(envelope["message_id"]),
        task_id="research:receipt-contract",
        kind="research",
        attempt=2,
        max_attempts=3,
    )

    assert receipt["state"] == "REDIS_ACCEPTED"
    assert receipt["verified"] is True
    assert receipt["receipt_key"].endswith("receipt-contract-message")
    assert len(str(receipt["sha256"])) == 64
    assert "secret_payload" not in json.dumps(receipt, ensure_ascii=False)
