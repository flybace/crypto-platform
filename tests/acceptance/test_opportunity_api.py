from fastapi.testclient import TestClient

from application.history_download import HistoryDownloadService
from application.history_storage import HistoryStorage
from backend.app.main import create_app
from backend.app.settings import Settings


def _settings() -> Settings:
    return Settings(
        app_name="Opportunity API Test",
        version="test",
        admin_username="admin",
        admin_password="local-password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
    )


def test_opportunity_scan_is_explicitly_recorded_and_read_route_stays_compatible(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPTO_MARKET_STATE_PATH", str(tmp_path / "market"))
    storage = HistoryStorage(tmp_path / "history")
    app = create_app(_settings(), history_service=HistoryDownloadService({}, storage))
    client = TestClient(app)
    try:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "local-password"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        recorded = client.post(
            "/api/v1/market/opportunities/scan",
            json={"symbol": "BTC/USDT", "buy_venue_id": "binance", "sell_venue_id": "bybit"},
            headers=headers,
        )
        assert recorded.status_code == 201
        assert recorded.json()["state"] == "BLOCKED"
        assert recorded.json()["record_id"].startswith("opportunity-")

        history = client.get("/api/v1/market/opportunities/history", headers=headers)
        assert history.status_code == 200
        assert history.json()["count"] == 1
        assert history.json()["summary"]["execution_eligible"] is False

        legacy = client.get(
            "/api/v1/market/opportunities",
            params={"symbol": "BTC/USDT", "buy_venue_id": "binance", "sell_venue_id": "bybit"},
            headers=headers,
        )
        assert legacy.status_code == 200
        assert legacy.json()["state"] == "BLOCKED"
        assert client.get("/api/v1/market/opportunities/summary", headers=headers).json()["record_count"] == 1
    finally:
        app.state.history_jobs.close()
