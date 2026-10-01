import importlib.util
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
        app_name="History Archive API Test",
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
        end_at=start + timedelta(days=1),
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
                open_time=start,
                close_time=start + timedelta(days=1) - timedelta(milliseconds=1),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100.5"),
                volume=Decimal("2"),
                quote_volume=Decimal("201"),
                trade_count=20,
            )
        ],
        source="test",
    )


def test_history_archive_api_reports_capability_and_result(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    _seed(storage)
    app = create_app(_settings(), history_service=HistoryDownloadService({}, storage))
    client = TestClient(app)
    try:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "local-password"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        status = client.get("/api/v1/history/archive", headers=headers)
        assert status.status_code == 200
        assert status.json()["dataset_count"] == 1
        assert "items" in status.json()

        archived = client.post("/api/v1/history/archive", headers=headers)
        if importlib.util.find_spec("pyarrow") is None:
            assert archived.status_code == 503
        else:
            assert archived.status_code == 200
            assert archived.json()["archived_count"] == 1
    finally:
        app.state.history_jobs.close()
