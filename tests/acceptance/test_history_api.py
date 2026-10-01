import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from application.history_download import HistoryDownloadService
from application.history_storage import HistoryStorage
from backend.app.main import create_app
from backend.app.settings import Settings
from domain.candle import Candle, CandleInterval


class FakeHistoryGateway:
    venue_id = "binance"
    source = "fake-public-history"

    def fetch_candles(self, query):
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
        app_name="History API Test",
        version="test",
        admin_username="admin",
        admin_password="local-password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
    )


def test_history_api_runs_authenticated_download_and_exposes_manifest(tmp_path) -> None:
    service = HistoryDownloadService({"binance": FakeHistoryGateway()}, HistoryStorage(tmp_path))
    app = create_app(settings(), history_service=service)
    client = TestClient(app)
    try:
        assert client.get("/api/v1/history/coverage").status_code == 401
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "local-password"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        scheduler_status = client.get("/api/v1/history/sync/status", headers=headers)
        assert scheduler_status.status_code == 200
        assert scheduler_status.json()["automatic"] is True
        assert scheduler_status.json()["status"] == "not_started"
        payload = {
            "venue_ids": ["binance"],
            "symbols": ["BTC/USDT"],
            "interval": "1d",
            "start_at": "2026-01-01T00:00:00Z",
            "end_at": "2026-01-03T00:00:00Z",
        }
        submitted = client.post("/api/v1/history/jobs", json=payload, headers=headers)
        assert submitted.status_code == 202
        job_id = submitted.json()["job_id"]

        # The job runs on a background thread; allow normal CI/Windows startup
        # latency while keeping the acceptance test bounded.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = client.get(f"/api/v1/history/jobs/{job_id}", headers=headers).json()
            if job["status"] == "completed":
                break
            time.sleep(0.01)
        assert job["status"] == "completed"
        assert job["items"][0]["dataset"]["row_count"] == 1

        datasets = client.get("/api/v1/history/datasets", headers=headers)
        coverage = client.get("/api/v1/history/coverage", headers=headers)
        assert datasets.status_code == 200
        assert datasets.json()["count"] == 1
        assert coverage.json()["row_count"] == 1
        assert coverage.json()["metadata"]["status"] == "ready"
        assert coverage.json()["metadata"]["dataset_count"] == 1
        sync_plan = client.get(
            "/api/v1/history/sync/plan?venue_ids=binance&symbols=BTC%2FUSDT&intervals=1d",
            headers=headers,
        )
        assert sync_plan.status_code == 200
        assert sync_plan.json()["verified_dataset_count"] == 1
        assert sync_plan.json()["verified_row_count"] == 1
        assert sync_plan.json()["missing_count"] == 0
        assert sync_plan.json()["quality_blocked_count"] == 0
        assert sync_plan.json()["status"] == "ACTIONABLE"
        candles = client.get(
            "/api/v1/history/candles?venue_id=binance&symbol=BTC/USDT&interval=1d",
            headers=headers,
        )
        assert candles.status_code == 200
        assert candles.json()["count"] == 1
        assert candles.json()["items"][0]["close"] == "105"
    finally:
        app.state.history_jobs.close()
