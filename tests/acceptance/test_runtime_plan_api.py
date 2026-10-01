from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from application.history_download import HistoryDownloadService
from application.history_storage import HistoryStorage
from backend.app.main import create_app
from backend.app.settings import Settings
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


def _settings() -> Settings:
    return Settings(
        app_name="Runtime Plan Test",
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
    candles = []
    for index in range(3):
        opened = start + timedelta(days=index)
        price = Decimal(100 + index)
        candles.append(Candle(
            venue_id="binance",
            market_type=MarketType.SPOT,
            instrument_key=query.instrument_key,
            native_symbol=query.native_symbol,
            interval=query.interval,
            open_time=opened,
            close_time=opened + timedelta(days=1) - timedelta(milliseconds=1),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=Decimal("2"),
            quote_volume=price * 2,
            trade_count=1,
        ))
    storage.upsert(query, candles, source="test")


def test_runtime_plan_and_gace_capabilities_are_authenticated_and_read_only(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    _seed(storage)
    app = create_app(_settings(), history_service=HistoryDownloadService({}, storage))
    client = TestClient(app)
    try:
        assert client.get("/api/v1/runtime/plan").status_code == 401
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "local-password"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        plan = client.get("/api/v1/runtime/plan", headers=headers)
        assert plan.status_code == 200
        body = plan.json()
        assert body["module"] == "runtime_plan"
        assert body["market_mode"] == "24/7 spot research"
        assert body["execution_mode"] == "DISABLED"
        assert body["status"] == "safe_paused"
        assert body["summary"]["verified_dataset_count"] == 1
        assert body["summary"]["row_count"] == 3
        assert {step["id"] for step in body["steps"]} == {"history", "strategies", "research", "paper", "risk", "live", "gace"}
        assert next(step for step in body["steps"] if step["id"] == "live")["status"] == "blocked"
        assert body["capabilities"]["read_only"] is True
        assert body["capabilities"]["write_count"] == 0

        catalog = client.get("/api/v1/gace/capabilities", headers=headers)
        assert catalog.status_code == 200
        catalog_body = catalog.json()
        assert catalog_body["read_only"] is True
        assert catalog_body["write_capabilities"] == []
        assert all(item["method"] == "GET" for item in catalog_body["capabilities"])
        assert {item["id"] for item in catalog_body["blocked_actions"]} >= {
            "execution.live_order.submit",
            "execution.withdraw.submit",
        }
    finally:
        app.state.history_jobs.close()
