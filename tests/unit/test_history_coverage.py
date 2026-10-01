from datetime import UTC, datetime, timedelta
from decimal import Decimal

from application.history_storage import HistoryStorage
from backend.app.services.history_coverage import build_history_coverage
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


def test_history_coverage_distinguishes_verified_and_missing_series(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    opened = datetime(2026, 1, 1, tzinfo=UTC)
    query = HistoryQuery(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=opened,
        end_at=opened + timedelta(days=1),
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
                open_time=opened,
                close_time=opened + timedelta(days=1) - timedelta(milliseconds=1),
                open=Decimal("100"),
                high=Decimal("110"),
                low=Decimal("90"),
                close=Decimal("105"),
                volume=Decimal("1"),
                quote_volume=Decimal("105"),
                trade_count=1,
            )
        ],
        source="test",
    )

    coverage = build_history_coverage(storage)

    assert coverage["expected_dataset_count"] == 27
    assert coverage["verified_dataset_count"] == 1
    assert coverage["missing_dataset_count"] == 26
    assert coverage["coverage_ratio_pct"] == "3.70"
    btc_daily = next(
        item
        for item in coverage["matrix"]
        if item["venue_id"] == "binance" and item["symbol"] == "BTC/USDT" and item["interval"] == "1d"
    )
    assert btc_daily["status"] == "VERIFIED"
    okx_daily = next(
        item
        for item in coverage["matrix"]
        if item["venue_id"] == "okx" and item["symbol"] == "BTC/USDT" and item["interval"] == "1d"
    )
    assert okx_daily["status"] == "MISSING"


def test_history_coverage_exposes_latest_blocked_attempt(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    coverage = build_history_coverage(
        storage,
        [
            {
                "items": [
                    {
                        "venue_id": "okx",
                        "instrument_key": "okx:spot:BTC/USDT",
                        "interval": "1d",
                        "status": "blocked",
                        "error": {"kind": "NETWORK_ERROR", "message": "public REST request failed"},
                    }
                ]
            }
        ],
    )

    assert coverage["verified_dataset_count"] == 0
    assert coverage["blocked_dataset_count"] == 1
    assert coverage["missing_dataset_count"] == 26
    blocked = next(
        item
        for item in coverage["matrix"]
        if item["venue_id"] == "okx" and item["symbol"] == "BTC/USDT" and item["interval"] == "1d"
    )
    assert blocked["status"] == "BLOCKED"
    assert blocked["reason"] == "NETWORK_ERROR: public REST request failed"
