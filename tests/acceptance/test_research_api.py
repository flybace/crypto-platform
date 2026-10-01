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
        app_name="Research API Test",
        version="test",
        admin_username="admin",
        admin_password="local-password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
    )


def _seed(storage: HistoryStorage, venue: str, symbol: str, interval: CandleInterval) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    step = timedelta(days=1) if interval is CandleInterval.DAY else timedelta(hours=1)
    query = HistoryQuery(
        venue_id=venue,
        market_type=MarketType.SPOT,
        instrument_key=f"{venue}:spot:{symbol}",
        native_symbol=symbol.replace("/", ""),
        interval=interval,
        start_at=start,
        end_at=start + step * 6,
    )
    candles = []
    for index in range(5):
        opened = start + step * index
        price = Decimal(100 + index)
        candles.append(Candle(
            venue_id=venue,
            market_type=MarketType.SPOT,
            instrument_key=query.instrument_key,
            native_symbol=query.native_symbol,
            interval=interval,
            open_time=opened,
            close_time=opened + step - timedelta(milliseconds=1),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=Decimal("2"),
            quote_volume=price * 2,
            trade_count=1,
        ))
    storage.upsert(query, candles, source="test")


def test_pool_screening_paper_risk_and_tasks_are_connected(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    _seed(storage, "binance", "BTC/USDT", CandleInterval.DAY)
    _seed(storage, "binance", "BTC/USDT", CandleInterval.HOUR)
    app = create_app(_settings(), history_service=HistoryDownloadService({}, storage))
    client = TestClient(app)
    try:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "local-password"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        pools = client.get("/api/v1/pools", headers=headers)
        assert pools.status_code == 200
        assert {item["kind"] for item in pools.json()["items"]} == {"research", "backtest", "paper"}

        comparison = client.get("/api/v1/market/compare?symbol=BTC/USDT&interval=1d", headers=headers)
        assert comparison.status_code == 200
        assert comparison.json()["comparison"] is None

        screening = client.post(
            "/api/v1/screening/run",
            json={"pool_id": "system.backtest.verified", "interval": "1d", "lookback": 5},
            headers=headers,
        )
        assert screening.status_code == 201
        assert screening.json()["candidate_count"] == 1

        batch = client.post(
            "/api/v1/backtests/pool",
            json={"pool_id": "system.backtest.verified", "interval": "1d", "strategy_id": "buy_and_hold", "max_datasets": 5},
            headers=headers,
        )
        assert batch.status_code == 201
        assert batch.json()["count"] == 1
        assert batch.json()["items"][0]["status"] == "completed"

        paper = client.post(
            "/api/v1/paper/orders",
            json={"venue_id": "binance", "symbol": "BTC/USDT", "interval": "1h", "side": "SELL", "quantity": "0.01"},
            headers=headers,
        )
        assert paper.status_code == 201
        assert paper.json()["status"] == "FILLED"

        risk = client.get("/api/v1/risk/summary", headers=headers)
        assert risk.status_code == 200
        assert risk.json()["real_orders_allowed"] is False
        assert "EXECUTION_MODE_DISABLED" in risk.json()["blocking_reasons"]

        tasks = client.get("/api/v1/tasks", headers=headers)
        assert tasks.status_code == 200
        assert {item["kind"] for item in tasks.json()["items"]} >= {"screening", "paper_order"}
    finally:
        app.state.history_jobs.close()
