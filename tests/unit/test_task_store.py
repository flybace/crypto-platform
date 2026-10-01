from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest
from sqlalchemy import event, inspect, text

from backend.app.services.task_store import (
    ACTIVE_IDEMPOTENCY_INDEX,
    PENDING_DISPATCH_INDEX,
    TaskStore,
)


class QueuePublisher:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []
        self.fail = False

    def enqueue(self, task_id, kind, payload, *, attempt, max_attempts, message_id):
        if self.fail:
            raise RuntimeError("redis unavailable")
        envelope = {
            "message_id": message_id,
            "task_id": task_id,
            "kind": kind,
            "payload": payload,
            "attempt": attempt,
            "max_attempts": max_attempts,
        }
        self.items.append(envelope)
        return envelope


class IdempotentQueuePublisher(QueuePublisher):
    def __init__(self) -> None:
        super().__init__()
        self.message_ids: set[str] = set()
        self.ensure_calls = 0

    def enqueue(self, task_id, kind, payload, *, attempt, max_attempts, message_id):
        envelope = {
            "message_id": message_id,
            "task_id": task_id,
            "kind": kind,
            "payload": payload,
            "attempt": attempt,
            "max_attempts": max_attempts,
        }
        if message_id not in self.message_ids:
            self.message_ids.add(message_id)
            self.items.append(envelope)
        return envelope

    def ensure_enqueued(self, task_id, kind, payload, *, attempt, max_attempts, message_id):
        self.ensure_calls += 1
        envelope = {
            "message_id": message_id,
            "task_id": task_id,
            "kind": kind,
            "payload": payload,
            "attempt": attempt,
            "max_attempts": max_attempts,
        }
        if message_id not in {str(item["message_id"]) for item in self.items}:
            self.message_ids.add(message_id)
            self.items.append(envelope)
        return envelope


class ReceiptQueuePublisher(IdempotentQueuePublisher):
    def get_delivery_receipt(
        self,
        message_id,
        *,
        task_id,
        kind,
        attempt,
        max_attempts,
    ):
        return {
            "state": "REDIS_ACCEPTED",
            "verified": True,
            "receipt_key": f"crypto:control_tasks:published:v1:{message_id}",
            "sha256": "a" * 64,
            "observed_at": "2026-09-18T00:00:00+00:00",
        }


class ReceiptOnlyQueuePublisher(QueuePublisher):
    def get_delivery_receipt(
        self,
        message_id,
        *,
        task_id,
        kind,
        attempt,
        max_attempts,
    ):
        return {
            "state": "REDIS_RECEIPT_ONLY",
            "verified": False,
            "receipt_key": f"crypto:control_tasks:published:v1:{message_id}",
            "sha256": None,
            "observed_at": "2026-09-18T00:00:00+00:00",
        }


class ReceiptHashMismatchPublisher(QueuePublisher):
    def get_delivery_receipt(
        self,
        message_id,
        *,
        task_id,
        kind,
        attempt,
        max_attempts,
    ):
        return {
            "state": "REDIS_ACCEPTED",
            "verified": True,
            "receipt_key": f"crypto:control_tasks:published:v1:{message_id}",
            "sha256": "a" * 64,
            "receipt_sha256": "b" * 64,
            "receipt_contract": "task-dispatch-receipt-v1",
            "observed_at": "2026-09-18T00:00:00+00:00",
        }


class ExistingDeliveryPublisher(QueuePublisher):
    def __init__(self, *, location: str = "queued") -> None:
        super().__init__()
        self.location = location
        self.inspect_calls = 0
        self.enqueue_calls = 0

    def enqueue(self, task_id, kind, payload, *, attempt, max_attempts, message_id):
        self.enqueue_calls += 1
        raise AssertionError("an existing fixed delivery must not be published again")

    def inspect_delivery(self, task_id, message_id, *, kind, attempt, max_attempts, limit):
        self.inspect_calls += 1
        return {
            "available": True,
            "exact_count": 1,
            "mismatched_count": 0,
            "matched_by_location": {
                "queued": int(self.location == "queued"),
                "processing": int(self.location == "processing"),
                "dead_letter": 0,
            },
        }


from backend.app.services.task_log_archive import TaskLogArchive, TaskLogArchiveError


def test_task_store_is_idempotent_and_keeps_lifecycle_events() -> None:
    store = TaskStore("sqlite:///:memory:")
    try:
        created = store.create(
            "history:job-1",
            kind="history_download",
            title="历史数据下载",
            payload={"symbol": "BTC/USDT"},
            idempotency_key="history-request-1",
        )
        duplicate = store.create(
            "history:job-2",
            kind="history_download",
            title="历史数据下载",
            payload={"symbol": "BTC/USDT"},
            idempotency_key="history-request-1",
        )

        assert created["task_id"] == "history:job-1"
        assert duplicate["task_id"] == "history:job-1"
        assert duplicate["deduplicated"] is True

        store.sync(
            "history:job-1",
            status="running",
            progress={"completed": 1, "total": 2, "percent": 50},
        )
        store.append_event(
            "history:job-1",
            "progress",
            status="running",
            message="已完成一项",
            payload={"completed": 1},
        )
        finished = store.sync(
            "history:job-1",
            status="completed",
            result={"rows": 2},
        )

        assert finished is not None
        assert finished["status"] == "completed"
        assert finished["progress"] == {"completed": 1, "total": 2, "percent": 50}
        assert finished["started_at"]
        assert finished["finished_at"]
        assert finished["version"] == 3
        assert [event["event_type"] for event in store.events("history:job-1")] == ["progress"]
        metadata = store.sync_history_datasets(
            [
                {
                    "storage_key": "binance/spot/BTCUSDT/1d.csv",
                    "dataset_id": "dataset-1",
                    "venue_id": "binance",
                    "market_type": "spot",
                    "instrument_key": "binance:spot:BTC/USDT",
                    "data_level": "KLINE",
                    "interval": "1d",
                    "start_at": "2026-01-01T00:00:00+00:00",
                    "end_at": "2026-01-02T00:00:00+00:00",
                    "file_format": "csv",
                    "source": "fake-public",
                    "row_count": 1,
                    "gap_count": 0,
                    "duplicate_count": 0,
                    "content_sha256": "a" * 64,
                }
            ]
        )
        assert metadata["dataset_count"] == 1
        assert store.history_metadata_status()["dataset_count"] == 1
        store.create("history:job-lease", kind="history_download", title="历史数据下载")
        claimed = store.mark_claimed(
            "history:job-lease",
            worker_id="worker-1",
            attempt=1,
            lease_seconds=60,
        )
        assert claimed is not None
        assert claimed["worker_id"] == "worker-1"
        recovered = store.recover_after_lease(
            "history:job-lease",
            attempt=2,
            message_id="message-1",
        )
        assert recovered is not None
        assert recovered["status"] == "queued"
        assert recovered["error"]["kind"] == "WORKER_LEASE_EXPIRED"
        dead = store.dead_letter(
            "history:job-lease",
            error={"kind": "WORKER_FAILED", "message": "permanent"},
            attempt=2,
        )
        assert dead is not None
        assert dead["status"] == "dead_lettered"
        assert store.dead_letters()[0]["task_id"] == "history:job-lease"
        assert store.status()["ok"] is True
    finally:
        store.close()


def test_task_store_commits_dispatch_intent_and_retries_publication() -> None:
    store = TaskStore("sqlite:///:memory:")
    publisher = QueuePublisher()
    try:
        created = store.create(
            "research:outbox-1",
            kind="research",
            title="研究任务",
            payload={"ledger": "payload"},
            stage_dispatch=True,
            dispatch_payload={"worker": "payload"},
            max_attempts=2,
        )
        assert created["status"] == "queued"
        pending = store.pending_dispatches(task_id="research:outbox-1")
        assert len(pending) == 1
        assert pending[0]["payload"] == {"worker": "payload"}
        assert pending[0]["max_attempts"] == 2

        publisher.fail = True
        failed = store.publish_pending_dispatches(publisher, task_id="research:outbox-1")
        assert len(failed["failed"]) == 1
        assert len(store.pending_dispatches(task_id="research:outbox-1")) == 1

        publisher.fail = False
        published = store.publish_pending_dispatches(publisher, task_id="research:outbox-1")
        assert len(published["published"]) == 1
        assert len(store.pending_dispatches(task_id="research:outbox-1")) == 0
        assert publisher.items[0]["message_id"] == pending[0]["message_id"]
    finally:
        store.close()


def test_task_store_persists_verified_delivery_receipt_without_payload() -> None:
    store = TaskStore("sqlite:///:memory:")
    publisher = ReceiptQueuePublisher()
    try:
        task_id = "research:durable-receipt"
        store.create(
            task_id,
            kind="research",
            title="持久投递回执",
            payload={"secret_payload": "must-not-escape"},
            stage_dispatch=True,
        )

        result = store.publish_pending_dispatches(publisher, task_id=task_id)

        assert len(result["published"]) == 1
        dispatch = store.dispatch_consistency_candidates(limit=10)[0]["dispatch"]
        assert dispatch["receipt"]["state"] == "REDIS_ACCEPTED"
        assert dispatch["receipt"]["sha256"] == "a" * 64
        assert dispatch["receipt"]["receipt_key"].endswith(str(dispatch["message_id"]))
        assert "secret_payload" not in json.dumps(dispatch["receipt"], ensure_ascii=False)
    finally:
        store.close()


def test_task_store_does_not_commit_sql_publish_for_receipt_only() -> None:
    store = TaskStore("sqlite:///:memory:")
    publisher = ReceiptOnlyQueuePublisher()
    try:
        task_id = "research:receipt-only"
        store.create(
            task_id,
            kind="research",
            title="只有 Redis 回执 marker",
            payload={"run_id": task_id},
            stage_dispatch=True,
        )

        result = store.publish_pending_dispatches(publisher, task_id=task_id)

        assert result["published"] == []
        assert len(result["failed"]) == 1
        assert store.pending_dispatches(task_id=task_id)
        assert result["failed"][0]["error"]["message"] == (
            "Redis delivery receipt was not fully accepted"
        )
    finally:
        store.close()


def test_task_store_rejects_a_receipt_hash_that_does_not_match_the_envelope() -> None:
    store = TaskStore("sqlite:///:memory:")
    publisher = ReceiptHashMismatchPublisher()
    try:
        task_id = "research:receipt-hash-mismatch"
        store.create(
            task_id,
            kind="research",
            title="回执摘要冲突",
            payload={"run_id": task_id},
            stage_dispatch=True,
        )

        result = store.publish_pending_dispatches(publisher, task_id=task_id)

        assert result["published"] == []
        assert result["failed"][0]["error"]["message"] == (
            "Redis delivery receipt hash does not match the envelope"
        )
        assert store.pending_dispatches(task_id=task_id)
    finally:
        store.close()


def test_task_store_retries_after_sql_receipt_failure_without_duplicate_publish() -> None:
    store = TaskStore("sqlite:///:memory:")
    publisher = IdempotentQueuePublisher()
    try:
        store.create(
            "research:receipt-uncertain",
            kind="research",
            title="回执不确定",
            payload={"run_id": "receipt-uncertain"},
            stage_dispatch=True,
        )
        original_mark = store.mark_dispatch_published
        failed_once = True

        def mark_with_one_failure(message_id: str) -> bool:
            nonlocal failed_once
            if failed_once:
                failed_once = False
                raise RuntimeError("sql receipt unavailable")
            return original_mark(message_id)

        store.mark_dispatch_published = mark_with_one_failure
        first = store.publish_pending_dispatches(publisher)
        assert first["published"] == []
        assert len(first["failed"]) == 1
        assert len(publisher.items) == 1
        assert len(store.pending_dispatches()) == 1

        store.mark_dispatch_published = original_mark
        second = store.publish_pending_dispatches(publisher)
        assert len(second["published"]) == 1
        assert second["failed"] == []
        assert len(publisher.items) == 1
        assert store.pending_dispatches() == []
    finally:
        store.close()


def test_task_store_counts_a_delivery_when_sql_ack_response_is_lost_after_commit() -> None:
    store = TaskStore("sqlite:///:memory:")
    publisher = IdempotentQueuePublisher()
    try:
        task_id = "research:receipt-committed"
        store.create(
            task_id,
            kind="research",
            title="回执提交后丢失",
            payload={"run_id": task_id},
            stage_dispatch=True,
        )
        original_mark = store.mark_dispatch_published

        def commit_then_fail(message_id: str) -> bool:
            acknowledged = original_mark(message_id)
            raise RuntimeError("sql response lost after commit")

        store.mark_dispatch_published = commit_then_fail

        result = store.publish_pending_dispatches(publisher, task_id=task_id)

        assert len(result["published"]) == 1
        assert result["failed"] == []
        assert result["published"][0]["confirmed"] is True
        assert len(publisher.items) == 1
        assert store.pending_dispatches(task_id=task_id) == []
    finally:
        store.close()


def test_task_store_confirms_existing_redis_delivery_without_republishing() -> None:
    store = TaskStore("sqlite:///:memory:")
    publisher = ExistingDeliveryPublisher(location="processing")
    try:
        task_id = "research:redis-already-accepted"
        store.create(
            task_id,
            kind="research",
            title="Redis 已接受",
            payload={"run_id": task_id},
            stage_dispatch=True,
        )

        result = store.publish_pending_dispatches(publisher, task_id=task_id)

        assert len(result["published"]) == 1
        assert result["published"][0]["confirmed"] is True
        assert publisher.inspect_calls == 1
        assert publisher.enqueue_calls == 0
        assert store.pending_dispatches(task_id=task_id) == []
    finally:
        store.close()


def test_task_store_suppresses_an_orphaned_outbox_after_task_reaches_terminal_state() -> None:
    store = TaskStore("sqlite:///:memory:")
    publisher = QueuePublisher()
    try:
        task_id = "research:terminal-outbox"
        store.create(
            task_id,
            kind="research",
            title="终态遗留 outbox",
            payload={"run_id": task_id},
            stage_dispatch=True,
        )
        store.sync(task_id, status="completed", result={"status": "completed"})

        result = store.publish_pending_dispatches(publisher, task_id=task_id)

        assert len(result["published"]) == 1
        assert result["published"][0]["settled"] is True
        assert publisher.items == []
        assert store.pending_dispatches(task_id=task_id) == []
    finally:
        store.close()


def test_task_store_rolls_back_task_when_dispatch_intent_insert_fails() -> None:
    store = TaskStore("sqlite:///:memory:")

    def fail_dispatch_insert(connection, cursor, statement, parameters, context, executemany):
        if "insert into control_task_dispatch_outbox" in statement.lower():
            raise RuntimeError("outbox insert unavailable")

    listener = fail_dispatch_insert
    event.listen(store._engine, "before_cursor_execute", listener)
    try:
        with pytest.raises(RuntimeError, match="outbox insert unavailable"):
            store.create(
                "research:dispatch-rollback",
                kind="research",
                title="回滚任务",
                payload={"run_id": "dispatch-rollback"},
                stage_dispatch=True,
            )
        assert store.get("research:dispatch-rollback") is None
        assert store.pending_dispatches() == []
    finally:
        event.remove(store._engine, "before_cursor_execute", listener)
        store.close()


def test_task_store_schedules_retry_and_outbox_in_one_sql_transaction() -> None:
    store = TaskStore("sqlite:///:memory:")
    try:
        task_id = "research:retry-transaction"
        store.create(task_id, kind="research", title="事务重试", payload={"run_id": "retry"})
        claimed = store.mark_claimed(
            task_id,
            worker_id="worker-1",
            attempt=1,
            lease_seconds=60,
        )
        assert claimed is not None

        retried = store.schedule_retry(
            task_id,
            attempt=2,
            error={"kind": "TEMPORARY", "message": "retry"},
            expected_worker_id="worker-1",
        )

        assert retried is not None
        assert retried["status"] == "queued"
        assert retried["attempt"] == 2
        pending = store.pending_dispatches(task_id=task_id)
        assert len(pending) == 1
        assert pending[0]["attempt"] == 2
        assert store.events(task_id)[0]["event_type"] == "retry_scheduled"
    finally:
        store.close()


def test_task_store_does_not_regress_attempt_when_an_old_claim_is_reacquired() -> None:
    store = TaskStore("sqlite:///:memory:")
    try:
        task_id = "research:stale-attempt"
        store.create(task_id, kind="research", title="旧 claim", payload={"run_id": task_id})
        first = store.mark_claimed(
            task_id,
            worker_id="worker-old",
            attempt=1,
            lease_seconds=60,
        )
        assert first is not None
        retried = store.schedule_retry(
            task_id,
            attempt=2,
            error={"kind": "TEMPORARY"},
            expected_worker_id="worker-old",
        )
        assert retried is not None and retried["attempt"] == 2

        reacquired = store.mark_claimed(
            task_id,
            worker_id="worker-new",
            attempt=1,
            lease_seconds=60,
        )

        assert reacquired is not None
        assert reacquired["attempt"] == 2
        assert store.get(task_id)["attempt"] == 2
    finally:
        store.close()


def test_task_store_repairs_a_published_dispatch_when_redis_list_was_lost() -> None:
    store = TaskStore("sqlite:///:memory:")
    publisher = IdempotentQueuePublisher()
    try:
        task_id = "research:published-repair"
        store.create(
            task_id,
            kind="research",
            title="队列修复",
            payload={"run_id": "published-repair"},
            stage_dispatch=True,
        )
        store.publish_pending_dispatches(publisher)
        assert publisher.ensure_calls == 1
        assert len(publisher.items) == 1
        publisher.items.clear()
        publisher.message_ids.clear()

        repaired = store.reconcile_queued_dispatches(publisher)
        assert repaired["checked"] == 1
        assert len(repaired["repaired"]) == 1
        assert repaired["failed"] == []
        assert len(publisher.items) == 1
        assert publisher.ensure_calls == 2

        repeated = store.reconcile_queued_dispatches(publisher)
        assert len(repeated["repaired"]) == 1
        assert len(publisher.items) == 1
        assert publisher.ensure_calls == 3
    finally:
        store.close()


def test_task_store_repairs_a_receipt_without_a_redis_list_item_during_relay() -> None:
    store = TaskStore("sqlite:///:memory:")
    publisher = IdempotentQueuePublisher()
    try:
        task_id = "research:receipt-list-split"
        store.create(
            task_id,
            kind="research",
            title="回执与队列分裂",
            payload={"run_id": "receipt-list-split"},
            stage_dispatch=True,
        )
        message_id = str(store.pending_dispatches(task_id=task_id)[0]["message_id"])
        # Redis may retain the published marker while its queue list was
        # restored or expired. The relay must recreate the list item.
        publisher.message_ids.add(message_id)

        result = store.publish_pending_dispatches(publisher, task_id=task_id)

        assert len(result["published"]) == 1
        assert result["failed"] == []
        assert publisher.ensure_calls == 1
        assert [item["message_id"] for item in publisher.items] == [message_id]
        assert store.pending_dispatches(task_id=task_id) == []
    finally:
        store.close()


def test_task_store_does_not_accept_a_false_sql_publish_acknowledgement() -> None:
    store = TaskStore("sqlite:///:memory:")
    publisher = IdempotentQueuePublisher()
    try:
        store.create(
            "research:false-ack",
            kind="research",
            title="错误回执",
            payload={"run_id": "false-ack"},
            stage_dispatch=True,
        )
        original_mark = store.mark_dispatch_published
        store.mark_dispatch_published = lambda message_id: False

        first = store.publish_pending_dispatches(publisher)

        assert first["published"] == []
        assert len(first["failed"]) == 1
        assert len(publisher.items) == 1
        assert len(store.pending_dispatches()) == 1

        store.mark_dispatch_published = original_mark
        second = store.publish_pending_dispatches(publisher)

        assert len(second["published"]) == 1
        assert second["failed"] == []
        assert len(publisher.items) == 1
        assert store.pending_dispatches() == []
    finally:
        store.close()


def test_task_store_rejects_a_mismatched_redis_delivery_receipt() -> None:
    class MismatchedPublisher(QueuePublisher):
        def enqueue(self, task_id, kind, payload, *, attempt, max_attempts, message_id):
            envelope = super().enqueue(
                task_id,
                kind,
                payload,
                attempt=attempt,
                max_attempts=max_attempts,
                message_id=message_id,
            )
            envelope["message_id"] = "different-message"
            return envelope

    store = TaskStore("sqlite:///:memory:")
    publisher = MismatchedPublisher()
    try:
        store.create(
            "research:mismatched-receipt",
            kind="research",
            title="错误投递回执",
            payload={"run_id": "mismatched-receipt"},
            stage_dispatch=True,
        )

        result = store.publish_pending_dispatches(publisher)

        assert result["published"] == []
        assert result["failed"][0]["error"]["message"] == "task publisher returned a mismatched message_id"
        assert len(store.pending_dispatches()) == 1
    finally:
        store.close()


def test_task_store_migrates_partial_unique_indexes_for_existing_databases(tmp_path) -> None:
    database = tmp_path / "runtime-indexes.sqlite3"
    store = TaskStore(f"sqlite:///{database.as_posix()}", required=True)
    try:
        with store._engine.begin() as connection:
            connection.execute(text(f"DROP INDEX IF EXISTS {ACTIVE_IDEMPOTENCY_INDEX}"))
            connection.execute(text(f"DROP INDEX IF EXISTS {PENDING_DISPATCH_INDEX}"))

        store._ensure_task_runtime_indexes()

        names = {item["name"] for item in inspect(store._engine).get_indexes("control_tasks")}
        names.update(item["name"] for item in inspect(store._engine).get_indexes("control_task_dispatch_outbox"))
        assert {ACTIVE_IDEMPOTENCY_INDEX, PENDING_DISPATCH_INDEX} <= names
    finally:
        store.close()


def test_task_store_concurrent_active_idempotency_returns_one_winner(tmp_path) -> None:
    database = tmp_path / "concurrent-idempotency.sqlite3"
    stores = [TaskStore(f"sqlite:///{database.as_posix()}", required=True) for _ in range(2)]
    barrier = threading.Barrier(2)

    def pause_before_task_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO CONTROL_TASKS"):
            barrier.wait(timeout=10)

    listeners = []
    for store in stores:
        listener = pause_before_task_insert
        event.listen(store._engine, "before_cursor_execute", listener)
        listeners.append((store._engine, listener))
    try:
        def create(index: int) -> dict[str, object]:
            return stores[index].create(
                f"research:concurrent-{index}",
                kind="research",
                title="并发幂等任务",
                idempotency_key="concurrent-request-1",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(create, range(2)))

        task_ids = {str(item["task_id"]) for item in results}
        assert len(task_ids) == 1
        assert task_ids <= {"research:concurrent-0", "research:concurrent-1"}
        assert len(stores[0].list(status="queued")) == 1
    finally:
        for engine, listener in listeners:
            event.remove(engine, "before_cursor_execute", listener)
        for store in stores:
            store.close()


def test_task_store_concurrent_staging_returns_one_pending_outbox(tmp_path) -> None:
    database = tmp_path / "concurrent-staging.sqlite3"
    seed = TaskStore(f"sqlite:///{database.as_posix()}", required=True)
    seed.create(
        "research:concurrent-stage",
        kind="research",
        title="并发分发意图",
        payload={"run_id": "concurrent-stage"},
    )
    seed.close()
    stores = [TaskStore(f"sqlite:///{database.as_posix()}", required=True) for _ in range(2)]
    barrier = threading.Barrier(2)

    def pause_before_dispatch_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO CONTROL_TASK_DISPATCH_OUTBOX"):
            barrier.wait(timeout=10)

    listeners = []
    for store in stores:
        listener = pause_before_dispatch_insert
        event.listen(store._engine, "before_cursor_execute", listener)
        listeners.append((store._engine, listener))
    try:
        def stage(index: int) -> dict[str, object] | None:
            return stores[index].stage_dispatch_for_task("research:concurrent-stage")

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(stage, range(2)))

        assert all(result is not None for result in results)
        pending = stores[0].pending_dispatches(task_id="research:concurrent-stage")
        assert len(pending) == 1
        assert all(result["message_id"] == pending[0]["message_id"] for result in results if result)
    finally:
        for engine, listener in listeners:
            event.remove(engine, "before_cursor_execute", listener)
        for store in stores:
            store.close()


def test_task_store_reserves_and_finishes_dead_letter_replay_without_task_payload() -> None:
    store = TaskStore("sqlite:///:memory:")
    try:
        source_id = "research:dead-audit"
        store.create(
            source_id,
            kind="research",
            title="失败研究",
            payload={"mode": "compare", "items": [{"secret": "must-not-be-in-event"}]},
            max_attempts=2,
        )
        store.dead_letter(
            source_id,
            error={"kind": "WORKER_FAILED", "message": "permanent"},
            attempt=2,
            dead_letter={
                "dead_letter_id": "dlq-1",
                "message_id": "message-1",
                "task_id": source_id,
                "kind": "research",
                "attempt": 2,
                "max_attempts": 2,
            },
        )

        reserved = store.reserve_dead_letter_replay(
            source_id,
            replay_task_id="research:replay-audit",
            request_id="replay-request-audit",
            actor="admin",
            reason="upstream recovered",
        )
        duplicate = store.reserve_dead_letter_replay(
            source_id,
            replay_task_id="research:other-target",
            request_id="replay-request-audit",
            actor="another-operator",
            reason="duplicate request",
        )
        assert reserved["status"] == "requested"
        assert duplicate["deduplicated"] is True
        assert duplicate["replay_task_id"] == "research:replay-audit"

        finished = store.finish_dead_letter_replay(
            "replay-request-audit",
            status="queued",
            result={"status": "queued", "task_id": "research:replay-audit"},
        )
        assert finished["status"] == "queued"
        assert finished["actor"] == "admin"
        assert finished["source_attempt"] == 2
        assert store.dead_letter_replays(source_id)[0]["request_id"] == "replay-request-audit"

        events = store.events(source_id)
        assert [event["event_type"] for event in events[:3]] == [
            "dead_letter_replayed",
            "dead_letter_replay_requested",
            "dead_lettered",
        ]
        replay_event = events[0]["payload"]
        assert replay_event["actor"] == "admin"
        assert replay_event["reason"] == "upstream recovered"
        assert replay_event["source_task_id"] == source_id
        assert replay_event["replay_task_id"] == "research:replay-audit"
        assert "payload" not in replay_event
        assert "secret" not in str(replay_event)
    finally:
        store.close()


def test_task_store_settles_replay_audit_after_target_completion_without_copying_result() -> None:
    store = TaskStore("sqlite:///:memory:")
    try:
        source_id = "research:replay-settle-completed"
        target_id = "research:replay-settle-completed-target"
        store.create(source_id, kind="research", title="源任务", payload={"source": True})
        store.dead_letter(
            source_id,
            error={"kind": "WORKER_FAILED", "message": "source failed"},
            attempt=1,
        )
        store.reserve_dead_letter_replay(
            source_id,
            replay_task_id=target_id,
            request_id="replay-settle-completed-request",
            actor="admin",
            reason="retry",
        )
        store.create(
            target_id,
            kind="research",
            title="重放目标",
            payload={"target": True},
        )
        store.sync(
            target_id,
            status="completed",
            result={
                "status": "completed",
                "records": [{"secret": "must-not-be-copied"}],
            },
        )

        audit = store.get_dead_letter_replay("replay-settle-completed-request")
        assert audit is not None
        assert audit["status"] == "completed"
        assert audit["result"] == {
            "status": "completed",
            "task_id": target_id,
            "version": 2,
            "attempt": 0,
        }
        assert "records" not in str(audit)
        events = store.events(source_id, limit=20)
        settled = [event for event in events if event["event_type"] == "dead_letter_replay_settled"]
        assert len(settled) == 1
        assert "must-not-be-copied" not in str(settled[0])

        before_count = len(events)
        assert store.reconcile_dead_letter_replays(request_id="replay-settle-completed-request")["settled"] == 0
        assert len(store.events(source_id, limit=20)) == before_count
    finally:
        store.close()


def test_task_store_reconciles_a_target_that_finished_before_the_audit_row() -> None:
    store = TaskStore("sqlite:///:memory:")
    try:
        source_id = "research:replay-late-audit"
        target_id = "research:replay-late-audit-target"
        store.create(source_id, kind="research", title="源任务", payload={"source": True})
        store.dead_letter(
            source_id,
            error={"kind": "WORKER_FAILED", "message": "source failed"},
            attempt=1,
        )
        store.create(
            target_id,
            kind="research",
            title="已先完成的目标",
            payload={"target": True},
            status="completed",
        )
        store.sync(target_id, status="completed", result={"status": "completed", "rows": 999})

        reserved = store.reserve_dead_letter_replay(
            source_id,
            replay_task_id=target_id,
            request_id="replay-late-audit-request",
            actor="admin",
            reason="late audit",
        )

        assert reserved["status"] == "completed"
        assert [event["event_type"] for event in store.events(source_id, limit=20)][:3] == [
            "dead_letter_replay_settled",
            "dead_letter_replay_requested",
            "dead_lettered",
        ]
    finally:
        store.close()


@pytest.mark.parametrize("terminal_status", ["failed", "dead_lettered"])
def test_task_store_settles_failed_or_dead_lettered_replay_targets(terminal_status: str) -> None:
    store = TaskStore("sqlite:///:memory:")
    try:
        source_id = f"research:replay-target-{terminal_status}"
        target_id = f"research:replay-target-{terminal_status}-target"
        request_id = f"replay-target-{terminal_status}-request"
        store.create(source_id, kind="research", title="源任务", payload={"source": True})
        store.dead_letter(
            source_id,
            error={"kind": "WORKER_FAILED", "message": "source failed"},
            attempt=1,
        )
        store.reserve_dead_letter_replay(
            source_id,
            replay_task_id=target_id,
            request_id=request_id,
            actor="admin",
            reason="terminal target",
        )
        store.create(
            target_id,
            kind="research",
            title="终态重放目标",
            payload={"target": True},
            max_attempts=1,
        )
        if terminal_status == "failed":
            store.sync(
                target_id,
                status="failed",
                error={"kind": "TARGET_FAILED", "message": "target stopped"},
            )
        else:
            store.dead_letter(
                target_id,
                error={"kind": "TARGET_DEAD", "message": "target exhausted"},
                attempt=1,
            )

        audit = store.get_dead_letter_replay(request_id)
        assert audit is not None and audit["status"] == terminal_status
        assert audit["error"]["kind"] in {"TARGET_FAILED", "TARGET_DEAD"}
        assert len(
            [
                event
                for event in store.events(source_id, limit=20)
                if event["event_type"] == "dead_letter_replay_settled"
            ]
        ) == 1
    finally:
        store.close()


def test_task_store_archives_redacted_events_and_can_reconcile_pending_rows(tmp_path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")
    store = TaskStore("sqlite:///:memory:", event_archive=archive)
    try:
        store.create("research:log-1", kind="research", title="研究日志")
        event = store.append_event(
            "research:log-1",
            "started",
            status="running",
            message="token=must-not-leak",
            payload={"api_key": "key-value", "progress": {"completed": 0}},
        )

        assert event["archive_state"] == "READY"
        listed = store.events("research:log-1")
        assert listed[0]["archive"] == event["archive"]
        restored = archive.read(event["archive"], expected_task_id="research:log-1")
        assert restored["payload"]["api_key"] == "[REDACTED]"
        assert "must-not-leak" not in str(restored)
        assert "must-not-leak" not in str(listed[0])

        store.event_archive = None
        pending = store.append_event("research:log-1", "progress", payload={"completed": 1})
        assert pending["archive_state"] == "DISABLED"
        store.event_archive = archive
        repaired = store.reconcile_event_archives()
        assert repaired == {"scanned": 1, "archived": 1, "failed": 0}
        assert store.events("research:log-1")[0]["archive"] is not None
    finally:
        store.close()


def test_task_store_records_archive_failures_and_retries_them(tmp_path) -> None:
    class FailingArchive(TaskLogArchive):
        def write(self, event):
            raise TaskLogArchiveError("archive backend unavailable")

    failing = FailingArchive(tmp_path / "logs")
    archive = TaskLogArchive(tmp_path / "logs")
    store = TaskStore("sqlite:///:memory:", event_archive=failing)
    try:
        task_id = "research:log-retry"
        store.create(task_id, kind="research", title="归档重试")
        event = store.append_event(task_id, "progress", payload={"completed": 1})

        assert event["archive_state"] == "PENDING"
        assert event["archive_attempts"] == 1
        assert event["archive_error"] == "archive backend unavailable"
        assert store.event_archive_status()["ok"] is False

        store.event_archive = archive
        repaired = store.reconcile_event_archives()

        assert repaired == {"scanned": 1, "archived": 1, "failed": 0}
        repaired_event = store.events(task_id)[0]
        assert repaired_event["archive_state"] == "READY"
        assert repaired_event["archive_attempts"] == 2
        assert repaired_event["archive_error"] is None
    finally:
        store.close()


def test_task_store_detects_and_repairs_a_tampered_ready_event_archive(tmp_path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")
    store = TaskStore("sqlite:///:memory:", event_archive=archive)
    try:
        task_id = "research:log-tamper"
        store.create(task_id, kind="research", title="日志完整性")
        event = store.append_event(task_id, "started", status="running", payload={"step": 1})
        archive_path = archive.root / str(event["archive"]["key"])
        archive_path.write_text('{"tampered":true}', encoding="utf-8")

        status = store.event_archive_status()
        assert status["ok"] is False
        assert status["corrupt_count"] == 1

        repaired = store.reconcile_event_archives()

        assert repaired == {"scanned": 1, "archived": 1, "failed": 0}
        assert store.event_archive_status()["ok"] is True
        assert archive.read(
            store.events(task_id)[0]["archive"],
            expected_task_id=task_id,
            expected_event_id=int(event["event_id"]),
        )["payload"] == {"step": 1}
    finally:
        store.close()


def test_task_store_requeues_a_task_after_its_worker_heartbeat_is_lost(tmp_path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")
    store = TaskStore("sqlite:///:memory:", event_archive=archive)
    try:
        task_id = "research:worker-loss"
        store.create(task_id, kind="research", title="Worker 恢复", payload={"mode": "compare"})
        claimed = store.mark_claimed(
            task_id,
            worker_id="worker-lost",
            attempt=1,
            lease_seconds=900,
        )
        assert claimed is not None

        recovered = store.recover_after_worker_loss(
            task_id,
            worker_id="worker-lost",
            attempt=1,
            message_id="old-message",
        )

        assert recovered is not None
        assert recovered["status"] == "queued"
        assert recovered["recovered_after_worker_loss"] is True
        assert recovered["worker_id"] is None
        pending = store.pending_dispatches(task_id=task_id)
        assert len(pending) == 1
        assert pending[0]["status"] == "pending"
        assert store.events(task_id, limit=10)[0]["event_type"] == "requeued_after_worker_loss"
    finally:
        store.close()


def test_task_store_reconciles_a_backlog_larger_than_the_legacy_batch(tmp_path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")
    store = TaskStore("sqlite:///:memory:")
    try:
        task_id = "research:log-backlog"
        store.create(task_id, kind="research", title="研究日志")
        for index in range(125):
            store.append_event(task_id, "progress", payload={"completed": index})

        store.event_archive = archive
        first = store.reconcile_event_archives(limit=120)
        second = store.reconcile_event_archives(limit=120)

        assert first == {"scanned": 120, "archived": 120, "failed": 0}
        assert second == {"scanned": 5, "archived": 5, "failed": 0}
        assert all(event["archive"] is not None for event in store.events(task_id, limit=500))
    finally:
        store.close()


def test_task_store_rejects_a_second_worker_while_the_lease_is_alive(tmp_path) -> None:
    database = tmp_path / "claims.sqlite3"
    url = f"sqlite:///{database.as_posix()}"
    first = TaskStore(url, required=True)
    second = TaskStore(url, required=True)
    try:
        first.create("research:claim-1", kind="research", title="研究任务")
        acquired = first.mark_claimed(
            "research:claim-1",
            worker_id="worker-1",
            attempt=1,
            lease_seconds=60,
        )
        rejected = second.mark_claimed(
            "research:claim-1",
            worker_id="worker-2",
            attempt=1,
            lease_seconds=60,
        )

        assert acquired is not None and acquired["claim_acquired"] is True
        assert first.renew_claim(
            "research:claim-1",
            worker_id="worker-1",
            lease_seconds=60,
        ) is True
        assert second.renew_claim(
            "research:claim-1",
            worker_id="worker-2",
            lease_seconds=60,
        ) is False
        assert rejected is not None
        assert rejected["claim_acquired"] is False
        assert rejected["worker_id"] == "worker-1"
    finally:
        second.close()
        first.close()


def test_task_store_clears_worker_lease_when_task_reaches_terminal_state() -> None:
    store = TaskStore("sqlite:///:memory:")
    try:
        store.create("research:terminal-lease", kind="research", title="研究任务")
        claimed = store.mark_claimed(
            "research:terminal-lease",
            worker_id="worker-1",
            attempt=1,
            lease_seconds=60,
        )
        assert claimed is not None

        finished = store.sync("research:terminal-lease", status="completed", result={"ok": True})

        assert finished is not None
        assert finished["worker_id"] is None
        assert finished["lease_expires_at"] is None
    finally:
        store.close()


def test_task_store_recovers_an_orphaned_sql_lease_through_the_outbox(tmp_path) -> None:
    database = tmp_path / "orphaned-lease.sqlite3"
    store = TaskStore(f"sqlite:///{database.as_posix()}", required=True)
    try:
        task_id = "research:orphaned-sql-lease"
        payload = {"run_id": "orphaned-sql-lease", "mode": "compare"}
        store.create(task_id, kind="research", title="研究任务", payload=payload, max_attempts=3)
        store.mark_claimed(task_id, worker_id="dead-worker", attempt=2, lease_seconds=60)
        with store._engine.begin() as connection:
            connection.execute(
                store._tasks.update()
                .where(store._tasks.c.task_id == task_id)
                .values(lease_expires_at="2000-01-01T00:00:00+00:00")
            )

        recovered = store.recover_expired_leases()

        assert recovered[0]["task_id"] == task_id
        assert recovered[0]["attempt"] == 2
        record = store.get(task_id)
        assert record is not None
        assert record["status"] == "queued"
        assert record["worker_id"] is None
        pending = store.pending_dispatches(task_id=task_id)
        assert len(pending) == 1
        assert pending[0]["payload"] == payload
        assert pending[0]["attempt"] == 2
        assert store.events(task_id)[0]["event_type"] == "requeued_after_sql_lease_expiry"
    finally:
        store.close()


def test_task_store_does_not_duplicate_sql_lease_recovery(tmp_path) -> None:
    database = tmp_path / "idempotent-lease.sqlite3"
    store = TaskStore(f"sqlite:///{database.as_posix()}", required=True)
    try:
        task_id = "research:idempotent-sql-lease"
        store.create(task_id, kind="research", title="研究任务", payload={"run_id": "idempotent"})
        store.mark_claimed(task_id, worker_id="dead-worker", attempt=1, lease_seconds=60)
        with store._engine.begin() as connection:
            connection.execute(
                store._tasks.update()
                .where(store._tasks.c.task_id == task_id)
                .values(lease_expires_at="2000-01-01T00:00:00+00:00")
            )

        first = store.recover_expired_leases()
        second = store.recover_expired_leases()

        assert len(first) == 1
        assert second == []
        assert len(store.pending_dispatches(task_id=task_id)) == 1
        assert len(store.events(task_id)) == 1
    finally:
        store.close()


def test_task_store_archives_dead_letter_replay_audit_events(tmp_path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")
    store = TaskStore("sqlite:///:memory:", event_archive=archive)
    try:
        source_id = "research:archive-replay"
        store.create(
            source_id,
            kind="research",
            title="研究任务",
            payload={"run_id": "archive-replay", "mode": "compare"},
        )
        store.dead_letter(
            source_id,
            error={"kind": "WORKER_FAILED", "message": "permanent"},
            attempt=3,
        )

        store.reserve_dead_letter_replay(
            source_id,
            replay_task_id="research:archive-replay-target",
            request_id="archive-replay-request",
            actor="admin",
            reason="恢复重放",
        )
        store.finish_dead_letter_replay(
            "archive-replay-request",
            status="queued",
            result={"status": "queued", "task_id": "research:archive-replay-target"},
        )

        events = store.events(source_id, limit=10)
        assert len(events) == 3
        assert all(isinstance(event["archive"], dict) for event in events)
        assert store.event_archive_status()["pending_count"] == 0
        for event in events:
            restored = archive.read(
                event["archive"],
                expected_task_id=source_id,
                expected_event_id=event["event_id"],
            )
            assert restored["event_id"] == event["event_id"]
    finally:
        store.close()
