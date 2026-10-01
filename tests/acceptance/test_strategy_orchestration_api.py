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
        app_name="Strategy Orchestration Test",
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
    prices = ("100", "110", "105", "120")
    storage.upsert(
        query,
        [
            Candle(
                venue_id=query.venue_id,
                market_type=query.market_type,
                instrument_key=query.instrument_key,
                native_symbol=query.native_symbol,
                interval=query.interval,
                open_time=start + timedelta(days=index),
                close_time=start + timedelta(days=index + 1) - timedelta(milliseconds=1),
                open=Decimal(price),
                high=Decimal(price),
                low=Decimal(price),
                close=Decimal(price),
                volume=Decimal("1"),
                quote_volume=Decimal(price),
                trade_count=1,
            )
            for index, price in enumerate(prices)
        ],
        source="test",
    )


def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "local-password"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_strategy_packages_are_metadata_only_and_matrix_runs_on_verified_history(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    seed_history(storage)
    app = create_app(settings(), history_service=HistoryDownloadService({}, storage))
    client = TestClient(app)
    try:
        headers = auth_headers(client)
        packages = client.get("/api/v1/strategies/packages/summary", headers=headers)
        assert packages.status_code == 200
        assert packages.json()["builtin_count"] >= 10

        registered = client.post(
            "/api/v1/strategies/packages",
            headers=headers,
            json={
                "package_id": "local-baseline",
                "name": "本地基线",
                "version": "0.1.0",
                "strategy_ids": ["buy_and_hold", "sma_cross"],
            },
        )
        assert registered.status_code == 201
        assert registered.json()["runnable"] is False
        assert registered.json()["status"] == "draft"

        dataset_id = client.get("/api/v1/history/coverage", headers=headers).json()["datasets"][0]["dataset_id"]
        created = client.post(
            "/api/v1/strategies/matrices",
            headers=headers,
            json={
                "name": "基线矩阵",
                "strategy_ids": ["buy_and_hold", "sma_cross"],
                "dataset_ids": [dataset_id],
                "interval": "1d",
                "initial_quote": "1000",
                "fee_bps": "0",
                "slippage_bps": "0",
                "fast_window": 2,
                "slow_window": 3,
            },
        )
        assert created.status_code == 201
        matrix = created.json()
        run = client.post(f"/api/v1/strategies/matrices/{matrix['matrix_id']}/run", headers=headers)
        assert run.status_code == 201
        body = run.json()
        assert body["run"]["status"] == "completed"
        assert body["run"]["completed_count"] == 2
        assert body["run"]["research_only"] is True
        assert body["matrix"]["last_run"]["run_id"] == body["run"]["run_id"]
    finally:
        app.state.history_jobs.close()


def test_strategy_orchestration_requires_authentication(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    app = create_app(settings(), history_service=HistoryDownloadService({}, storage))
    client = TestClient(app)
    try:
        assert client.get("/api/v1/strategies/packages").status_code == 401
        assert client.get("/api/v1/strategies/matrices/summary").status_code == 401
    finally:
        app.state.history_jobs.close()
