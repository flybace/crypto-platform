from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from application.history_storage import HistoryStorage
from application.spread_research import HistoricalSpreadResearch, SpreadResearchError
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


def _seed(
    storage: HistoryStorage,
    venue_id: str,
    prices: tuple[str, ...],
    *,
    interval: CandleInterval = CandleInterval.FIVE_MINUTE,
    gap_after: int | None = None,
) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    step = timedelta(milliseconds=interval.milliseconds)
    query = HistoryQuery(
        venue_id=venue_id,
        market_type=MarketType.SPOT,
        instrument_key=f"{venue_id}:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=interval,
        start_at=start,
        end_at=start + step * (len(prices) + 1),
    )
    candles = []
    for index, value in enumerate(prices):
        opened = start + step * (index + (1 if gap_after is not None and index > gap_after else 0))
        close = Decimal(value)
        candles.append(
            Candle(
                venue_id=venue_id,
                market_type=MarketType.SPOT,
                instrument_key=query.instrument_key,
                native_symbol=query.native_symbol,
                interval=interval,
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


def test_historical_spread_aligns_candles_and_deducts_both_legs(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    _seed(storage, "binance", ("100", "100", "100"))
    _seed(storage, "bybit", ("101", "99", "100"))

    result = HistoricalSpreadResearch(storage).run(
        symbol="BTC/USDT",
        buy_venue_id="binance",
        sell_venue_id="bybit",
        interval="5m",
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        min_net_spread_bps=Decimal("70"),
    )

    assert result["research_only"] is True
    assert result["aligned_candle_count"] == 3
    assert result["opportunity_count"] == 1
    assert Decimal(str(result["average_gross_spread_pct"])) == pytest.approx(Decimal("0"), abs=Decimal("0.0001"))
    assert Decimal(str(result["average_net_spread_pct"])) == pytest.approx(Decimal("-0.30"), abs=Decimal("0.0001"))
    assert result["cost_model"]["total_cost_bps"] == "30"
    assert len(result["recent_observations"]) == 3


def test_spread_research_blocks_a_quality_failed_dataset(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    _seed(storage, "binance", ("100", "100", "100"))
    _seed(storage, "bybit", ("101", "99", "100"), gap_after=0)

    with pytest.raises(SpreadResearchError, match="quality gate"):
        HistoricalSpreadResearch(storage).run(
            symbol="BTC/USDT",
            buy_venue_id="binance",
            sell_venue_id="bybit",
            interval="5m",
        )


def test_read_all_keeps_the_complete_verified_range(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    _seed(storage, "binance", tuple("100" for _ in range(5)), interval=CandleInterval.FIVE_MINUTE)
    dataset = storage.find_dataset(
        venue_id="binance",
        instrument_key="binance:spot:BTC/USDT",
        interval="5m",
    )
    assert dataset is not None

    page = storage.read_all(dataset)

    assert len(page.items) == 5
    assert page.total_count == 5
    assert page.truncated is False
