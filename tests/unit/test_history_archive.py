import importlib.util
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from application.history_archive import HistoryArchiveError, ParquetHistoryArchive
from application.history_storage import HistoryStorage
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


def _seed(storage: HistoryStorage) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    query = HistoryQuery(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=start,
        end_at=start + timedelta(days=2),
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
                open=Decimal(str(100 + index)),
                high=Decimal(str(101 + index)),
                low=Decimal(str(99 + index)),
                close=Decimal(str(100 + index)),
                volume=Decimal("1"),
                quote_volume=Decimal(str(100 + index)),
                trade_count=10,
            )
            for index in range(2)
        ],
        source="test",
    )


def test_archive_path_is_scoped_to_dedicated_root(tmp_path) -> None:
    archive = ParquetHistoryArchive(tmp_path / "history")
    assert archive.archive_path("binance/spot/BTCUSDT/1d.csv") == tmp_path / "history" / "_parquet" / "binance" / "spot" / "BTCUSDT" / "1d.parquet"
    with pytest.raises(HistoryArchiveError):
        archive.archive_path("../outside.csv")


def test_archive_status_is_truthful_when_pyarrow_is_unavailable(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    _seed(storage)
    archive = ParquetHistoryArchive(storage.root)
    status = archive.status(storage.list_datasets())
    assert status["dataset_count"] == 1
    if importlib.util.find_spec("pyarrow") is None:
        assert status["status"] == "UNAVAILABLE"
        assert status["available"] is False
        with pytest.raises(HistoryArchiveError):
            archive.archive_all(storage)


@pytest.mark.skipif(importlib.util.find_spec("pyarrow") is None, reason="pyarrow is an optional runtime dependency")
def test_parquet_archive_binds_manifest_digest(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    _seed(storage)
    archive = ParquetHistoryArchive(storage.root)
    result = archive.archive_all(storage)
    assert result["status"] == "COMPLETED"
    assert result["archived_count"] == 1
    dataset = storage.list_datasets()[0]
    assert archive.status((dataset,))["items"][0]["state"] == "ARCHIVED"
