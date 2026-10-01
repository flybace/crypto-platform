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
        app_name="Spread API Test",
        version="test",
        admin_username="admin",
        admin_password="local-password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
    )


def _seed(storage: HistoryStorage, venue_id: str, values: tuple[str, ...], *, gap: bool = False) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    step = timedelta(minutes=5)
    query = HistoryQuery(
        venue_id=venue_id,
        market_type=MarketType.SPOT,
        instrument_key=f"{venue_id}:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.FIVE_MINUTE,
        start_at=start,
        end_at=start + step * (len(values) + 2),
    )
    candles = []
    for index, raw in enumerate(values):
        opened = start + step * (index + (1 if gap and index > 0 else 0))
        close = Decimal(raw)
        candles.append(
            Candle(
                venue_id=venue_id,
                market_type=MarketType.SPOT,
                instrument_key=query.instrument_key,
                native_symbol="BTCUSDT",
                interval=CandleInterval.FIVE_MINUTE,
                open_time=opened,
                close_time=opened + step - timedelta(milliseconds=1),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=Decimal("1"),
                quote_volume=close,
                trade_count=1,
            )
        )
    storage.upsert(query, candles, source="test")


def _headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "local-password"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_spread_history_api_returns_research_result_with_costs(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    _seed(storage, "binance", ("100", "100", "100"))
    _seed(storage, "bybit", ("101", "99", "100"))
    app = create_app(_settings(), history_service=HistoryDownloadService({}, storage))
    client = TestClient(app)
    try:
        response = client.get(
            "/api/v1/market/spread-history",
            params={
                "symbol": "BTC/USDT",
                "buy_venue_id": "binance",
                "sell_venue_id": "bybit",
                "interval": "5m",
                "fee_bps": "10",
                "slippage_bps": "5",
                "start_at": "2026-01-01T00:00:00Z",
                "end_at": "2026-01-01T00:20:00Z",
            },
            headers=_headers(client),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "historical_spread_research"
        assert body["research_only"] is True
        assert body["aligned_candle_count"] == 3
        assert body["cost_model"]["total_cost_bps"] == "30"
        assert body["top_observations"]
    finally:
        app.state.history_jobs.close()


def test_spread_history_api_blocks_missing_or_bad_history(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    _seed(storage, "binance", ("100", "100", "100"))
    app = create_app(_settings(), history_service=HistoryDownloadService({}, storage))
    client = TestClient(app)
    try:
        missing = client.get(
            "/api/v1/market/spread-history",
            params={"interval": "5m", "start_at": "2026-01-01T00:00:00Z", "end_at": "2026-01-01T00:20:00Z"},
            headers=_headers(client),
        )
        assert missing.status_code == 422
        assert "dataset was not found" in missing.json()["detail"]

        _seed(storage, "bybit", ("101", "99", "100"), gap=True)
        blocked = client.get(
            "/api/v1/market/spread-history",
            params={"interval": "5m", "start_at": "2026-01-01T00:00:00Z", "end_at": "2026-01-01T00:20:00Z"},
            headers=_headers(client),
        )
        assert blocked.status_code == 422
        assert "quality gate" in blocked.json()["detail"]
    finally:
        app.state.history_jobs.close()
