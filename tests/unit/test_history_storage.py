from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from application.history_storage import HistoryStorage, HistoryStorageError
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


START = datetime(2026, 1, 1, tzinfo=UTC)


def make_query() -> HistoryQuery:
    return HistoryQuery(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=START,
        end_at=START + timedelta(days=4),
    )


def candle(day: int, close: str = "101") -> Candle:
    opened = START + timedelta(days=day)
    return Candle(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        open_time=opened,
        close_time=opened + timedelta(days=1) - timedelta(milliseconds=1),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal(close),
        volume=Decimal("12"),
        quote_volume=Decimal("1200"),
        trade_count=10,
    )


def test_history_storage_is_idempotent_and_reports_gaps(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    query = make_query()

    first = storage.upsert(query, [candle(0), candle(2)], source="fake-public")
    second = storage.upsert(query, [candle(1), candle(2, "104")], source="fake-public")

    assert first.manifest.row_count == 2
    assert first.manifest.gap_count == 1
    assert first.manifest.duplicate_count == 0
    assert second.manifest.row_count == 3
    assert second.manifest.gap_count == 0
    assert second.manifest.duplicate_count == 0
    assert second.storage_key == "binance/spot/BTCUSDT/1d.csv"
    assert (tmp_path / "binance" / "spot" / "BTCUSDT" / "1d.csv").exists()
    assert len(storage.list_datasets()) == 1
    assert storage.coverage()["row_count"] == 3

    page = storage.read_page(second, limit=2)
    assert page.total_count == 3
    assert page.truncated is True
    assert [item.open_time.day for item in page.items] == [1, 2]


def test_history_storage_rejects_file_hash_mismatch(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    query = make_query()
    stored = storage.upsert(query, [candle(0)], source="fake-public")
    data_path = tmp_path / stored.storage_key
    data_path.write_text(data_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(HistoryStorageError, match="hash mismatch"):
        storage.list_datasets()


def test_history_storage_lock_is_reentrant_for_archive_operations(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)

    with storage.locked():
        assert storage.list_datasets() == ()

    assert (tmp_path / ".history.lock").exists()
