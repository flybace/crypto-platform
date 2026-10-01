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
        app_name="Management API Test",
        version="test",
        admin_username="admin",
        admin_password="local-password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
    )


def seed_dataset(storage: HistoryStorage) -> None:
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
                open=str(price),
                high=str(price),
                low=str(price),
                close=str(price),
                volume=Decimal("1"),
                quote_volume=Decimal(str(price)),
                trade_count=1,
            )
            for index, price in enumerate((100, 110, 120))
        ],
        source="test",
    )


def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "local-password"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_strategy_management_and_backtest_archive_controls(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    seed_dataset(storage)
    app = create_app(make_settings(), history_service=HistoryDownloadService({}, storage))
    client = TestClient(app)
    headers = auth_headers(client)
    try:
        management = client.get("/api/v1/strategies/management", headers=headers)
        assert management.status_code == 200
        disabled = client.patch(
            "/api/v1/strategies/management/buy_and_hold",
            json={"enabled": False},
            headers=headers,
        )
        assert disabled.status_code == 200
        rejected = client.post(
            "/api/v1/backtests/run",
            json={"venue_id": "binance", "symbol": "BTC/USDT", "interval": "1d", "strategy_id": "buy_and_hold"},
            headers=headers,
        )
        assert rejected.status_code == 422

        client.post(
            "/api/v1/strategies/management/buy_and_hold/reset",
            headers=headers,
        )
        run = client.post(
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
        assert run.status_code == 201
        run_id = run.json()["run_id"]
        assert client.get("/api/v1/backtests/summary", headers=headers).json()["completed_count"] == 1
        assert client.get("/api/v1/backtests/best", headers=headers).json()["count"] == 1
        assert client.delete(f"/api/v1/backtests/runs/{run_id}", headers=headers).status_code == 200
    finally:
        app.state.history_jobs.close()


def test_paper_automation_is_explicitly_enabled_and_replay_only(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    seed_dataset(storage)
    app = create_app(make_settings(), history_service=HistoryDownloadService({}, storage))
    client = TestClient(app)
    headers = auth_headers(client)
    try:
        blocked = client.post("/api/v1/paper/automation/run", headers=headers)
        assert blocked.status_code == 422
        configured = client.put(
            "/api/v1/paper/automation",
            json={
                "enabled": True,
                "venue_id": "binance",
                "symbol": "BTC/USDT",
                "interval": "1d",
                "strategy_id": "buy_and_hold",
            },
            headers=headers,
        )
        assert configured.status_code == 200
        result = client.post("/api/v1/paper/automation/run", headers=headers)
        assert result.status_code == 201
        assert result.json()["run"]["mode"] == "PAPER_REPLAY"
        assert result.json()["automation"]["last_run_id"] == result.json()["run"]["run_id"]
    finally:
        app.state.history_jobs.close()
