"""Unit tests for MarketRegimeService."""

from __future__ import annotations

from types import SimpleNamespace
from datetime import UTC, datetime, timedelta

from backend.app.services.market_regime import MarketRegimeService


def _candle(close: float, days_ago: int = 0):
    ot = datetime.now(UTC) - timedelta(days=days_ago)
    return SimpleNamespace(close=close, open_time=ot)


def _storage_with_closes(closes: list[float]):
    candles = [_candle(c, len(closes) - 1 - i) for i, c in enumerate(closes)]
    storage = SimpleNamespace()
    storage.find_dataset = lambda **kw: SimpleNamespace(
        manifest=SimpleNamespace(gap_count=0, duplicate_count=0)
    )
    storage.read_page = lambda dataset, limit=None, tail=False: SimpleNamespace(items=candles)
    return storage


def test_strong_uptrend():
    # Steady uptrend: 60 days rising 1% per day
    closes = [100 * (1.01 ** i) for i in range(60)]
    svc = MarketRegimeService(storage=_storage_with_closes(closes), state_store=None)
    result = svc.evaluate(force=True)
    assert result.regime == "strong"
    assert result.factor == 1.0
    assert result.blocks_new_positions is False


def test_crisis_downtrend():
    # Steady crash: 60 days falling 2% per day
    closes = [100 * (0.98 ** i) for i in range(60)]
    svc = MarketRegimeService(storage=_storage_with_closes(closes), state_store=None)
    result = svc.evaluate(force=True)
    assert result.regime == "crisis"
    assert result.factor == 0.0
    assert result.blocks_new_positions is True


def test_data_unavailable_blocks():
    storage = SimpleNamespace()
    storage.find_dataset = lambda **kw: None
    svc = MarketRegimeService(storage=storage, state_store=None)
    result = svc.evaluate(force=True)
    assert result.regime == "crisis"
    assert result.blocks_new_positions is True
    assert "数据不可用" in result.reason


def test_no_storage_blocks():
    svc = MarketRegimeService(storage=None, state_store=None)
    result = svc.evaluate(force=True)
    assert result.blocks_new_positions is True


def test_disabled_returns_neutral():
    svc = MarketRegimeService(storage=None, state_store=None)
    svc.update_config({"enabled": False})
    result = svc.evaluate(force=True)
    assert result.regime == "neutral"
    assert result.blocks_new_positions is False


def test_config_update():
    svc = MarketRegimeService(storage=None, state_store=None)
    cfg = svc.update_config({"factors": {"neutral": 0.8}})
    assert cfg["factors"]["neutral"] == 0.8
    assert cfg["factors"]["strong"] == 1.0  # others preserved
