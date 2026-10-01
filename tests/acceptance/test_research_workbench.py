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
        app_name="Research Workbench Test",
        version="test",
        admin_username="admin",
        admin_password="local-password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
    )


def _seed(storage: HistoryStorage, count: int = 40) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    query = HistoryQuery(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=start,
        end_at=start + timedelta(days=count),
    )
    candles = []
    for index in range(count):
        opened = start + timedelta(days=index)
        price = Decimal(100 + (index % 5) * 3 + index // 5)
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


def test_research_routes_provide_portfolio_compare_tune_and_strategy_paper_replay(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    _seed(storage)
    app = create_app(_settings(), history_service=HistoryDownloadService({}, storage))
    client = TestClient(app)
    try:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "local-password"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        strategies = client.get("/api/v1/strategies/catalog", headers=headers)
        assert strategies.status_code == 200
        assert strategies.json()["count"] >= 10

        portfolio = client.post(
            "/api/v1/research/portfolio",
            json={"pool_id": "system.backtest.verified", "interval": "1d", "strategy_id": "buy_and_hold", "max_datasets": 2, "fee_bps": "0", "slippage_bps": "0"},
            headers=headers,
        )
        assert portfolio.status_code == 201
        assert portfolio.json()["dataset_count"] == 1
        assert portfolio.json()["kind"] == "portfolio"
        assert portfolio.json()["candle_count"] > 0
        assert Decimal(portfolio.json()["total_return_pct"]).is_finite()

        screening = client.post(
            "/api/v1/screening/run",
            json={"pool_id": "system.backtest.verified", "interval": "1d", "lookback": 30, "limit": 10},
            headers=headers,
        )
        assert screening.status_code == 201
        screened_portfolio = client.post(
            "/api/v1/research/portfolio",
            json={
                "pool_id": "system.backtest.verified",
                "screening_run_id": screening.json()["run_id"],
                "interval": "1d",
                "strategy_id": "buy_and_hold",
                "max_datasets": 2,
                "fee_bps": "0",
                "slippage_bps": "0",
            },
            headers=headers,
        )
        assert screened_portfolio.status_code == 201
        assert screened_portfolio.json()["screening_run_id"] == screening.json()["run_id"]

        compare = client.post(
            "/api/v1/research/compare",
            json={"venue_id": "binance", "symbol": "BTC/USDT", "interval": "1d", "strategy_ids": ["buy_and_hold", "trend_breakout"], "fee_bps": "0", "slippage_bps": "0", "strategy_parameters": {"trend_breakout": {"window": 3}}},
            headers=headers,
        )
        assert compare.status_code == 201
        assert len(compare.json()["items"]) == 2

        tune = client.post(
            "/api/v1/research/tune",
            json={"venue_id": "binance", "symbol": "BTC/USDT", "interval": "1d", "strategy_id": "trend_breakout", "parameter_grid": {"window": [2, 3]}, "max_runs": 4, "fee_bps": "0", "slippage_bps": "0"},
            headers=headers,
        )
        assert tune.status_code == 201
        assert tune.json()["trial_count"] == 2

        paper = client.post(
            "/api/v1/paper/strategy-runs",
            json={"venue_id": "binance", "symbol": "BTC/USDT", "interval": "1d", "strategy_id": "trend_breakout", "strategy_parameters": {"window": 3}, "fee_bps": "0", "slippage_bps": "0"},
            headers=headers,
        )
        assert paper.status_code == 201
        assert paper.json()["mode"] == "PAPER_REPLAY"

        tasks = client.get("/api/v1/tasks", params={"limit": 100}, headers=headers)
        assert tasks.status_code == 200
        task_ids = {item["task_id"] for item in tasks.json()["items"]}
        assert f"screening:{screening.json()['run_id']}" in task_ids
        assert f"research:{portfolio.json()['run_id']}" in task_ids
        assert f"paper-strategy:{paper.json()['run_id']}" in task_ids
        events = client.get(
            f"/api/v1/tasks/research:{portfolio.json()['run_id']}/events",
            headers=headers,
        )
        assert events.status_code == 200
        assert events.json()["items"][0]["event_type"] == "completed"
        task_detail = client.get(
            f"/api/v1/tasks/research:{portfolio.json()['run_id']}",
            headers=headers,
        )
        assert task_detail.status_code == 200
        assert task_detail.json()["task"]["retryable"] is False
        assert task_detail.json()["detail"]["run_id"] == portfolio.json()["run_id"]
        assert task_detail.json()["detail"]["kind"] == "portfolio"
    finally:
        app.state.history_jobs.close()
