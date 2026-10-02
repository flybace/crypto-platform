from datetime import UTC, datetime, timedelta

import pytest

from backend.app.services.paper_follow import (
    DEFAULT_FOLLOW,
    PaperFollowService,
    execute_paper_follow,
)


def make_service(tmp_path, **overrides):
    service = PaperFollowService(state_path=tmp_path / "paper-follow.json")
    if overrides:
        service.update(overrides)
    return service


def test_default_config_is_disabled() -> None:
    service = PaperFollowService()
    payload = service.get()
    assert payload["config"]["enabled"] is False
    assert payload["snapshots"] == []
    assert payload["latest"] is None


def test_update_validates_enabled_type(tmp_path) -> None:
    service = make_service(tmp_path)
    with pytest.raises(ValueError):
        service.update({"enabled": "yes"})


def test_update_validates_interval_bounds(tmp_path) -> None:
    service = make_service(tmp_path)
    with pytest.raises(ValueError):
        service.update({"interval_seconds": 60})
    with pytest.raises(ValueError):
        service.update({"interval_seconds": 99999999})
    updated = service.update({"interval_seconds": 3600})
    assert updated["config"]["interval_seconds"] == 3600


def test_update_validates_thresholds(tmp_path) -> None:
    service = make_service(tmp_path)
    with pytest.raises(ValueError):
        service.update({"alert_min_return_pct": "-1"})
    with pytest.raises(ValueError):
        service.update({"alert_max_drawdown_pct": "not-a-number"})
    updated = service.update({"alert_underperform_pct": "8"})
    assert updated["config"]["alert_underperform_pct"] == "8"


def test_update_ignores_readonly_fields(tmp_path) -> None:
    service = make_service(tmp_path)
    updated = service.update({"enabled": True, "last_follow_at": "2099-01-01T00:00:00+00:00"})
    assert updated["config"]["enabled"] is True
    assert updated["config"]["last_follow_at"] is None


def test_should_run_gates_on_enabled_and_interval(tmp_path) -> None:
    service = make_service(tmp_path, enabled=True, interval_seconds=3600)
    assert service.should_run() is True
    service.record_snapshot(
        {
            "run_id": "r1",
            "strategy_return_pct": "1.5",
            "market_return_pct": "0.5",
            "max_drawdown_pct": "1.0",
        }
    )
    assert service.should_run() is False
    future = datetime.now(UTC) + timedelta(seconds=7200)
    assert service.should_run(future) is True


def test_should_run_blocks_recent_dispatch(tmp_path) -> None:
    service = make_service(tmp_path, enabled=True, interval_seconds=3600)
    service.mark_dispatched()
    assert service.should_run() is False


def test_should_run_disabled_is_always_false(tmp_path) -> None:
    service = make_service(tmp_path)
    assert service.should_run() is False


def test_record_snapshot_evaluates_alerts(tmp_path) -> None:
    service = make_service(
        tmp_path,
        enabled=True,
        alert_min_return_pct="0",
        alert_max_drawdown_pct="20",
        alert_underperform_pct="5",
    )
    snapshot = service.record_snapshot(
        {
            "run_id": "r-alert",
            "strategy_return_pct": "-3.5",
            "market_return_pct": "4.0",
            "max_drawdown_pct": "25.0",
        }
    )
    assert set(snapshot["alerts"]) == {
        "return_below_threshold",
        "drawdown_breach",
        "underperforms_market",
    }
    assert service.get()["latest"]["run_id"] == "r-alert"


def test_record_snapshot_no_alerts_when_healthy(tmp_path) -> None:
    service = make_service(tmp_path, enabled=True)
    snapshot = service.record_snapshot(
        {
            "run_id": "r-ok",
            "strategy_return_pct": "2.0",
            "market_return_pct": "1.0",
            "max_drawdown_pct": "3.0",
        }
    )
    assert snapshot["alerts"] == []


def test_snapshots_are_bounded(tmp_path) -> None:
    service = PaperFollowService(state_path=tmp_path / "paper-follow.json", snapshot_limit=3)
    for index in range(5):
        service.record_snapshot({"run_id": f"r{index}", "strategy_return_pct": "1"})
    assert [item["run_id"] for item in service.get()["snapshots"]] == ["r4", "r3", "r2"]


def test_state_roundtrips_across_instances(tmp_path) -> None:
    path = tmp_path / "paper-follow.json"
    first = PaperFollowService(state_path=path)
    first.update({"enabled": True, "interval_seconds": 7200})
    first.record_snapshot({"run_id": "rx", "strategy_return_pct": "1.2"})
    second = PaperFollowService(state_path=path)
    payload = second.get()
    assert payload["config"]["enabled"] is True
    assert payload["config"]["interval_seconds"] == 7200
    assert payload["latest"]["run_id"] == "rx"
    assert second.should_run() is False


class _FakeAutomation:
    def __init__(self, config):
        self._config = dict(config)

    def get(self):
        return dict(self._config)

    def run_now(self, *, run_id, record_task=True):
        return {
            "run": {
                "run_id": run_id,
                "dataset_id": "ds-1",
                "start_at": "2026-09-01T00:00:00+00:00",
                "end_at": "2026-10-01T00:00:00+00:00",
                "candle_count": 100,
                "total_return_pct": "5.00",
                "max_drawdown_pct": "4.00",
                "orders": 6,
                "win_rate_pct": "66.67",
            }
        }


class _FakePaperTrading:
    def __init__(self):
        self.calls = []

    def run_strategy(self, *, venue_id, symbol, interval, config, run_id=None, record_task=True):
        self.calls.append({"run_id": run_id, "strategy_id": config.strategy_id})
        return {
            "run_id": run_id,
            "total_return_pct": "2.00",
        }


class _FakeRegistry:
    def __init__(self):
        self.asserted = []

    def assert_enabled(self, strategy_id, mode):
        self.asserted.append((strategy_id, mode))


def test_execute_paper_follow_records_benchmark_comparison(tmp_path) -> None:
    automation = _FakeAutomation(
        {
            "enabled": True,
            "strategy_id": "macd_reversal",
            "venue_id": "binance",
            "symbol": "BTC/USDT",
            "interval": "1h",
            "initial_quote": "10000",
            "initial_base": "0",
            "fee_bps": "10",
            "slippage_bps": "5",
            "allocation_ratio": "1",
            "momentum_threshold_pct": "0.02",
        }
    )
    paper = _FakePaperTrading()
    registry = _FakeRegistry()
    follow = PaperFollowService(state_path=tmp_path / "paper-follow.json")

    snapshot = execute_paper_follow(
        paper_automation=automation,
        paper_trading=paper,
        strategy_registry=registry,
        follow_service=follow,
        run_id="follow-1",
    )

    assert snapshot["strategy_return_pct"] == "5.00"
    assert snapshot["market_return_pct"] == "2.00"
    assert snapshot["excess_return_pct"] == "3.00"
    assert snapshot["strategy_id"] == "macd_reversal"
    assert paper.calls and paper.calls[0]["strategy_id"] == "buy_and_hold"
    assert ("macd_reversal", "paper") in registry.asserted
    assert ("buy_and_hold", "paper") in registry.asserted


def test_execute_paper_follow_requires_enabled_automation(tmp_path) -> None:
    automation = _FakeAutomation({"enabled": False, "strategy_id": "sma_cross"})
    follow = PaperFollowService(state_path=tmp_path / "paper-follow.json")
    with pytest.raises(ValueError, match="disabled"):
        execute_paper_follow(
            paper_automation=automation,
            paper_trading=_FakePaperTrading(),
            strategy_registry=_FakeRegistry(),
            follow_service=follow,
            run_id="follow-x",
        )


def test_default_follow_has_expected_keys() -> None:
    for key in (
        "enabled",
        "interval_seconds",
        "alert_min_return_pct",
        "alert_max_drawdown_pct",
        "alert_underperform_pct",
    ):
        assert key in DEFAULT_FOLLOW
