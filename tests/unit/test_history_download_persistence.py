from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from application.history_download import HistoryDownloadError, HistoryDownloadService
from application.history_response_archive import HistoryResponseArchive
from application.history_storage import HistoryStorage
from backend.app.services.history_metadata import HistoryMetadataRepository
from backend.app.services.task_store import TaskStore
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType
from ports.history import HistoryMetadataError
from ports.rest import PublicJsonResponse


def _query() -> HistoryQuery:
    return HistoryQuery(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=datetime(2026, 1, 1, tzinfo=UTC),
        end_at=datetime(2026, 1, 2, tzinfo=UTC),
    )


class Gateway:
    venue_id = "binance"
    source = "binance-public-history"

    def __init__(self) -> None:
        self._responses: tuple[PublicJsonResponse, ...] = ()

    def reset_raw_responses(self) -> None:
        self._responses = ()

    def fetch_candles(self, query: HistoryQuery) -> tuple[Candle, ...]:
        self._responses = (
            PublicJsonResponse(
                path="/api/v3/klines",
                params={"symbol": query.native_symbol, "interval": query.interval.value},
                payload=[[1767225600000, "100", "101", "99", "100.5", "2"]],
                received_at=datetime(2026, 1, 2, tzinfo=UTC),
            ),
        )
        return (
            Candle(
                venue_id=query.venue_id,
                market_type=query.market_type,
                instrument_key=query.instrument_key,
                native_symbol=query.native_symbol,
                interval=query.interval,
                open_time=query.start_at,
                close_time=query.start_at + timedelta(days=1) - timedelta(milliseconds=1),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100.5"),
                volume=Decimal("2"),
                quote_volume=Decimal("201"),
                trade_count=20,
            ),
        )

    def drain_raw_responses(self) -> tuple[PublicJsonResponse, ...]:
        responses = self._responses
        self._responses = ()
        return responses


class FailingMetadataWriter:
    def commit_dataset(self, *args, **kwargs):
        raise HistoryMetadataError("database unavailable")


def test_download_commits_raw_provenance_and_is_idempotent(tmp_path) -> None:
    store = TaskStore("sqlite:///:memory:")
    service = HistoryDownloadService(
        {"binance": Gateway()},
        HistoryStorage(tmp_path / "history"),
        raw_archive=HistoryResponseArchive(tmp_path / "history"),
        metadata_writer=HistoryMetadataRepository(store),
    )
    try:
        first = service.download(_query())
        second = service.download(_query())

        assert first.raw_archive is not None
        assert first.raw_archive["written_count"] == 1
        assert second.raw_archive is not None
        assert second.raw_archive["existing_count"] == 1
        status = store.history_metadata_status()
        assert status["dataset_count"] == 1
        assert status["raw_response_count"] == 1
    finally:
        service.close()
        store.close()


def test_database_failure_leaves_verified_files_for_reconciliation(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    service = HistoryDownloadService(
        {"binance": Gateway()},
        storage,
        raw_archive=HistoryResponseArchive(storage.root),
        metadata_writer=FailingMetadataWriter(),
    )

    with pytest.raises(HistoryMetadataError, match="database unavailable"):
        service.download(_query())

    assert len(storage.list_datasets()) == 1
    assert HistoryResponseArchive(storage.root).status()["response_count"] == 1


def test_archive_failure_marks_formal_metadata_degraded(tmp_path) -> None:
    store = TaskStore("sqlite:///:memory:")
    storage = HistoryStorage(tmp_path / "history")
    service = HistoryDownloadService({"binance": Gateway()}, storage)
    repository = HistoryMetadataRepository(store)
    try:
        dataset = service.download(_query()).dataset
        repository.commit_dataset(
            storage.dataset_dict(dataset),
            raw_responses=(),
            raw_response_state="NOT_CONFIGURED",
            parquet_state="PENDING",
        )
        repository.record_archive({"status": "FAILED", "message": "parquet write failed", "items": []})
        status = store.history_metadata_status()
        assert status["status"] == "degraded"
        assert status["degraded_count"] == 1
    finally:
        service.close()
        store.close()
