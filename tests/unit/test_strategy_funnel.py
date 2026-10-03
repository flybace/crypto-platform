"""Unit tests for the strategy admission funnel."""

from __future__ import annotations

import pytest

from backend.app.services.strategy_funnel import (
    StrategyFunnelService,
    calculate_score,
    meets_paper_gate,
    param_hash,
    score_to_rating,
)


def test_param_hash_stable():
    h1 = param_hash({"fast": 8, "slow": 26})
    h2 = param_hash({"slow": 26, "fast": 8})
    assert h1 == h2
    assert len(h1) == 16


def test_score_to_rating():
    assert score_to_rating(90) == "S"
    assert score_to_rating(80) == "A"
    assert score_to_rating(65) == "B"
    assert score_to_rating(45) == "C"
    assert score_to_rating(10) == "D"


def test_calculate_score_good_strategy():
    metrics = {
        "total_return": 0.25,
        "max_drawdown": 0.08,
        "win_rate": 0.55,
        "sharpe_ratio": 1.8,
        "trade_count": 50,
    }
    score, breakdown = calculate_score(metrics)
    assert score >= 75  # A level
    assert score_to_rating(score) in ("A", "S")


def test_calculate_score_bad_strategy():
    metrics = {
        "total_return": -0.15,
        "max_drawdown": 0.30,
        "win_rate": 0.30,
        "sharpe_ratio": -0.5,
        "trade_count": 10,
    }
    score, _ = calculate_score(metrics)
    assert score_to_rating(score) in ("C", "D")


def test_meets_paper_gate():
    assert meets_paper_gate("S") is True
    assert meets_paper_gate("A") is True
    assert meets_paper_gate("B") is False
    assert meets_paper_gate("D") is False


class TestFunnelService:
    def _service(self):
        return StrategyFunnelService(state_store=None)

    def _good_metrics(self):
        return {
            "total_return": 0.25,
            "max_drawdown": 0.08,
            "win_rate": 0.55,
            "sharpe_ratio": 1.8,
            "trade_count": 50,
        }

    def test_submit_creates_archive_and_rating(self):
        svc = self._service()
        result = svc.submit_backtest("macd_reversal", {"fast": 8}, self._good_metrics())
        assert result["archive"]["archive_id"].startswith("run-")
        assert result["archive"]["param_hash"]
        assert result["funnel"]["rating"] in ("A", "S")
        assert result["funnel"]["stage"] == "scored"

    def test_only_promotes_never_demotes(self):
        svc = self._service()
        svc.submit_backtest("macd_reversal", {"fast": 8}, self._good_metrics())
        # Submit worse metrics with same params — rating should NOT drop
        bad = {"total_return": -0.5, "max_drawdown": 0.5, "win_rate": 0.2,
               "sharpe_ratio": -1, "trade_count": 5}
        result = svc.submit_backtest("macd_reversal", {"fast": 8}, bad)
        assert result["promoted"] is False
        funnel = svc.get_funnel("macd_reversal", {"fast": 8})
        assert funnel["rating"] in ("A", "S")

    def test_advance_stage_in_order(self):
        svc = self._service()
        svc.submit_backtest("macd_reversal", {"fast": 8}, self._good_metrics())
        # Must go one stage at a time
        with pytest.raises(ValueError, match="one stage at a time"):
            svc.advance_stage("macd_reversal", {"fast": 8}, "cross_validated")
        funnel = svc.advance_stage("macd_reversal", {"fast": 8}, "retested")
        assert funnel["stage"] == "retested"
        funnel = svc.advance_stage("macd_reversal", {"fast": 8}, "cross_validated")
        assert funnel["stage"] == "cross_validated"

    def test_paper_gate_requires_a_level(self):
        svc = self._service()
        # D-rated strategy cannot reach paper_approved
        bad = {"total_return": -0.2, "max_drawdown": 0.4, "win_rate": 0.25,
               "sharpe_ratio": -0.8, "trade_count": 10}
        svc.submit_backtest("bad_strategy", {}, bad)
        svc.advance_stage("bad_strategy", {}, "retested")
        svc.advance_stage("bad_strategy", {}, "cross_validated")
        with pytest.raises(ValueError, match="paper gate requires"):
            svc.advance_stage("bad_strategy", {}, "paper_approved")

    def test_paper_approved_allows_eligible(self):
        svc = self._service()
        svc.submit_backtest("macd_reversal", {"fast": 8}, self._good_metrics())
        svc.advance_stage("macd_reversal", {"fast": 8}, "retested")
        svc.advance_stage("macd_reversal", {"fast": 8}, "cross_validated")
        svc.advance_stage("macd_reversal", {"fast": 8}, "paper_approved")
        eligible, reason = svc.check_paper_eligible("macd_reversal", {"fast": 8})
        assert eligible is True

    def test_unrated_strategy_not_eligible(self):
        svc = self._service()
        eligible, reason = svc.check_paper_eligible("unknown", {})
        assert eligible is False
        assert "no rating" in reason
