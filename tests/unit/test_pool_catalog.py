from datetime import UTC, datetime, timedelta
from decimal import Decimal

from application.history_storage import HistoryStorage
from application.pool_catalog import PoolCatalog
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


def _add_dataset(storage: HistoryStorage, venue: str, symbol: str, interval: CandleInterval) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    step = timedelta(days=1) if interval is CandleInterval.DAY else timedelta(hours=1)
    query = HistoryQuery(
        venue_id=venue,
        market_type=MarketType.SPOT,
        instrument_key=f"{venue}:spot:{symbol}",
        native_symbol=symbol.replace("/", ""),
        interval=interval,
        start_at=start,
        end_at=start + step * 4,
    )
    candles = []
    for index in range(3):
        opened = start + step * index
        price = Decimal(100 + index)
        candles.append(Candle(
            venue_id=venue,
            market_type=MarketType.SPOT,
            instrument_key=query.instrument_key,
            native_symbol=query.native_symbol,
            interval=interval,
            open_time=opened,
            close_time=opened + step - timedelta(milliseconds=1),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=Decimal("2"),
            quote_volume=price * 2,
            trade_count=1,
        ))
    storage.upsert(query, candles, source="test")


def test_system_pools_are_scoped_by_use_case(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    _add_dataset(storage, "binance", "BTC/USDT", CandleInterval.DAY)
    _add_dataset(storage, "okx", "BTC/USDT", CandleInterval.DAY)
    _add_dataset(storage, "binance", "BTC/USDT", CandleInterval.HOUR)

    catalog = PoolCatalog(storage)
    pools = {pool["pool_id"]: pool for pool in catalog.list()}

    assert pools["system.research.cross_venue"]["dataset_count"] == 3
    assert pools["system.backtest.verified"]["dataset_count"] == 2
    assert pools["system.paper.whitelist"]["dataset_count"] == 1
    assert pools["system.backtest.verified"]["complete_symbol_count"] == 1


def test_custom_pool_filters_venue_symbol_and_interval(tmp_path) -> None:
    storage = HistoryStorage(tmp_path)
    _add_dataset(storage, "binance", "BTC/USDT", CandleInterval.DAY)
    _add_dataset(storage, "okx", "ETH/USDT", CandleInterval.DAY)

    pool = PoolCatalog(storage).create(
        name="BTC 研究",
        kind="research",
        venue_ids=["binance"],
        symbols=["BTCUSDT"],
        interval="1d",
    )

    assert pool["kind"] == "research"
    assert [member["symbol"] for member in pool["members"]] == ["BTC/USDT"]
    assert [member["venue_id"] for member in pool["members"]] == ["binance"]

