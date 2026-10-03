from datetime import UTC, datetime, timedelta
from decimal import Decimal

from application.candle_backtest import CandleBacktestConfig
from application.history_storage import HistoryStorage
from application.pool_catalog import PoolCatalog
from application.screening import ScreeningService
from backend.app.services.backtest_runs import BacktestRunManager
from backend.app.services.paper_trading import PaperTradingService
from backend.app.services.risk_policy import RiskPolicyService
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


def _seed(
    storage: HistoryStorage,
    interval: CandleInterval,
    count: int = 5,
    *,
    venue_id: str = "binance",
) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    step = timedelta(days=1) if interval is CandleInterval.DAY else timedelta(hours=1)
    query = HistoryQuery(
        venue_id=venue_id,
        market_type=MarketType.SPOT,
        instrument_key=f"{venue_id}:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=interval,
        start_at=start,
        end_at=start + step * (count + 1),
    )
    candles = []
    for index in range(count):
        opened = start + step * index
        price = Decimal(100 + index)
        candles.append(
            Candle(
                venue_id=query.venue_id,
                market_type=query.market_type,
                instrument_key=query.instrument_key,
                native_symbol=query.native_symbol,
                interval=query.interval,
                open_time=opened,
                close_time=opened + step - timedelta(milliseconds=1),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("2"),
                quote_volume=price * 2,
                trade_count=1,
            )
        )
    storage.upsert(query, candles, source="test")


def test_research_records_and_custom_pool_restore(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    _seed(storage, CandleInterval.DAY)
    pool_state = tmp_path / "pools.json"
    pools = PoolCatalog(storage, state_path=pool_state)
    created = pools.create(
        name="持久化测试池",
        kind="backtest",
        venue_ids=["binance"],
        symbols=["BTC/USDT"],
        interval="1d",
    )
    restored_pools = PoolCatalog(storage, state_path=pool_state)
    assert restored_pools.get(created["pool_id"])["name"] == "持久化测试池"

    backtest_state = tmp_path / "backtests.json"
    manager = BacktestRunManager(storage, state_path=backtest_state)
    run = manager.run(
        venue_id="binance",
        symbol="BTC/USDT",
        interval="1d",
        config=CandleBacktestConfig(
            strategy_id="buy_and_hold",
            initial_quote=Decimal("1000"),
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            fast_window=2,
            slow_window=3,
        ),
    )
    assert BacktestRunManager(storage, state_path=backtest_state).get(run["run_id"])["status"] == "completed"

    screening_state = tmp_path / "screening.json"
    screening = ScreeningService(storage, restored_pools, state_path=screening_state)
    result = screening.run(
        pool_id=created["pool_id"],
        interval="1d",
        lookback=5,
        min_return_pct=Decimal("0"),
        max_volatility_pct=None,
        min_quote_volume=Decimal("0"),
        min_momentum_pct=Decimal("0"),
        limit=10,
    )
    restored_screening = ScreeningService(storage, restored_pools, state_path=screening_state)
    assert restored_screening.get(result["run_id"])["candidate_count"] == result["candidate_count"]


def test_paper_account_and_order_id_restore(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    _seed(storage, CandleInterval.HOUR)
    state_path = tmp_path / "paper.json"
    service = PaperTradingService(storage, state_path=state_path)
    first = service.submit(
        venue_id="binance",
        symbol="BTC/USDT",
        interval="1h",
        side="BUY",
        quantity=Decimal("0.01"),
        limit_price=Decimal("50000"),
        request_id="paper-request-1",
    )
    restored = PaperTradingService(storage, state_path=state_path)
    assert restored.orders()[0]["order_id"] == first["order_id"]
    # 默认纯 USDT 10000，买入 0.01 BTC @50000 花掉 500 USDT
    assert restored.summary()["balances"]["BTC"] == "0.01"
    second = restored.submit(
        venue_id="binance",
        symbol="BTC/USDT",
        interval="1h",
        side="BUY",
        quantity=Decimal("0.01"),
        limit_price=Decimal("50000"),
        request_id="paper-request-2",
    )
    assert second["order_id"] == "paper-000002"


def test_paper_accounts_are_isolated_and_order_ids_are_namespaced(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    _seed(storage, CandleInterval.HOUR, venue_id="binance")
    _seed(storage, CandleInterval.HOUR, venue_id="bybit")
    state_path = tmp_path / "paper.json"
    service = PaperTradingService(storage, state_path=state_path)

    binance_order = service.submit(
        venue_id="binance",
        symbol="BTC/USDT",
        interval="1h",
        side="BUY",
        quantity=Decimal("0.01"),
        limit_price=Decimal("50000"),
        request_id="binance-request",
    )
    bybit_order = service.submit(
        venue_id="bybit",
        symbol="BTC/USDT",
        interval="1h",
        side="BUY",
        quantity=Decimal("0.01"),
        limit_price=Decimal("50000"),
        request_id="bybit-request",
    )

    assert binance_order["order_id"] == "paper-000001"
    assert bybit_order["order_id"] == "paper-bybit-000001"
    assert binance_order["order_id"] != bybit_order["order_id"]

    summary = service.summary()
    accounts = {item["venue_id"]: item for item in summary["accounts"]}
    assert set(accounts) == {"binance", "okx", "bybit"}
    assert accounts["binance"]["balances"]["BTC"] == "0.01"
    assert accounts["bybit"]["balances"]["BTC"] == "0.01"
    assert summary["balances"]["BTC"] == "0.01"
    assert summary["aggregate_balances"]["BTC"] == "0.02"

    restored = PaperTradingService(storage, state_path=state_path)
    restored_accounts = {item["venue_id"]: item for item in restored.summary()["accounts"]}
    assert restored_accounts["binance"]["balances"]["BTC"] == "0.01"
    assert restored_accounts["bybit"]["balances"]["BTC"] == "0.01"
    assert restored_accounts["okx"]["balances"].get("BTC", "0") == "0"

    restored.reset(venue_id="bybit")
    after_reset = {item["venue_id"]: item for item in restored.summary()["accounts"]}
    assert after_reset["bybit"]["balances"] == {"USDT": "10000"}
    assert after_reset["binance"]["balances"]["BTC"] == "0.01"
    assert all(item["venue_id"] != "bybit" for item in restored.orders())


def test_risk_policy_and_events_restore(tmp_path) -> None:
    state_path = tmp_path / "risk.json"
    service = RiskPolicyService(state_path=state_path)
    service.update(
        {
            "enabled": True,
            "mode": "SELL_ONLY",
            "venue_allowlist": ["binance"],
            "instrument_allowlist": ["binance:spot:BTC/USDT"],
            "max_order_notional_quote": "100",
            "max_daily_sell_notional_quote": "500",
            "max_slippage_bps": "25",
        }
    )
    restored = RiskPolicyService(state_path=state_path)
    assert restored.policy()["mode"] == "SELL_ONLY"
    assert restored.policy()["max_order_notional_quote"] == "100"
    assert restored.events()[0]["event_type"] == "POLICY_UPDATED"
