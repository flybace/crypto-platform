"""币池服务单元测试：规则、快照、权限分层、时间点成分。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.app.services.coin_pools import CoinPoolError, CoinPoolService


class FakeTickers:
    def __init__(self, items):
        self._items = items

    def snapshot(self, **kwargs):
        return {"items": {"binance": self._items}}


class FakeRow:
    def __init__(self, close):
        self.close = close
        self.open_time = datetime.now(UTC)


class FakeManifest:
    def __init__(self, venue_id, interval, key, gaps=0):
        self.venue_id = venue_id
        self.interval = interval
        self.instrument_key = key
        self.gap_count = gaps
        self.duplicate_count = 0


class FakeDataset:
    def __init__(self, manifest):
        self.manifest = manifest


class FakePage:
    def __init__(self, items):
        self.items = items


class FakeStorage:
    def __init__(self, datasets, closes):
        self._datasets = datasets
        self._closes = closes

    def list_datasets(self):
        return self._datasets

    def read_page(self, dataset, limit=10, tail=True):
        return FakePage([FakeRow(c) for c in self._closes])


def _service(**kwargs):
    return CoinPoolService(state_store=None, **kwargs)


def test_create_static_pool_starts_as_candidate():
    svc = _service()
    pool = svc.create(name="手动池", pool_type="static", venue_id="binance", symbols=["BTC/USDT", "eth-usdt"])
    assert pool["role"] == "candidate"
    assert pool["confirmed"] is False
    assert pool["current_members"] == ["BTC/USDT", "ETH/USDT"]


def test_create_rejects_bad_params():
    svc = _service()
    with pytest.raises(CoinPoolError):
        svc.create(name="", pool_type="static", venue_id="binance", symbols=["BTC/USDT"])
    with pytest.raises(CoinPoolError):
        svc.create(name="x", pool_type="nope", venue_id="binance")
    with pytest.raises(CoinPoolError):
        svc.create(name="x", pool_type="static", venue_id="binance", symbols=[])
    with pytest.raises(CoinPoolError):
        svc.create(name="x", pool_type="top_volume", venue_id="binance", rule={"top_n": 0})


def test_top_volume_rule_picks_top_n():
    items = [
        {"symbol": "BTC-USDT", "venue_id": "binance"},
        {"symbol": "ETH-USDT", "venue_id": "binance"},
        {"symbol": "SOL-USDT", "venue_id": "binance"},
    ]
    svc = _service(ticker_service=FakeTickers(items))
    pool = svc.create(name="成交额池", pool_type="top_volume", venue_id="binance", rule={"top_n": 2})
    refreshed = svc.refresh(pool["pool_id"])
    assert refreshed["current_members"] == ["BTC/USDT", "ETH/USDT"]
    assert len(refreshed["snapshots"]) == 1


def test_low_volatility_rule_filters():
    # 平稳价格：波动率 ~0；剧烈价格：波动率高
    flat = [100.0 + (i % 2) * 0.1 for i in range(10)]
    wild = [100.0 * (1.15 if i % 2 == 0 else 0.87) ** 1 for i in range(10)]
    wild = [100.0]
    for i in range(1, 10):
        wild.append(wild[-1] * (1.15 if i % 2 else 0.85))
    datasets = [
        FakeDataset(FakeManifest("binance", "1d", "binance:spot:FLAT/USDT")),
        FakeDataset(FakeManifest("binance", "1d", "binance:spot:WILD/USDT")),
    ]
    closes = {"FLAT/USDT": flat, "WILD/USDT": wild}

    class Storage(FakeStorage):
        def read_page(self, dataset, limit=10, tail=True):
            key = dataset.manifest.instrument_key.rsplit(":", 1)[-1]
            return FakePage([FakeRow(c) for c in closes[key]])

    svc = _service(history_storage=Storage(datasets, closes))
    pool = svc.create(
        name="低波池", pool_type="low_volatility", venue_id="binance",
        rule={"top_n": 10, "max_daily_volatility": 0.05},
    )
    refreshed = svc.refresh(pool["pool_id"])
    assert refreshed["current_members"] == ["FLAT/USDT"]


def test_confirm_gate_candidate_cannot_trade():
    svc = _service()
    pool = svc.create(name="候选", pool_type="static", venue_id="binance", symbols=["BTC/USDT"])
    with pytest.raises(CoinPoolError, match="candidate"):
        svc.assert_tradable(pool["pool_id"])
    confirmed = svc.confirm(pool["pool_id"])
    assert confirmed["role"] == "trading"
    assert confirmed["confirmed"] is True
    tradable = svc.assert_tradable(pool["pool_id"])
    assert tradable["pool_id"] == pool["pool_id"]


def test_confirm_empty_pool_rejected():
    svc = _service()
    pool = svc.create(name="空", pool_type="static", venue_id="binance", symbols=["BTC/USDT"])
    # 手动把成员清空模拟异常状态
    svc._pools[pool["pool_id"]]["current_members"] = []
    with pytest.raises(CoinPoolError, match="empty"):
        svc.confirm(pool["pool_id"])


def test_composition_at_avoids_lookahead():
    svc = _service()
    pool = svc.create(name="快照", pool_type="static", venue_id="binance", symbols=["BTC/USDT"])
    first = svc.refresh(pool["pool_id"])
    # 模拟成分变化
    svc._pools[pool["pool_id"]]["static_symbols"] = ["BTC/USDT", "ETH/USDT"]
    second = svc.refresh(pool["pool_id"])
    between = (datetime.fromisoformat(first["snapshots"][-1]["taken_at"])
               + timedelta(microseconds=1)).isoformat()
    comp = svc.composition_at(pool["pool_id"], between)
    assert comp["members"] == ["BTC/USDT"]
    assert comp["snapshot_id"] == first["snapshots"][-1]["snapshot_id"]
    assert second["snapshots"][-1]["snapshot_id"] != comp["snapshot_id"]
    with pytest.raises(CoinPoolError):
        svc.composition_at(pool["pool_id"], "2000-01-01T00:00:00+00:00")


def test_refresh_due_pools_only_dynamic_and_due():
    svc = _service(ticker_service=FakeTickers([{"symbol": "BTC-USDT", "venue_id": "binance"}]))
    static = svc.create(name="s", pool_type="static", venue_id="binance", symbols=["BTC/USDT"])
    dyn = svc.create(name="d", pool_type="top_volume", venue_id="binance",
                     rule={"top_n": 5}, refresh_interval_seconds=300)
    refreshed = svc.refresh_due_pools()
    ids = {p["pool_id"] for p in refreshed}
    assert dyn["pool_id"] in ids
    assert static["pool_id"] not in ids
    # 刚刷新过，不应再次到期
    assert svc.refresh_due_pools() == []


def test_delete_pool():
    svc = _service()
    pool = svc.create(name="删", pool_type="static", venue_id="binance", symbols=["BTC/USDT"])
    svc.delete(pool["pool_id"])
    assert svc.get(pool["pool_id"]) is None
    with pytest.raises(KeyError):
        svc.delete(pool["pool_id"])
