from datetime import UTC, datetime, timedelta
from decimal import Decimal

from application.history_storage import HistoryStorage
from application.pool_catalog import PoolCatalog
from application.screening import ScreeningService
from application.strategy_incubator import StrategyIncubatorService
from adapters.standalone.state_store import SqlStateStore
from backend.app.services.task_store import TaskStore
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


def _screening(tmp_path) -> tuple[ScreeningService, str]:
    storage = HistoryStorage(tmp_path / "history")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    query = HistoryQuery(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=start,
        end_at=start + timedelta(days=5),
    )
    candles = []
    for index, price in enumerate((100, 105, 110, 115, 120)):
        opened = start + timedelta(days=index)
        value = Decimal(price)
        candles.append(
            Candle(
                venue_id="binance",
                market_type=MarketType.SPOT,
                instrument_key=query.instrument_key,
                native_symbol=query.native_symbol,
                interval=query.interval,
                open_time=opened,
                close_time=opened + timedelta(days=1) - timedelta(milliseconds=1),
                open=value,
                high=value,
                low=value,
                close=value,
                volume=Decimal("1"),
                quote_volume=value,
                trade_count=1,
            )
        )
    storage.upsert(query, candles, source="test")
    screening = ScreeningService(storage, PoolCatalog(storage))
    run = screening.run(
        pool_id="system.backtest.verified",
        interval="1d",
        lookback=5,
        min_return_pct=Decimal("10"),
        max_volatility_pct=None,
        min_quote_volume=Decimal("0"),
        min_momentum_pct=Decimal("0"),
        limit=10,
    )
    return screening, str(run["run_id"])


def test_screening_candidates_are_deduplicated_and_persisted(tmp_path) -> None:
    screening, run_id = _screening(tmp_path)
    service = StrategyIncubatorService(screening, state_path=tmp_path / "incubators.json")

    created = service.add_from_screening(run_id)
    assert created["stats"] == {"added": 1, "updated": 0, "removed": 0}
    assert created["pool"]["kind"] == "strategy_incubator"
    assert created["pool"]["candidate_count"] == 1
    assert created["stage"]["key"] == "collecting"

    repeated = service.add_from_screening(run_id)
    assert repeated["stats"] == {"added": 0, "updated": 1, "removed": 0}
    restored = StrategyIncubatorService(screening, state_path=tmp_path / "incubators.json")
    assert restored.get(created["pool"]["pool_id"])["candidate_count"] == 1


def test_replace_mode_removes_candidates_and_recent_screen_is_processed(tmp_path) -> None:
    screening, run_id = _screening(tmp_path)
    service = StrategyIncubatorService(screening)
    result = service.add_from_screening(run_id, mode="replace")
    snapshot = service.snapshot()
    assert snapshot["recent_screens"][0]["already_in_incubator"] is True
    assert result["pool"]["research_only"] is True


class _ScreeningStub:
    def __init__(self, runs: dict[str, dict[str, object]]) -> None:
        self._runs = runs

    def get(self, run_id: str) -> dict[str, object] | None:
        return self._runs.get(run_id)

    def list(self, limit: int = 30) -> list[dict[str, object]]:
        return list(self._runs.values())[:limit]


def test_sql_incubator_merge_refreshes_before_cross_process_write(tmp_path) -> None:
    screening = _ScreeningStub(
        {
            "run-1": {
                "run_id": "run-1",
                "pool_id": "system.backtest.verified",
                "strategy_id": "sma_cross",
                "items": [{"dataset_id": "binance:btc", "passed": True, "score": "10"}],
            },
            "run-2": {
                "run_id": "run-2",
                "pool_id": "system.backtest.verified",
                "strategy_id": "sma_cross",
                "items": [{"dataset_id": "bybit:btc", "passed": True, "score": "9"}],
            },
        }
    )
    database = tmp_path / "control-plane.sqlite3"
    first_store = TaskStore(f"sqlite:///{database.as_posix()}", required=True)
    second_store = TaskStore(f"sqlite:///{database.as_posix()}", required=True)
    first = StrategyIncubatorService(
        screening,
        state_store=SqlStateStore(first_store, "crypto.domain.strategy-incubators"),
    )
    second = StrategyIncubatorService(
        screening,
        state_store=SqlStateStore(second_store, "crypto.domain.strategy-incubators"),
    )

    first.add_from_screening("run-1")
    merged = second.add_from_screening("run-2")

    assert merged["pool"]["candidate_count"] == 2
    assert {item["dataset_id"] for item in merged["pool"]["items"]} == {"binance:btc", "bybit:btc"}
    first_store.close()
    second_store.close()
