from datetime import UTC, datetime, timedelta
from decimal import Decimal

from application.history_storage import HistoryStorage
from application.pool_catalog import PoolCatalog
from application.screening import ScreeningService
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


def test_screening_returns_metrics_and_applies_thresholds(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
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
    for index, price in enumerate((100, 105, 110, 115)):
        opened = start + timedelta(days=index)
        value = Decimal(price)
        candles.append(Candle(
            venue_id="binance",
            market_type=MarketType.SPOT,
            instrument_key=query.instrument_key,
            native_symbol=query.native_symbol,
            interval=CandleInterval.DAY,
            open_time=opened,
            close_time=opened + timedelta(days=1) - timedelta(milliseconds=1),
            open=value,
            high=value,
            low=value,
            close=value,
            volume=Decimal("1"),
            quote_volume=value,
            trade_count=1,
        ))
    storage.upsert(query, candles, source="test")
    pools = PoolCatalog(storage)
    service = ScreeningService(storage, pools)

    result = service.run(
        pool_id="system.backtest.verified",
        interval="1d",
        lookback=4,
        min_return_pct=Decimal("10"),
        max_volatility_pct=None,
        min_quote_volume=Decimal("0"),
        min_momentum_pct=Decimal("0"),
        limit=10,
    )

    assert result["candidate_count"] == 1
    item = result["items"][0]
    assert item["passed"] is True
    assert Decimal(item["period_return_pct"]) == Decimal("15")
    assert Decimal(item["max_drawdown_pct"]) == Decimal("0")

