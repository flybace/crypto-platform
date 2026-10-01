from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from application.history_download import HistoryDownloadService
from application.history_storage import HistoryStorage
from backend.app.main import create_app
from backend.app.services.task_dispatcher import TaskDispatcher
from backend.app.settings import Settings
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


def _settings() -> Settings:
    return Settings(
        app_name="Control Plane Test",
        version="test",
        admin_username="admin",
        admin_password="local-password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
    )


def _seed(storage: HistoryStorage) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    query = HistoryQuery(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=start,
        end_at=start + timedelta(days=4),
    )
    storage.upsert(
        query,
        [
            Candle(
                venue_id="binance",
                market_type=MarketType.SPOT,
                instrument_key=query.instrument_key,
                native_symbol=query.native_symbol,
                interval=query.interval,
                open_time=start + timedelta(days=index),
                close_time=start + timedelta(days=index + 1) - timedelta(milliseconds=1),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("1"),
                quote_volume=price,
                trade_count=1,
            )
            for index, price in enumerate((100, 110, 120))
        ],
        source="test",
    )


def _headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "local-password"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


class ReplayQueue:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def replay_dead_letter(
        self,
        source_task_id: str,
        kind: str,
        payload: dict[str, object],
        *,
        replay_task_id: str,
        request_id: str,
        max_attempts: int,
        message_id: str | None = None,
    ) -> dict[str, object]:
        envelope = {
            "message_id": message_id or f"message-{len(self.items) + 1}",
            "task_id": replay_task_id,
            "kind": kind,
            "payload": payload,
            "attempt": 1,
            "max_attempts": max_attempts,
        }
        self.items.append(envelope)
        return {"envelope": envelope, "deduplicated": False}


def test_crypto_control_plane_exposes_research_advice_system_and_tradeplan(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    _seed(storage)
    app = create_app(_settings(), history_service=HistoryDownloadService({}, storage))
    client = TestClient(app)
    headers = _headers(client)
    try:
        assert client.get("/api/v1/advice/summary").status_code == 401

        advice = client.get("/api/v1/advice/summary", params={"interval": "1d"}, headers=headers)
        assert advice.status_code == 200
        assert advice.json()["research_only"] is True
        assert advice.json()["execution_eligible"] is False
        assert advice.json()["total"] == 1

        generated = client.post("/api/v1/advice/generate", params={"interval": "1d"}, headers=headers)
        assert generated.status_code == 201
        snapshot_id = generated.json()["snapshot_id"]
        assert client.get(f"/api/v1/advice/snapshots/{snapshot_id}", headers=headers).status_code == 200

        system = client.get("/api/v1/system/status", headers=headers)
        assert system.status_code == 200
        assert system.json()["data"]["coverage"]["verified_dataset_count"] == 1
        assert system.json()["readiness"]["overall"]["real_execution_allowed"] is False

        plan = client.get("/api/v1/tradeplan/summary", headers=headers)
        assert plan.status_code == 200
        assert plan.json()["module"] == "tradeplan"
        assert plan.json()["market_mode"] == "24/7 spot research"
    finally:
        app.state.history_jobs.close()


def test_notifications_are_local_and_assistant_actions_are_explicitly_read_only(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    _seed(storage)
    app = create_app(_settings(), history_service=HistoryDownloadService({}, storage))
    client = TestClient(app)
    headers = _headers(client)
    try:
        notification = client.post("/api/v1/notifications/test", headers=headers)
        assert notification.status_code == 200
        item_id = notification.json()["id"]
        assert client.get("/api/v1/notifications/unread", headers=headers).json()["count"] == 1
        assert client.post(f"/api/v1/notifications/read/{item_id}", headers=headers).status_code == 200
        assert client.get("/api/v1/notifications/unread", headers=headers).json()["count"] == 0

        actions = client.get("/api/v1/assistant/actions", headers=headers)
        assert actions.status_code == 200
        assert actions.json()["read_only"] is True
        assert "advice.candidates.read" in {item["id"] for item in actions.json()["items"]}

        expires_at = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        request = {
            "request_id": "request-001",
            "action": "history.coverage.read",
            "actor": "gace-ai",
            "risk_level": "READ_ONLY",
            "payload": {},
            "authorization_id": "auth-read-001",
            "idempotency_key": "idem-001",
            "expires_at": expires_at,
        }
        invoked = client.post("/api/v1/assistant/invoke", json=request, headers=headers)
        assert invoked.status_code == 200
        assert invoked.json()["status"] == "completed"
        replayed = client.post("/api/v1/assistant/invoke", json=request, headers=headers)
        assert replayed.status_code == 200
        assert replayed.json()["replayed"] is True

        write_request = {**request, "request_id": "request-002", "risk_level": "CONTROLLED_WRITE", "idempotency_key": "idem-002"}
        assert client.post("/api/v1/assistant/invoke", json=write_request, headers=headers).status_code == 422
        expired_request = {**request, "request_id": "request-003", "idempotency_key": "idem-003", "expires_at": "2020-01-01T00:00:00+00:00"}
        assert client.post("/api/v1/assistant/invoke", json=expired_request, headers=headers).status_code == 422
    finally:
        app.state.history_jobs.close()


def test_dead_letter_task_projection_is_authenticated_and_bounded() -> None:
    app = create_app(_settings())
    client = TestClient(app)
    headers = _headers(client)
    try:
        app.state.task_store.create(
            "research:dead-1",
            kind="research",
            title="失败研究",
            payload={"mode": "compare"},
            max_attempts=2,
        )
        app.state.task_store.dead_letter(
            "research:dead-1",
            error={"kind": "WORKER_FAILED", "message": "permanent"},
            attempt=2,
        )

        assert client.get("/api/v1/tasks/dead-letters").status_code == 401
        response = client.get("/api/v1/tasks/dead-letters", headers=headers)
        assert response.status_code == 200
        assert response.json()["items"][0]["task_id"] == "research:dead-1"
        assert response.json()["items"][0]["attempt"] == 2
        assert client.get("/api/v1/tasks/summary", headers=headers).json()["dead_lettered"] == 1
    finally:
        app.state.history_jobs.close()


def test_task_log_archive_inventory_is_authenticated_and_verified(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    app = create_app(_settings(), history_service=HistoryDownloadService({}, storage))
    client = TestClient(app)
    headers = _headers(client)
    task_id = "research:archive-inventory-api"
    try:
        app.state.task_store.create(task_id, kind="research", title="归档盘点")
        app.state.task_store.append_event(
            task_id,
            "completed",
            status="completed",
            message="任务已完成",
            payload={"summary": {"status": "completed"}},
        )

        assert client.get("/api/v1/tasks/log-archive").status_code == 401
        response = client.get("/api/v1/tasks/log-archive", headers=headers, params={"include_files": True})

        assert response.status_code == 200
        body = response.json()
        assert body["archive"]["status"] == "READY"
        assert body["archive"]["task_count"] == 1
        assert body["archive"]["event_object_count"] == 1
        assert body["sql"]["status"] == "ready"
        assert body["sql"]["ready_count"] == 1
        assert body["archive"]["files"]
    finally:
        app.state.history_jobs.close()


def test_task_log_restore_rehearsal_supports_safe_isolated_suffix_and_audit(tmp_path) -> None:
    storage_root = tmp_path / "history"
    settings = replace(
        _settings(),
        history_data_path=str(storage_root),
        task_log_backup_path=str(tmp_path / "backup" / "task-logs.tar"),
        task_log_restore_path=str(tmp_path / "restore" / "task-logs"),
    )
    app = create_app(settings)
    client = TestClient(app)
    headers = _headers(client)
    task_id = "research:restore-rehearsal-api"
    try:
        app.state.task_store.create(task_id, kind="research", title="恢复演练")
        app.state.task_store.append_event(
            task_id,
            "completed",
            status="completed",
            message="任务已完成",
            payload={"summary": {"status": "completed"}},
        )

        exported = client.post(
            "/api/v1/tasks/log-archive/backup",
            json={"replace": False},
            headers=headers,
        )
        assert exported.status_code == 200
        assert exported.json()["backup"]["state"] == "READY"

        restored = client.post(
            "/api/v1/tasks/log-archive/restore-rehearsal",
            json={"target_suffix": "api-run-1"},
            headers=headers,
        )
        assert restored.status_code == 200
        body = restored.json()["restore"]
        assert body["state"] == "READY"
        assert body["target_root"].endswith("task-logs-api-run-1")
        assert body["backup"]["inventory_sha256"] == exported.json()["backup"]["inventory_sha256"]

        invalid = client.post(
            "/api/v1/tasks/log-archive/restore-rehearsal",
            json={"target_suffix": "../escape"},
            headers=headers,
        )
        assert invalid.status_code == 422
        runs = client.get("/api/v1/tasks/log-archive/runs", headers=headers)
        assert runs.status_code == 200
        assert any(
            item["operation"] == "restore_rehearsal" and item["state"] == "completed"
            for item in runs.json()["items"]
        )
    finally:
        app.state.history_jobs.close()


def test_dead_letter_replay_endpoint_is_authenticated_idempotent_and_audited() -> None:
    app = create_app(_settings())
    client = TestClient(app)
    headers = _headers(client)
    queue = ReplayQueue()
    app.state.task_dispatcher = TaskDispatcher(
        app.state.task_store,
        queue,
        mode="redis",
    )
    source_id = "research:dead-api"
    try:
        app.state.task_store.create(
            source_id,
            kind="research",
            title="失败研究",
            payload={"run_id": "original", "mode": "compare", "secret": "payload"},
            max_attempts=2,
        )
        app.state.task_store.dead_letter(
            source_id,
            error={"kind": "WORKER_FAILED", "message": "permanent"},
            attempt=2,
        )

        endpoint = f"/api/v1/tasks/dead-letters/{source_id}/replay"
        assert client.post(endpoint, json={"reason": "retry after recovery"}).status_code == 401
        first = client.post(
            endpoint,
            json={"reason": "retry after recovery", "request_id": "api-replay-1"},
            headers=headers,
        )
        repeated = client.post(
            endpoint,
            json={"reason": "different text is ignored for the same request", "request_id": "api-replay-1"},
            headers=headers,
        )

        assert first.status_code == 202
        assert first.json()["status"] == "queued"
        assert repeated.status_code == 202
        assert repeated.json()["deduplicated"] is True
        assert repeated.json()["replay_task_id"] == first.json()["replay_task_id"]
        assert len(queue.items) == 1
        assert client.post(
            f"/api/v1/tasks/{source_id}/retry",
            headers=headers,
        ).status_code == 422

        events = client.get(f"/api/v1/tasks/{source_id}/events", headers=headers)
        assert events.status_code == 200
        replay_event = events.json()["items"][0]
        assert replay_event["event_type"] == "dead_letter_replayed"
        assert replay_event["payload"]["actor"] == "admin"
        assert replay_event["payload"]["reason"] == "retry after recovery"
        assert "payload" not in replay_event["payload"]
        assert "secret" not in str(replay_event)
    finally:
        app.state.history_jobs.close()


def test_task_result_endpoint_reads_verified_archive_reference(tmp_path) -> None:
    app = create_app(_settings())
    client = TestClient(app)
    headers = _headers(client)
    task_id = "research:archive-api-1"
    result = {"status": "completed", "items": [{"dataset_id": "binance:spot:BTC/USDT:1d"}]}
    try:
        app.state.task_store.create(task_id, kind="research", title="归档结果")
        reference = app.state.task_result_archive.write(task_id, result)
        app.state.task_store.sync(task_id, status="completed", result={"archive": reference})
        app.state.task_store.append_event(
            task_id,
            "completed",
            status="completed",
            payload={"summary": {"status": "completed"}, "archive": reference},
        )

        assert client.get(f"/api/v1/tasks/{task_id}/result").status_code == 401
        response = client.get(f"/api/v1/tasks/{task_id}/result", headers=headers)

        assert response.status_code == 200
        assert response.json()["result"] == result
        assert response.json()["archive"] == reference
        events = client.get(f"/api/v1/tasks/{task_id}/events", headers=headers)
        assert events.status_code == 200
        assert events.json()["items"][0]["payload"] == {
            "summary": {"status": "completed"},
            "archive": reference,
        }
        assert "items" not in events.json()["items"][0]["payload"]
        logs = client.get(f"/api/v1/tasks/{task_id}/logs", headers=headers)
        assert logs.status_code == 200
        assert logs.json()["archive"]["state"] == "READY"
        assert logs.json()["items"][0]["event_type"] == "completed"
    finally:
        app.state.history_jobs.close()


def test_task_result_endpoint_preserves_legacy_history_archive_summary() -> None:
    app = create_app(_settings())
    client = TestClient(app)
    headers = _headers(client)
    task_id = "history:legacy-archive-api-1"
    legacy_archive = {
        "status": "READY",
        "archived_count": 18,
        "dataset_count": 18,
        "missing_count": 0,
    }
    result = {"completed": 1, "blocked": 0, "archive": legacy_archive}
    try:
        app.state.task_store.create(task_id, kind="history_download", title="历史数据下载")
        app.state.task_store.sync(task_id, status="completed", result=result)

        response = client.get(f"/api/v1/tasks/{task_id}/result", headers=headers)

        assert response.status_code == 200
        assert response.json()["result"] == result
        assert response.json()["archive"] is None
    finally:
        app.state.history_jobs.close()
