"""Tests for the parameter tuning service."""

import pytest

from backend.app.services.parameter_tuning import ParameterTuner


class FakeManager:
    """Backtest manager stub: return_pct = sum of param values."""

    def run(self, *, venue_id, symbol, interval, config, run_id=None, record_task=True,
            start_at=None, end_at=None):
        params = getattr(config, "parameters", {}) or {}
        total = sum(float(v) for v in params.values() if isinstance(v, (int, float)))
        return {
            "run_id": run_id or "r1",
            "total_return_pct": total,
            "max_drawdown_pct": total / 10,
            "win_rate_pct": 50.0,
            "trade_log": [1, 2],
        }

    def dataset_time_range(self, *, venue_id, symbol, interval):
        from datetime import datetime, timezone
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 4, 1, tzinfo=timezone.utc)
        return (start, end)


class FakeConfig:
    def __init__(self):
        self.parameters = {}


class TestParameterTuner:
    def test_grid_search_finds_best(self):
        tuner = ParameterTuner(FakeManager())
        result = tuner.tune(
            strategy_id="s1", venue_id="binance", symbol="BTC/USDT", interval="1d",
            param_grids={"a": [1, 2], "b": [10, 20]},
            base_config=FakeConfig(),
        )
        assert result.total_combinations == 4
        assert result.completed == 4
        assert result.failed == 0
        # best = a=2, b=20 -> return 22
        assert result.best["params"] == {"a": 2, "b": 20}
        assert result.best["score"] == 22.0

    def test_rejects_empty_grids(self):
        tuner = ParameterTuner(FakeManager())
        with pytest.raises(ValueError):
            tuner.tune(
                strategy_id="s1", venue_id="b", symbol="s", interval="1d",
                param_grids={}, base_config=FakeConfig(),
            )

    def test_rejects_too_many_combinations(self):
        tuner = ParameterTuner(FakeManager(), max_combinations=3)
        with pytest.raises(ValueError):
            tuner.tune(
                strategy_id="s1", venue_id="b", symbol="s", interval="1d",
                param_grids={"a": [1, 2], "b": [1, 2]},
                base_config=FakeConfig(),
            )

    def test_rejects_bad_metric(self):
        tuner = ParameterTuner(FakeManager())
        with pytest.raises(ValueError):
            tuner.tune(
                strategy_id="s1", venue_id="b", symbol="s", interval="1d",
                param_grids={"a": [1]},
                base_config=FakeConfig(),
                metric="not_a_metric",
            )

    def test_lower_is_better_metric(self):
        tuner = ParameterTuner(FakeManager())
        result = tuner.tune(
            strategy_id="s1", venue_id="b", symbol="s", interval="1d",
            param_grids={"a": [1, 5]},
            base_config=FakeConfig(),
            metric="max_drawdown_pct",
        )
        # lower drawdown wins: a=1 -> dd=0.1
        assert result.best["params"] == {"a": 1}

    def test_handles_run_failures(self):
        class FailManager(FakeManager):
            def run(self, **kwargs):
                raise RuntimeError("boom")
        tuner = ParameterTuner(FailManager())
        result = tuner.tune(
            strategy_id="s1", venue_id="b", symbol="s", interval="1d",
            param_grids={"a": [1, 2]},
            base_config=FakeConfig(),
        )
        assert result.completed == 0
        assert result.failed == 2
        assert result.best is None

    def test_rejects_bad_validation_ratio(self):
        tuner = ParameterTuner(FakeManager())
        with pytest.raises(ValueError):
            tuner.tune(
                strategy_id="s1", venue_id="b", symbol="s", interval="1d",
                param_grids={"a": [1]},
                base_config=FakeConfig(),
                validation_ratio=0.6,
            )

    def test_validation_split_marks_overfit_guard(self):
        from datetime import datetime, timezone

        class SplitManager(FakeManager):
            def dataset_time_range(self, *, venue_id, symbol, interval):
                start = datetime(2024, 1, 1, tzinfo=timezone.utc)
                end = datetime(2024, 4, 1, tzinfo=timezone.utc)
                return (start, end)

            def run(self, *, venue_id, symbol, interval, config, run_id=None,
                    record_task=True, start_at=None, end_at=None):
                params = getattr(config, "parameters", {}) or {}
                total = sum(float(v) for v in params.values() if isinstance(v, (int, float)))
                # Simulate overfitting: train window profitable, validation window loses
                if start_at is not None:
                    total = -total
                return {
                    "run_id": run_id or "r1",
                    "total_return_pct": total,
                    "max_drawdown_pct": abs(total) / 10,
                    "win_rate_pct": 50.0,
                    "trade_log": [1, 2],
                }

        tuner = ParameterTuner(SplitManager())
        result = tuner.tune(
            strategy_id="s1", venue_id="binance", symbol="BTC/USDT", interval="1d",
            param_grids={"a": [1, 2]},
            base_config=FakeConfig(),
            validation_ratio=0.3,
            validation_top_n=2,
        )
        assert result.validation is not None
        assert result.validation["enabled"] is True
        # All combos ran train; top-2 advanced to validation
        assert result.total_combinations == 2
        for entry in result.ranked:
            assert entry["validation_status"] in ("completed", "skipped", "failed")
        # Validation window was negative -> overfit guard must fail
        validated = [e for e in result.ranked if e["validation_status"] == "completed"]
        assert validated, "expected some validated entries"
        assert all(e["overfit_guard_passed"] is False for e in validated)

    def test_validation_passes_when_both_windows_profitable(self):
        from datetime import datetime, timezone

        class ProfitManager(FakeManager):
            def dataset_time_range(self, *, venue_id, symbol, interval):
                start = datetime(2024, 1, 1, tzinfo=timezone.utc)
                end = datetime(2024, 4, 1, tzinfo=timezone.utc)
                return (start, end)

        tuner = ParameterTuner(ProfitManager())
        result = tuner.tune(
            strategy_id="s1", venue_id="binance", symbol="BTC/USDT", interval="1d",
            param_grids={"a": [1, 2]},
            base_config=FakeConfig(),
            validation_ratio=0.3,
            validation_top_n=2,
        )
        validated = [e for e in result.ranked if e["validation_status"] == "completed"]
        assert validated
        assert all(e["overfit_guard_passed"] is True for e in validated)
        assert all(e["robust_score"] is not None for e in validated)
