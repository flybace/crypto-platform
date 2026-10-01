from datetime import UTC, datetime, timedelta
from decimal import Decimal

from application.candle_backtest import CandleBacktestConfig
from application.history_storage import HistoryStorage
from application.portfolio_backtest import PortfolioBacktestRunner
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


def _dataset(storage: HistoryStorage, venue: str, prices: list[str]):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    query = HistoryQuery(
        venue_id=venue,
        market_type=MarketType.SPOT,
        instrument_key=f"{venue}:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=start,
        end_at=start + timedelta(days=len(prices)),
    )
    candles = []
    for index, price in enumerate(prices):
        opened = start + timedelta(days=index)
        candles.append(Candle(
            venue_id=venue,
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
            volume=Decimal("1"),
            quote_volume=Decimal(price),
            trade_count=1,
        ))
    storage.upsert(query, candles, source="test")


def test_portfolio_runner_allocates_capital_and_aggregates_metrics(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    _dataset(storage, "binance", ["100", "110", "120", "130"])
    _dataset(storage, "bybit", ["100", "90", "80", "70"])
    runner = PortfolioBacktestRunner(storage)
    result = runner.run(
        storage.list_datasets(),
        config=CandleBacktestConfig(strategy_id="buy_and_hold", initial_quote=1000, fee_bps=0, slippage_bps=0),
        max_datasets=2,
    )

    assert result["dataset_count"] == 2
    assert Decimal(str(result["initial_equity"])) == Decimal("1000")
    assert result["orders"] == 4
    assert len(result["items"]) == 2
    assert result["equity_curve"]
