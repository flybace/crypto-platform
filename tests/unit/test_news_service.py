from datetime import UTC, datetime, timedelta
from decimal import Decimal

from application.history_storage import HistoryStorage
from backend.app.services.news import NewsService
from domain.candle import Candle, CandleInterval, HistoryQuery
from domain.market import MarketType


def _storage(tmp_path) -> HistoryStorage:
    storage = HistoryStorage(tmp_path / "history")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    query = HistoryQuery(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=start,
        end_at=start + timedelta(days=3),
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
                open=Decimal(str(price)),
                high=Decimal(str(price + 1)),
                low=Decimal(str(price - 1)),
                close=Decimal(str(price)),
                volume=Decimal("10"),
                quote_volume=Decimal(str(price * 10)),
                trade_count=10,
            )
            for index, price in enumerate((100, 110, 120))
        ],
        source="test",
    )
    return storage


def test_news_event_is_deduplicated_and_listed(tmp_path) -> None:
    service = NewsService(_storage(tmp_path), state_path=tmp_path / "news.json")
    values = {
        "title": "BTC network update",
        "summary": "A source-labelled event for research.",
        "source": "test-feed",
        "published_at": datetime(2026, 1, 2, tzinfo=UTC),
        "symbols": ["BTC/USDT"],
        "topics": ["network"],
        "sentiment": "positive",
        "risk_level": "low",
        "impact_score": "80",
    }
    first = service.create(**values)
    second = service.create(**values)
    assert first["event_id"] == second["event_id"]
    assert second["deduplicated"] is True
    assert service.summary()["event_count"] == 1
    assert service.events(symbol="BTC/USDT")[0]["source"] == "test-feed"


def test_resonance_marks_risk_events_as_research_blocked(tmp_path) -> None:
    service = NewsService(_storage(tmp_path))
    service.create(
        title="BTC risk notice",
        summary="A risk event that must block a candidate.",
        source="risk-feed",
        published_at=datetime(2026, 1, 2, tzinfo=UTC),
        symbols=["BTC/USDT"],
        topics=["risk"],
        sentiment="risk",
        risk_level="high",
        impact_score=90,
    )
    result = service.resonance(interval="1d")
    assert result["research_only"] is True
    assert result["execution_eligible"] is False
    assert result["items"][0]["state"] == "RISK_BLOCK"
    assert result["items"][0]["execution_eligible"] is False
