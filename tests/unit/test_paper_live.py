"""Unit tests for the live paper-trading loop (signal -> simulated order)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from application.candle_backtest import CandleBacktestEngine
from backend.app.services.paper_live import PaperLiveService, decide_order


def _dec(value: str) -> Decimal:
    return Decimal(value)


class TestDecideOrder:
    def test_buy_when_flat(self):
        decision = decide_order(
            signal="BUY",
            base_balance=_dec("0"),
            quote_balance=_dec("10000"),
            price=_dec("50000"),
            allocation_ratio=_dec("1"),
        )
        assert decision is not None
        assert decision["side"] == "BUY"
        assert decision["quantity"] == pytest.approx(Decimal("0.2"))

    def test_buy_respects_allocation(self):
        decision = decide_order(
            signal="BUY",
            base_balance=_dec("0"),
            quote_balance=_dec("10000"),
            price=_dec("50000"),
            allocation_ratio=_dec("0.5"),
        )
        assert decision is not None
        assert decision["quantity"] == pytest.approx(Decimal("0.1"))

    def test_no_buy_when_already_holding(self):
        assert decide_order(
            signal="BUY",
            base_balance=_dec("0.5"),
            quote_balance=_dec("10000"),
            price=_dec("50000"),
            allocation_ratio=_dec("1"),
        ) is None

    def test_sell_whole_position(self):
        decision = decide_order(
            signal="SELL",
            base_balance=_dec("0.5"),
            quote_balance=_dec("100"),
            price=_dec("50000"),
            allocation_ratio=_dec("1"),
        )
        assert decision is not None
        assert decision["side"] == "SELL"
        assert decision["quantity"] == _dec("0.5")

    def test_no_sell_when_flat(self):
        assert decide_order(
            signal="SELL",
            base_balance=_dec("0"),
            quote_balance=_dec("10000"),
            price=_dec("50000"),
            allocation_ratio=_dec("1"),
        ) is None

    def test_no_signal_no_trade(self):
        assert decide_order(
            signal=None,
            base_balance=_dec("0"),
            quote_balance=_dec("10000"),
            price=_dec("50000"),
            allocation_ratio=_dec("1"),
        ) is None

    def test_skips_dust_notional(self):
        decision = decide_order(
            signal="BUY",
            base_balance=_dec("0"),
            quote_balance=_dec("1"),
            price=_dec("50000"),
            allocation_ratio=_dec("1"),
        )
        assert decision is not None
        assert decision.get("skipped") == "notional_below_minimum"

    def test_none_on_bad_price(self):
        assert decide_order(
            signal="BUY",
            base_balance=_dec("0"),
            quote_balance=_dec("10000"),
            price=_dec("0"),
            allocation_ratio=_dec("1"),
        ) is None


def _candles(count: int):
    base = datetime(2026, 10, 1, tzinfo=UTC)
    return [
        SimpleNamespace(
            open_time=base + timedelta(hours=i),
            close=Decimal("50000") + Decimal(i * 10),
        )
        for i in range(count)
    ]


class _FakePaper:
    def __init__(self, balances):
        self._balances = dict(balances)
        self.submitted: list[dict] = []

    def summary(self):
        return {"accounts": [{"venue_id": "binance", "balances": dict(self._balances)}]}

    def submit(self, *, venue_id, symbol, interval, side, quantity, limit_price, request_id):
        record = {
            "venue_id": venue_id, "symbol": symbol, "interval": interval,
            "side": side, "quantity": quantity, "limit_price": limit_price,
            "request_id": request_id, "order_id": f"order-{len(self.submitted)}",
        }
        self.submitted.append(record)
        return record


class _FakeStrategies:
    def assert_enabled(self, strategy_id, mode=None):
        return None


class _FakeStorage:
    def __init__(self, candles):
        self._candles = candles

    def find_dataset(self, *, venue_id, instrument_key, interval):
        return SimpleNamespace(
            manifest=SimpleNamespace(gap_count=0, duplicate_count=0)
        )

    def read_page(self, dataset, *, limit=None, tail=False):
        items = self._candles[-limit:] if limit else self._candles
        return SimpleNamespace(items=items)


def _service(paper, storage, **overrides):
    service = PaperLiveService(paper, _FakeStrategies(), storage, state_store=None)
    config = {
        "enabled": True,
        "venue_id": "binance",
        "symbol": "BTC/USDT",
        "interval": "1h",
        "strategy_id": "sma_cross",
        "strategy_parameters": {},
        "allocation_ratio": "1",
    }
    config.update(overrides)
    service.update(config)
    return service


@pytest.fixture()
def signal_script(monkeypatch):
    """Script CandleBacktestEngine.signal_at to return a fixed signal."""
    calls = []

    def fake_signal_at(cls, rows, index, config):
        calls.append((len(rows), index))
        return fake_signal_at.value

    fake_signal_at.value = None
    monkeypatch.setattr(CandleBacktestEngine, "signal_at", classmethod(fake_signal_at))
    return fake_signal_at


def test_tick_disabled_returns_disabled(signal_script):
    paper = _FakePaper({"BTC": "0", "USDT": "10000"})
    service = _service(paper, _FakeStorage(_candles(60)), enabled=False)
    assert service.tick()["status"] == "disabled"
    assert paper.submitted == []


def test_tick_trades_buy_once_per_candle(signal_script):
    signal_script.value = "BUY"
    paper = _FakePaper({"BTC": "0", "USDT": "10000"})
    service = _service(paper, _FakeStorage(_candles(60)))
    first = service.tick()
    assert first["status"] == "traded"
    assert first["trade"]["side"] == "BUY"
    assert len(paper.submitted) == 1
    # second tick on the same candle must not trade again
    second = service.tick()
    assert second["status"] == "no_new_candle"
    assert len(paper.submitted) == 1


def test_tick_sells_existing_position(signal_script):
    signal_script.value = "SELL"
    paper = _FakePaper({"BTC": "0.25", "USDT": "100"})
    service = _service(paper, _FakeStorage(_candles(60)))
    result = service.tick()
    assert result["status"] == "traded"
    assert result["trade"]["side"] == "SELL"
    assert paper.submitted[0]["quantity"] == Decimal("0.25")


def test_tick_no_trade_without_signal(signal_script):
    signal_script.value = None
    paper = _FakePaper({"BTC": "0", "USDT": "10000"})
    service = _service(paper, _FakeStorage(_candles(60)))
    result = service.tick()
    assert result["status"] == "checked"
    assert paper.submitted == []
    assert service.get()["last_signal"] is None


def test_tick_new_candle_trades_again(signal_script):
    signal_script.value = "BUY"
    candles = _candles(60)
    paper = _FakePaper({"BTC": "0", "USDT": "10000"})
    storage = _FakeStorage(candles)
    service = _service(paper, storage)
    assert service.tick()["status"] == "traded"
    # append a new candle -> next tick may trade again
    candles.append(
        SimpleNamespace(
            open_time=candles[-1].open_time + timedelta(hours=1),
            close=Decimal("60000"),
        )
    )
    result = service.tick()
    assert result["status"] == "traded"
    assert len(paper.submitted) == 2


def test_tick_request_id_is_deterministic(signal_script):
    signal_script.value = "BUY"
    paper = _FakePaper({"BTC": "0", "USDT": "10000"})
    service = _service(paper, _FakeStorage(_candles(60)))
    service.tick()
    request_id = paper.submitted[0]["request_id"]
    assert request_id.startswith("paper-live:binance:BTC/USDT:1h:")
    assert request_id.endswith(":BUY")


def test_update_validates_allocation():
    paper = _FakePaper({"BTC": "0", "USDT": "10000"})
    service = PaperLiveService(paper, _FakeStrategies(), _FakeStorage(_candles(60)), state_store=None)
    with pytest.raises(ValueError):
        service.update({"enabled": True, "allocation_ratio": "2"})
    with pytest.raises(ValueError):
        service.update({"enabled": "yes"})


def test_update_ignores_readonly_fields():
    paper = _FakePaper({"BTC": "0", "USDT": "10000"})
    service = PaperLiveService(paper, _FakeStrategies(), _FakeStorage(_candles(60)), state_store=None)
    service.update({"enabled": True, "trade_count": 99, "last_candle_time": "x"})
    assert service.get()["trade_count"] == 0
    assert service.get()["last_candle_time"] is None
