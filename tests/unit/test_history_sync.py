from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json

from application.history_storage import HistoryStorage
from adapters.standalone.state_store import SqlStateStore
from backend.app.services.history_sync import HistorySyncService
from backend.app.services.task_store import TaskStore
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


class FakeJobs:
    def __init__(self) -> None:
        self.queries = ()

    def submit(self, queries):
        self.queries = tuple(queries)
        return {"job_id": "job-sync-1", "status": "queued", "total": len(self.queries)}


def _query(start: datetime, end: datetime) -> HistoryQuery:
    return HistoryQuery(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=start,
        end_at=end,
    )


def _seed(storage: HistoryStorage) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    query = _query(start, start + timedelta(days=3))
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
                open=Decimal(str(price)),
                high=Decimal(str(price)),
                low=Decimal(str(price)),
                close=Decimal(str(price)),
                volume=Decimal("1"),
                quote_volume=Decimal(str(price)),
                trade_count=1,
            )
            for index, price in enumerate((100, 110, 120))
        ],
        source="test",
    )


def test_incremental_plan_starts_at_existing_manifest_end(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    _seed(storage)
    service = HistorySyncService(storage, default_lookback_days=30)

    plan = service.plan(
        venues=["binance"],
        symbols=["BTC/USDT"],
        intervals=["1d"],
        now=datetime(2026, 1, 10, 12, 34, tzinfo=UTC),
    )

    item = plan["items"][0]
    assert item["status"] == "STALE"
    assert item["download"] is True
    assert item["start_at"] == "2026-01-04T00:00:00+00:00"
    assert item["end_at"] == "2026-01-10T00:00:00+00:00"


def test_sync_submits_only_actionable_windows_and_persists_last_job(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    service = HistorySyncService(storage, state_path=tmp_path / "sync.json", default_lookback_days=30)
    jobs = FakeJobs()

    result = service.submit(
        jobs,
        venues=["binance"],
        symbols=["BTC/USDT"],
        intervals=["1d"],
        lookback_days=5,
    )

    assert result["status"] == "submitted"
    assert result["job"]["job_id"] == "job-sync-1"
    assert len(jobs.queries) == 1
    assert jobs.queries[0].start_at < jobs.queries[0].end_at
    restored = HistorySyncService(storage, state_path=tmp_path / "sync.json")
    assert restored.last_plan()["last_job_id"] == "job-sync-1"


def test_up_to_date_plan_has_no_network_job(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    query = _query(start, start + timedelta(days=3))
    candles = [
        Candle(
            venue_id="binance",
            market_type=MarketType.SPOT,
            instrument_key=query.instrument_key,
            native_symbol=query.native_symbol,
            interval=query.interval,
            open_time=start + timedelta(days=index),
            close_time=start + timedelta(days=index + 1) - timedelta(milliseconds=1),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("1"),
            quote_volume=Decimal("100"),
            trade_count=1,
        )
        for index in range(3)
    ]
    storage.upsert(query, candles, source="test")
    service = HistorySyncService(storage)
    plan = service.plan(
        venues=["binance"],
        symbols=["BTC/USDT"],
        intervals=["1d"],
        now=datetime(2026, 1, 3, 12, tzinfo=UTC),
    )
    assert plan["status"] == "UP_TO_DATE"
    assert plan["verified_dataset_count"] == 1
    assert plan["verified_row_count"] == 3
    assert plan["missing_count"] == 0
    assert plan["quality_blocked_count"] == 0
    assert plan["last_job_id"] is None

    jobs = FakeJobs()
    result = service.submit(
        jobs,
        venues=["binance"],
        symbols=["BTC/USDT"],
        intervals=["1d"],
        now=datetime(2026, 1, 3, 12, tzinfo=UTC),
    )
    assert result["status"] == "up_to_date"
    assert result["job"] is None
    assert jobs.queries == ()


def test_sync_state_uses_sql_as_authority_across_instances(tmp_path) -> None:
    database = tmp_path / "control-plane.sqlite3"
    legacy = tmp_path / "history-sync.json"
    storage = HistoryStorage(tmp_path / "history")
    first_store = TaskStore(f"sqlite:///{database.as_posix()}", required=True)
    first = HistorySyncService(
        storage,
        state_path=legacy,
        state_store=SqlStateStore(
            first_store,
            "crypto.runtime.history-sync",
            legacy_path=legacy,
        ),
        default_lookback_days=5,
    )
    jobs = FakeJobs()
    first.submit(
        jobs,
        venues=["binance"],
        symbols=["BTC/USDT"],
        intervals=["1d"],
        now=datetime(2026, 1, 5, 12, tzinfo=UTC),
    )
    assert json.loads(legacy.read_text(encoding="utf-8"))["last_job_id"] == "job-sync-1"
    legacy.write_text(json.dumps({"last_job_id": "stale-json-job"}), encoding="utf-8")
    first_store.close()

    second_store = TaskStore(f"sqlite:///{database.as_posix()}", required=True)
    second = HistorySyncService(
        storage,
        state_path=legacy,
        state_store=SqlStateStore(
            second_store,
            "crypto.runtime.history-sync",
            legacy_path=legacy,
        ),
    )
    assert second.last_plan()["last_job_id"] == "job-sync-1"
    second_store.close()
