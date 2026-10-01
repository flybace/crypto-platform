from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from application.history_download import HistoryDownloadService
from application.history_storage import HistoryStorage
from backend.app.main import create_app
from backend.app.settings import Settings
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


def make_settings() -> Settings:
    return Settings(
        app_name="Backtest API Test",
        version="test",
        admin_username="admin",
        admin_password="local-password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
    )


def make_dataset(storage: HistoryStorage) -> None:
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
    for index, price in enumerate(("100", "110", "120")):
        opened = start + timedelta(days=index)
        candles.append(
            Candle(
                venue_id=query.venue_id,
                market_type=query.market_type,
                instrument_key=query.instrument_key,
                native_symbol=query.native_symbol,
                interval=query.interval,
                open_time=opened,
                close_time=opened + timedelta(days=1) - timedelta(milliseconds=1),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("1"),
                quote_volume=Decimal(price),
                trade_count=1,
            )
        )
    storage.upsert(query, candles, source="test")


def test_backtest_api_consumes_verified_history_and_archives_result(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    make_dataset(storage)
    app = create_app(make_settings(), history_service=HistoryDownloadService({}, storage))
    client = TestClient(app)
    try:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "local-password"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = client.post(
            "/api/v1/backtests/run",
            json={
                "venue_id": "binance",
                "symbol": "BTC/USDT",
                "interval": "1d",
                "strategy_id": "buy_and_hold",
                "initial_quote": "1000",
                "fee_bps": "0",
                "slippage_bps": "0",
            },
            headers=headers,
        )
        assert response.status_code == 201
        result = response.json()
        assert result["candle_count"] == 3
        assert result["final_equity"] == "1090.909090909090909090909091"
        assert client.get(f"/api/v1/backtests/runs/{result['run_id']}", headers=headers).status_code == 200
    finally:
        app.state.history_jobs.close()
