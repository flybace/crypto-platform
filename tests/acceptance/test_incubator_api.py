from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from application.history_download import HistoryDownloadService
from application.history_storage import HistoryStorage
from backend.app.main import create_app
from backend.app.settings import Settings
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


def settings() -> Settings:
    return Settings(
        app_name="Incubator API Test",
        version="test",
        admin_username="admin",
        admin_password="local-password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
    )


def seed_history(storage: HistoryStorage) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    query = HistoryQuery(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=start,
        end_at=start + timedelta(days=5),
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
                open=Decimal(str(price)),
                high=Decimal(str(price)),
                low=Decimal(str(price)),
                close=Decimal(str(price)),
                volume=Decimal("1"),
                quote_volume=Decimal(str(price)),
                trade_count=1,
            )
            for index, price in enumerate((100, 105, 110, 115, 120))
        ],
        source="test",
    )


def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "local-password"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_screening_candidates_can_be_written_to_incubator_and_read_only_catalog(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    seed_history(storage)
    app = create_app(settings(), history_service=HistoryDownloadService({}, storage))
    client = TestClient(app)
    headers = auth_headers(client)
    try:
        screening = client.post(
            "/api/v1/screening/run",
            json={"pool_id": "system.backtest.verified", "interval": "1d", "lookback": 5, "limit": 10},
            headers=headers,
        )
        assert screening.status_code == 201
        assert screening.json()["candidate_count"] == 1

        written = client.post(
            f"/api/v1/incubators/from-screen/{screening.json()['run_id']}",
            json={"mode": "append", "limit": 10},
            headers=headers,
        )
        assert written.status_code == 201
        body = written.json()
        assert body["pool"]["candidate_count"] == 1
        assert body["pool"]["research_only"] is True
        assert body["stats"] == {"added": 1, "updated": 0, "removed": 0}

        repeated = client.post(
            f"/api/v1/incubators/from-screen/{screening.json()['run_id']}",
            json={"mode": "append", "limit": 10},
            headers=headers,
        )
        assert repeated.status_code == 201
        assert repeated.json()["stats"] == {"added": 0, "updated": 1, "removed": 0}

        snapshot = client.get("/api/v1/incubators", headers=headers)
        assert snapshot.status_code == 200
        assert snapshot.json()["summary"]["incubator_items"] == 1
        assert snapshot.json()["recent_screens"][0]["already_in_incubator"] is True

        catalog = client.get("/api/v1/gace/capabilities", headers=headers)
        assert catalog.status_code == 200
        assert "strategy.incubators.read" in {item["id"] for item in catalog.json()["capabilities"]}
        assert catalog.json()["write_capabilities"] == []
    finally:
        app.state.history_jobs.close()
