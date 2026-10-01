from datetime import UTC, datetime, timedelta
from decimal import Decimal

from application.history_storage import HistoryStorage
from backend.app.services.market_summary import build_market_rankings, build_market_summary
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


START = datetime(2026, 1, 1, tzinfo=UTC)


def _query(venue_id: str) -> HistoryQuery:
    return HistoryQuery(
        venue_id=venue_id,
        market_type=MarketType.SPOT,
        instrument_key=f"{venue_id}:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=START,
        end_at=START + timedelta(days=2),
    )


def _candle(query: HistoryQuery, day: int, close: str) -> Candle:
    opened = START + timedelta(days=day)
    return Candle(
        venue_id=query.venue_id,
        market_type=query.market_type,
        instrument_key=query.instrument_key,
        native_symbol=query.native_symbol,
        interval=query.interval,
        open_time=opened,
        close_time=opened + timedelta(days=1) - timedelta(milliseconds=1),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal(close),
        volume=Decimal("2"),
        quote_volume=Decimal("200"),
        trade_count=10,
    )


def test_market_summary_uses_verified_data_and_calculates_change(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    binance = _query("binance")
    bybit = _query("bybit")
    storage.upsert(binance, [_candle(binance, 0, "100"), _candle(binance, 1, "110")], source="fake")
    storage.upsert(bybit, [_candle(bybit, 0, "100"), _candle(bybit, 1, "105")], source="fake")

    summary = build_market_summary(storage, interval="1d", expected_dataset_count=2)

    assert summary["coverage"] == {
        "verified_dataset_count": 2,
        "expected_dataset_count": 2,
        "row_count": 4,
        "coverage_ratio_pct": "100.00",
        "quality_blocked_dataset_count": 0,
    }
    assert len(summary["quotes"]) == 2
    assert summary["quotes"][0]["change_pct"] in {"5", "5.0", "5.00"}
    assert summary["spreads"][0]["buy_venue_id"] == "bybit"
    assert summary["spreads"][0]["sell_venue_id"] == "binance"


def test_market_rankings_support_spread_sort(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    query = _query("binance")
    storage.upsert(query, [_candle(query, 0, "100"), _candle(query, 1, "110")], source="fake")

    result = build_market_rankings(storage, interval="1d", rank_by="rise", limit=1)

    assert result["rank_by"] == "rise"
    assert result["count"] == 1
    assert result["items"][0]["symbol"] == "BTC/USDT"
