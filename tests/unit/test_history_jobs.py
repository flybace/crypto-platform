import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from application.history_download import DownloadResult
from application.history_storage import HistoryStorage
from backend.app.services.history_jobs import HistoryJobManager
from backend.app.services.task_store import TaskStore
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


class NoopHistoryService:
    def __init__(self, root) -> None:
        self.storage = HistoryStorage(root)

    def close(self) -> None:
        return None


class QueueCapture:
    def __init__(self) -> None:
        self.items = []

    def enqueue(self, task_id, kind, payload, *, attempt=1, max_attempts=3, message_id=None):
        envelope = {"message_id": message_id or f"message-{len(self.items) + 1}", "task_id": task_id, "kind": kind, "payload": payload, "attempt": attempt, "max_attempts": max_attempts}
        self.items.append(envelope)
        return envelope

    def replay_dead_letter(
        self,
        source_task_id,
        kind,
        payload,
        *,
        replay_task_id,
        request_id,
        max_attempts,
        message_id=None,
    ):
        return {
            "envelope": self.enqueue(replay_task_id, kind, payload, max_attempts=max_attempts, message_id=message_id),
            "request_id": request_id,
            "source_task_id": source_task_id,
            "max_attempts": max_attempts,
        }


class DownloadHistoryService(NoopHistoryService):
    def download(self, query):
        start = query.start_at
        dataset = self.storage.upsert(
            query,
            [
                Candle(
                    venue_id=query.venue_id,
                    market_type=query.market_type,
                    instrument_key=query.instrument_key,
                    native_symbol=query.native_symbol,
                    interval=query.interval,
                    open_time=start,
                    close_time=start + timedelta(days=1) - timedelta(milliseconds=1),
                    open=Decimal("100"),
                    high=Decimal("101"),
                    low=Decimal("99"),
                    close=Decimal("100"),
                    volume=Decimal("1"),
                    quote_volume=Decimal("100"),
                    trade_count=1,
                )
            ],
            source="test",
        )
        return DownloadResult(query=query, dataset=dataset, fetched_rows=1)


class ArchiveCapture:
    def __init__(self) -> None:
        self.calls = 0

    def archive_all(self, storage):
        self.calls += 1
        return {
            "status": "COMPLETED",
            "archived_count": len(storage.list_datasets()),
            "dataset_count": len(storage.list_datasets()),
            "stale_count": 0,
            "missing_count": 0,
            "failed_count": 0,
            "error": None,
        }


def _query() -> HistoryQuery:
    return HistoryQuery(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=datetime(2026, 1, 1, tzinfo=UTC),
        end_at=datetime(2026, 1, 2, tzinfo=UTC),
    )


def _json_job(job_id: str, query: HistoryQuery, *, status: str, item_status: str) -> dict[str, object]:
    item = HistoryJobManager._item(query)
    item["status"] = item_status
    return {
        "job_id": job_id,
        "status": status,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:01:00+00:00",
        "request_fingerprint": "json-recovery",
        "total": 1,
        "completed": 0,
        "blocked": 0,
        "failed": 0,
        "interrupted": 0,
        "cancelled": 0,
        "archive": None,
        "items": [item],
    }


def test_history_jobs_restore_active_work_as_interrupted(tmp_path) -> None:
    state_path = tmp_path / "jobs.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "job_id": "job-restarted",
                        "status": "running",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:01:00+00:00",
                        "total": 1,
                        "completed": 0,
                        "blocked": 0,
                        "failed": 0,
                        "items": [{"status": "running", "venue_id": "binance"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manager = HistoryJobManager(NoopHistoryService(tmp_path / "history"), state_path)
    try:
        job = manager.get("job-restarted")
        assert job is not None
        assert job["status"] == "interrupted"
        assert job["items"][0]["status"] == "interrupted"
        assert job["items"][0]["error"]["kind"] == "PROCESS_RESTARTED"
    finally:
        manager.close()


def test_task_ledger_is_authoritative_over_stale_json_after_restart(tmp_path) -> None:
    query = _query()
    state_path = tmp_path / "jobs.json"
    state_path.write_text(
        json.dumps({"version": 1, "jobs": [_json_job("job-authority", query, status="running", item_status="running")] }),
        encoding="utf-8",
    )
    store = TaskStore("sqlite:///:memory:")
    store.create(
        "history:job-authority",
        kind="history_download",
        title="历史数据下载",
        payload={"request_fingerprint": "sql-recovery", "items": [HistoryJobManager._item(query)]},
        status="running",
        progress={"completed": 0, "total": 1, "blocked": 0, "failed": 0, "interrupted": 0, "cancelled": 0},
    )
    store.sync(
        "history:job-authority",
        status="completed",
        progress={"completed": 1, "total": 1, "blocked": 0, "failed": 0, "interrupted": 0, "cancelled": 0},
        payload={
            "request_fingerprint": "sql-authority",
            "items": [{**HistoryJobManager._item(query), "status": "completed"}],
        },
        result={"archive": {"status": "COMPLETED"}},
        error={},
    )
    manager = HistoryJobManager(
        NoopHistoryService(tmp_path / "history"),
        state_path,
        task_store=store,
        task_queue=QueueCapture(),
    )
    try:
        job = manager.get("job-authority")
        assert job is not None
        assert job["status"] == "completed"
        assert job["request_fingerprint"] == "sql-authority"
        assert job["items"][0]["status"] == "completed"
        assert json.loads(state_path.read_text(encoding="utf-8"))["jobs"][0]["status"] == "completed"
    finally:
        manager.close()
        store.close()


def test_corrupt_json_recovery_copy_does_not_hide_a_valid_task_ledger(tmp_path) -> None:
    state_path = tmp_path / "jobs.json"
    state_path.write_text("{not-json", encoding="utf-8")
    query = _query()
    store = TaskStore("sqlite:///:memory:")
    store.create(
        "history:job-json-corrupt",
        kind="history_download",
        title="历史数据下载",
        payload={"request_fingerprint": "sql-authority", "items": [HistoryJobManager._item(query)]},
        status="completed",
        progress={"completed": 1, "total": 1, "blocked": 0, "failed": 0, "interrupted": 0, "cancelled": 0},
    )
    manager = HistoryJobManager(
        NoopHistoryService(tmp_path / "history"),
        state_path,
        task_store=store,
        task_queue=QueueCapture(),
    )
    try:
        job = manager.get("job-json-corrupt")
        assert job is not None
        assert job["status"] == "completed"
        assert json.loads(state_path.read_text(encoding="utf-8"))["jobs"][0]["job_id"] == "job-json-corrupt"
    finally:
        manager.close()
        store.close()


def test_json_only_active_job_is_interrupted_when_sql_ledger_is_missing(tmp_path) -> None:
    query = _query()
    state_path = tmp_path / "jobs.json"
    state_path.write_text(
        json.dumps({"version": 1, "jobs": [_json_job("job-missing-ledger", query, status="running", item_status="running")] }),
        encoding="utf-8",
    )
    store = TaskStore("sqlite:///:memory:")
    manager = HistoryJobManager(
        NoopHistoryService(tmp_path / "history"),
        state_path,
        task_store=store,
        task_queue=QueueCapture(),
    )
    try:
        job = manager.get("job-missing-ledger")
        assert job is not None
        assert job["status"] == "interrupted"
        assert job["error"]["kind"] == "TASK_LEDGER_MISSING"
        persisted = store.get("history:job-missing-ledger")
        assert persisted is not None
        assert persisted["status"] == "interrupted"
    finally:
        manager.close()
        store.close()


def test_corrupt_task_ledger_uses_json_recovery_and_records_an_audit_event(tmp_path) -> None:
    query = _query()
    state_path = tmp_path / "jobs.json"
    state_path.write_text(
        json.dumps({"version": 1, "jobs": [_json_job("job-corrupt-ledger", query, status="running", item_status="running")] }),
        encoding="utf-8",
    )
    store = TaskStore("sqlite:///:memory:")
    store.create(
        "history:job-corrupt-ledger",
        kind="history_download",
        title="历史数据下载",
        payload={"request_fingerprint": "broken", "items": "not-a-list"},
        status="running",
        progress={"completed": 0, "total": 1},
    )
    manager = HistoryJobManager(
        NoopHistoryService(tmp_path / "history"),
        state_path,
        task_store=store,
        task_queue=QueueCapture(),
    )
    try:
        job = manager.get("job-corrupt-ledger")
        assert job is not None
        assert job["status"] == "interrupted"
        assert job["error"]["kind"] == "TASK_LEDGER_CORRUPT"
        repaired = store.get("history:job-corrupt-ledger")
        assert repaired is not None
        assert isinstance(repaired["payload"]["items"], list)
        assert any(event["event_type"] == "task_ledger_corrupt" for event in store.events("history:job-corrupt-ledger"))
    finally:
        manager.close()
        store.close()


def test_corrupt_task_ledger_without_json_keeps_a_visible_failed_job(tmp_path) -> None:
    store = TaskStore("sqlite:///:memory:")
    store.create(
        "history:job-orphan-corrupt",
        kind="history_download",
        title="历史数据下载",
        payload={"items": []},
        status="running",
        progress={"completed": 0, "total": 1},
    )
    manager = HistoryJobManager(
        NoopHistoryService(tmp_path / "history"),
        tmp_path / "jobs.json",
        task_store=store,
        task_queue=QueueCapture(),
    )
    try:
        job = manager.get("job-orphan-corrupt")
        assert job is not None
        assert job["status"] == "failed"
        assert job["error"]["kind"] == "TASK_LEDGER_CORRUPT"
        assert store.get("history:job-orphan-corrupt")["status"] == "failed"
    finally:
        manager.close()
    restored = HistoryJobManager(
        NoopHistoryService(tmp_path / "restored-history"),
        tmp_path / "jobs.json",
        task_store=store,
        task_queue=QueueCapture(),
    )
    try:
        job = restored.get("job-orphan-corrupt")
        assert job is not None
        assert job["status"] == "failed"
        assert sum(
            event["event_type"] == "task_ledger_corrupt"
            for event in store.events("history:job-orphan-corrupt")
        ) == 1
    finally:
        restored.close()
        store.close()


def test_history_job_can_be_submitted_to_external_queue(tmp_path) -> None:
    service = NoopHistoryService(tmp_path / "history")
    store = TaskStore("sqlite:///:memory:")
    queue = QueueCapture()
    manager = HistoryJobManager(
        service,
        tmp_path / "jobs.json",
        task_store=store,
        task_queue=queue,
    )
    query = HistoryQuery(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=datetime(2026, 1, 1, tzinfo=UTC),
        end_at=datetime(2026, 1, 2, tzinfo=UTC) + timedelta(days=1),
    )
    try:
        job = manager.submit((query,))
        assert job["status"] == "queued"
        assert queue.items[0]["task_id"] == f"history:{job['job_id']}"
        assert queue.items[0]["payload"]["queries"][0]["native_symbol"] == "BTCUSDT"
        assert store.get(f"history:{job['job_id']}")["status"] == "queued"
    finally:
        manager.close()
        store.close()


def test_history_job_manager_rehydrates_a_sibling_process_task(tmp_path) -> None:
    store = TaskStore("sqlite:///:memory:")
    queue = QueueCapture()
    api_manager = HistoryJobManager(
        NoopHistoryService(tmp_path / "api-history"),
        tmp_path / "api-jobs.json",
        task_store=store,
        task_queue=queue,
    )
    query = HistoryQuery(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=datetime(2026, 1, 1, tzinfo=UTC),
        end_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    try:
        created = api_manager.submit((query,))
        worker_manager = HistoryJobManager(
            NoopHistoryService(tmp_path / "worker-history"),
            tmp_path / "worker-jobs.json",
            task_store=store,
            task_queue=queue,
            recover_interrupted=False,
        )
        try:
            restored = worker_manager.get(created["job_id"])
            assert restored is not None
            assert restored["status"] == "queued"
            assert restored["items"][0]["native_symbol"] == "BTCUSDT"
            assert worker_manager.list()[0]["job_id"] == created["job_id"]
        finally:
            worker_manager.close()
    finally:
        api_manager.close()
        store.close()


def test_completed_history_job_refreshes_archive_summary(tmp_path) -> None:
    service = DownloadHistoryService(tmp_path / "history")
    archive = ArchiveCapture()
    manager = HistoryJobManager(
        service,
        tmp_path / "jobs.json",
        task_queue=QueueCapture(),
        archive=archive,
    )
    query = HistoryQuery(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=datetime(2026, 1, 1, tzinfo=UTC),
        end_at=datetime(2026, 1, 3, tzinfo=UTC),
    )
    try:
        created = manager.submit((query,))
        manager.run_job(created["job_id"], (query,))
        finished = manager.get(created["job_id"])
        assert finished is not None
        assert finished["status"] == "completed"
        assert finished["archive"]["status"] == "COMPLETED"
        assert finished["archive"]["archived_count"] == 1
        assert archive.calls == 1
    finally:
        manager.close()


def test_history_dead_letter_replay_creates_deterministic_target_job(tmp_path) -> None:
    store = TaskStore("sqlite:///:memory:")
    queue = QueueCapture()
    manager = HistoryJobManager(
        NoopHistoryService(tmp_path / "history"),
        tmp_path / "jobs.json",
        task_store=store,
        task_queue=queue,
    )
    query = HistoryQuery(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=datetime(2026, 1, 1, tzinfo=UTC),
        end_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    try:
        created = manager.submit((query,))
        source_id = f"history:{created['job_id']}"
        store.dead_letter(
            source_id,
            error={"kind": "WORKER_FAILED", "message": "permanent"},
            attempt=3,
        )
        target_id = "history:replay-history-1"

        replayed = manager.replay_dead_letter(source_id, target_id, "history-replay-1")

        assert replayed["job_id"] == "replay-history-1"
        assert queue.items[-1]["task_id"] == target_id
        target = store.get(target_id)
        assert target is not None
        assert target["retry_of"] == source_id
        assert target["payload"]["items"][0]["native_symbol"] == "BTCUSDT"
    finally:
        manager.close()
        store.close()
