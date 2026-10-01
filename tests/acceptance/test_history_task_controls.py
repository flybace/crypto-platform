import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from application.history_download import HistoryDownloadService
from application.history_storage import HistoryStorage
from backend.app.main import create_app
from backend.app.settings import Settings
from domain.candle import Candle


class BlockingHistoryGateway:
    venue_id = "binance"
    source = "test-blocking-history"

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def fetch_candles(self, query):
        self.started.set()
        self.release.wait(timeout=3)
        return (
            Candle(
                venue_id=query.venue_id,
                market_type=query.market_type,
                instrument_key=query.instrument_key,
                native_symbol=query.native_symbol,
                interval=query.interval,
                open_time=query.start_at,
                close_time=query.start_at + timedelta(days=1) - timedelta(milliseconds=1),
                open=Decimal("100"),
                high=Decimal("110"),
                low=Decimal("90"),
                close=Decimal("105"),
                volume=Decimal("2"),
                quote_volume=Decimal("210"),
                trade_count=20,
            ),
        )


def settings() -> Settings:
    return Settings(
        app_name="History Task Controls Test",
        version="test",
        admin_username="admin",
        admin_password="local-password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
    )


def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "local-password"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def wait_for_status(client: TestClient, path: str, headers: dict[str, str], expected: str, timeout: float = 3) -> dict:
    deadline = time.monotonic() + timeout
    body = {}
    while time.monotonic() < deadline:
        body = client.get(path, headers=headers).json()
        if body.get("status") == expected:
            return body
        time.sleep(0.01)
    return body


def test_history_tasks_deduplicate_cancel_show_detail_and_retry(tmp_path) -> None:
    gateway = BlockingHistoryGateway()
    service = HistoryDownloadService({"binance": gateway}, HistoryStorage(tmp_path / "history"))
    app = create_app(settings(), history_service=service)
    client = TestClient(app)
    headers = auth_headers(client)
    payload = {
        "venue_ids": ["binance"],
        "symbols": ["BTC/USDT", "ETH/USDT"],
        "interval": "1d",
        "start_at": "2026-01-01T00:00:00Z",
        "end_at": "2026-01-03T00:00:00Z",
    }
    try:
        first = client.post("/api/v1/history/jobs", json=payload, headers=headers)
        assert first.status_code == 202
        job_id = first.json()["job_id"]
        assert gateway.started.wait(timeout=2)

        duplicate = client.post("/api/v1/history/jobs", json=payload, headers=headers)
        assert duplicate.status_code == 202
        assert duplicate.json()["job_id"] == job_id
        assert duplicate.json()["deduplicated"] is True

        detail = client.get(f"/api/v1/tasks/history:{job_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["detail"]["job_id"] == job_id
        assert detail.json()["task"]["kind"] == "history_download"
        assert detail.json()["ledger"]["task_id"] == f"history:{job_id}"
        assert detail.json()["events"]

        cancelled = client.post(f"/api/v1/tasks/history:{job_id}/cancel", headers=headers)
        assert cancelled.status_code == 200
        assert cancelled.json()["detail"]["status"] == "cancelling"
        gateway.release.set()
        finished = wait_for_status(client, f"/api/v1/history/jobs/{job_id}", headers, "cancelled")
        assert finished["completed"] == 1
        assert finished["cancelled"] == 1
        assert finished["items"][0]["status"] == "completed"
        assert finished["items"][1]["status"] == "cancelled"
        events = client.get(f"/api/v1/tasks/history:{job_id}/events", headers=headers)
        assert events.status_code == 200
        assert any(item["status"] == "cancelled" for item in events.json()["items"])

        retry = client.post(f"/api/v1/tasks/history:{job_id}/retry", headers=headers)
        assert retry.status_code == 202
        retry_id = retry.json()["detail"]["job_id"]
        retried = wait_for_status(client, f"/api/v1/history/jobs/{retry_id}", headers, "completed")
        assert retried["items"][0]["status"] == "completed"
        assert client.get("/api/v1/tasks/summary", headers=headers).json()["active"] == 0
    finally:
        gateway.release.set()
        app.state.history_jobs.close()
