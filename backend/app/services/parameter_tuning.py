"""Automatic parameter tuning via grid search over backtest configurations.

Given a strategy and parameter grids, runs a backtest for each combination
and ranks results by the chosen optimization metric. Bounded by
max_combinations to keep API response times sane.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class TuneResult:
    tune_id: str
    strategy_id: str
    total_combinations: int
    completed: int
    failed: int
    ranked: tuple[dict[str, Any], ...]
    best: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tune_id": self.tune_id,
            "strategy_id": self.strategy_id,
            "total_combinations": self.total_combinations,
            "completed": self.completed,
            "failed": self.failed,
            "ranked": list(self.ranked),
            "best": self.best,
        }


# Metrics where higher is better
_HIGHER_IS_BETTER = frozenset({
    "total_return_pct", "win_rate_pct", "profit_factor",
    "sharpe_ratio", "final_equity",
})
# Metrics where lower is better
_LOWER_IS_BETTER = frozenset({
    "max_drawdown_pct",
})


class ParameterTuner:
    """Grid-search tuner backed by a BacktestRunManager."""

    def __init__(self, backtest_manager: Any, *, max_combinations: int = 100) -> None:
        self._manager = backtest_manager
        self._max_combinations = max(1, int(max_combinations))

    def tune(
        self,
        *,
        strategy_id: str,
        venue_id: str,
        symbol: str,
        interval: str,
        param_grids: dict[str, list[Any]],
        base_config: Any,
        metric: str = "total_return_pct",
        max_combinations: int | None = None,
    ) -> TuneResult:
        strategy_id = str(strategy_id).strip()
        if not strategy_id:
            raise ValueError("strategy_id must not be empty")
        if not param_grids:
            raise ValueError("param_grids must not be empty")
        metric = str(metric).strip()
        if metric not in _HIGHER_IS_BETTER and metric not in _LOWER_IS_BETTER:
            raise ValueError(f"unsupported metric: {metric}")

        keys = list(param_grids.keys())
        value_lists = []
        for key in keys:
            values = param_grids[key]
            if not isinstance(values, (list, tuple)) or not values:
                raise ValueError(f"param_grid[{key}] must be a non-empty list")
            value_lists.append(list(values))
        combos = list(itertools.product(*value_lists))

        limit = self._max_combinations if max_combinations is None else max(1, int(max_combinations))
        if len(combos) > limit:
            raise ValueError(
                f"parameter grid has {len(combos)} combinations, exceeds limit of {limit}"
            )

        tune_id = uuid4().hex
        ranked: list[dict[str, Any]] = []
        failed = 0
        for vals in combos:
            params = dict(zip(keys, vals))
            # Build a per-combo config by copying base and overriding strategy_parameters
            config = self._with_params(base_config, params)
            try:
                result = self._manager.run(
                    venue_id=venue_id,
                    symbol=symbol,
                    interval=interval,
                    config=config,
                    run_id=f"{tune_id}-{len(ranked)+failed}",
                    record_task=False,
                )
                score = self._extract_metric(result, metric)
                ranked.append({
                    "params": params,
                    "metric": metric,
                    "score": score,
                    "total_return_pct": self._extract_metric(result, "total_return_pct"),
                    "max_drawdown_pct": self._extract_metric(result, "max_drawdown_pct"),
                    "win_rate_pct": self._extract_metric(result, "win_rate_pct"),
                    "trade_count": self._extract_trade_count(result),
                    "run_id": result.get("run_id"),
                })
            except Exception:
                failed += 1

        reverse = metric in _HIGHER_IS_BETTER
        ranked.sort(
            key=lambda r: (
                -(r["score"] if r["score"] is not None else math.inf)
                if reverse else (r["score"] if r["score"] is not None else math.inf),
                r["max_drawdown_pct"] if r["max_drawdown_pct"] is not None else math.inf,
            )
        )
        best = ranked[0] if ranked else None
        return TuneResult(
            tune_id=tune_id,
            strategy_id=strategy_id,
            total_combinations=len(combos),
            completed=len(ranked),
            failed=failed,
            ranked=tuple(ranked),
            best=best,
        )

    def _with_params(self, base_config: Any, params: dict[str, Any]) -> Any:
        """Return a copy of base_config with strategy_parameters overridden."""
        import copy
        config = copy.copy(base_config)
        # CandleBacktestConfig is a dataclass; strategy_parameters is a field
        existing = dict(getattr(config, "parameters", {}) or {})
        existing.update(params)
        object.__setattr__(config, "parameters", existing)
        # Also update strategy_id if the config carries it
        return config

    def _extract_metric(self, result: dict[str, Any], metric: str) -> float | None:
        # BacktestRunManager.run returns result.as_dict() which has the metrics
        # at top level or nested. Try both.
        for source in (result, result.get("summary") or {}):
            if isinstance(source, dict) and metric in source:
                try:
                    return float(source[metric])
                except (TypeError, ValueError):
                    pass
        return None

    def _extract_trade_count(self, result: dict[str, Any]) -> int:
        for source in (result, result.get("summary") or {}):
            if isinstance(source, dict):
                log = source.get("trade_log")
                if isinstance(log, list):
                    return len(log)
        return 0
