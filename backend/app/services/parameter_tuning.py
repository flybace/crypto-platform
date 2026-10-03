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
    # Train/validation split info (None when validation is disabled)
    validation: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tune_id": self.tune_id,
            "strategy_id": self.strategy_id,
            "total_combinations": self.total_combinations,
            "completed": self.completed,
            "failed": self.failed,
            "ranked": list(self.ranked),
            "best": self.best,
            "validation": self.validation,
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
        self._validation_context: dict[str, Any] | None = None

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
        validation_ratio: float = 0.0,
        validation_top_n: int = 10,
    ) -> TuneResult:
        """Grid-search with optional train/validation split.

        When validation_ratio > 0, the dataset is split by time: every combination
        is backtested on the train window, the top validation_top_n advance to
        the validation window, and final ranking uses a robust score
        (validation metric minus drawdown penalty). A combination only "passes"
        the overfitting guard when it is profitable on BOTH windows.
        """
        strategy_id = str(strategy_id).strip()
        if not strategy_id:
            raise ValueError("strategy_id must not be empty")
        if not param_grids:
            raise ValueError("param_grids must not be empty")
        metric = str(metric).strip()
        if metric not in _HIGHER_IS_BETTER and metric not in _LOWER_IS_BETTER:
            raise ValueError(f"unsupported metric: {metric}")
        validation_ratio = float(validation_ratio or 0.0)
        if not 0.0 <= validation_ratio < 0.5:
            raise ValueError("validation_ratio must be in [0, 0.5)")
        validation_top_n = max(1, int(validation_top_n or 10))

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

        # Compute train/validation split points when requested.
        split_at = None
        validation_info: dict[str, Any] | None = None
        if validation_ratio > 0:
            split_at, validation_info = self._split_window(
                venue_id=venue_id, symbol=symbol, interval=interval,
                validation_ratio=validation_ratio,
            )

        tune_id = uuid4().hex
        ranked: list[dict[str, Any]] = []
        failed = 0
        for vals in combos:
            params = dict(zip(keys, vals))
            config = self._with_params(base_config, params)
            try:
                if split_at is None:
                    result = self._manager.run(
                        venue_id=venue_id,
                        symbol=symbol,
                        interval=interval,
                        config=config,
                        run_id=f"{tune_id}-{len(ranked)+failed}",
                        record_task=False,
                    )
                    entry = self._entry(params, metric, result)
                else:
                    entry = self._tune_with_validation(
                        tune_id=tune_id,
                        seq=len(ranked) + failed,
                        params=params,
                        metric=metric,
                        config=config,
                        venue_id=venue_id,
                        symbol=symbol,
                        interval=interval,
                        split_at=split_at,
                        data_end=validation_info["data_end_at"] if validation_info else None,
                    )
                    if entry is None:
                        failed += 1
                        continue
                ranked.append(entry)
            except Exception:
                failed += 1

        if split_at is not None:
            # Keep only top-N train performers for validation to bound cost,
            # then re-rank by robust score.
            self._validation_context = {
                "tune_id": tune_id,
                "venue_id": venue_id,
                "symbol": symbol,
                "interval": interval,
                "split_at": split_at,
                "metric": metric,
                "reverse": metric in _HIGHER_IS_BETTER,
                "base_config": base_config,
            }
            ranked = self._apply_validation(ranked, validation_top_n)
            self._validation_context = None

        reverse = metric in _HIGHER_IS_BETTER
        if split_at is not None:
            ranked.sort(
                key=lambda r: (
                    -(r["robust_score"] if r["robust_score"] is not None else math.inf),
                    r["validation_max_drawdown_pct"] if r["validation_max_drawdown_pct"] is not None else math.inf,
                )
            )
        else:
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
            validation=validation_info,
        )

    def _split_window(
        self,
        *,
        venue_id: str,
        symbol: str,
        interval: str,
        validation_ratio: float,
    ) -> tuple[Any, dict[str, Any]]:
        """Return (split_at, info) dividing the dataset into train/validation by time."""
        from datetime import timezone

        start_at, end_at = self._manager.dataset_time_range(
            venue_id=venue_id, symbol=symbol, interval=interval,
        )
        total_seconds = (end_at - start_at).total_seconds()
        if total_seconds <= 0:
            raise ValueError("dataset time range is empty, cannot split train/validation")
        split_at = start_at + (end_at - start_at) * (1.0 - validation_ratio)
        # Normalize to timezone-aware UTC for the storage layer
        if split_at.tzinfo is None:
            split_at = split_at.replace(tzinfo=timezone.utc)
        info = {
            "enabled": True,
            "validation_ratio": validation_ratio,
            "data_start_at": start_at.isoformat(),
            "data_end_at": end_at.isoformat(),
            "train_start_at": start_at.isoformat(),
            "train_end_at": split_at.isoformat(),
            "validation_start_at": split_at.isoformat(),
            "validation_end_at": end_at.isoformat(),
        }
        return split_at, info

    def _entry(self, params: dict[str, Any], metric: str, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "params": params,
            "metric": metric,
            "score": self._extract_metric(result, metric),
            "total_return_pct": self._extract_metric(result, "total_return_pct"),
            "max_drawdown_pct": self._extract_metric(result, "max_drawdown_pct"),
            "win_rate_pct": self._extract_metric(result, "win_rate_pct"),
            "trade_count": self._extract_trade_count(result),
            "run_id": result.get("run_id"),
        }

    def _tune_with_validation(
        self,
        *,
        tune_id: str,
        seq: int,
        params: dict[str, Any],
        metric: str,
        config: Any,
        venue_id: str,
        symbol: str,
        interval: str,
        split_at: Any,
        data_end: str | None,
    ) -> dict[str, Any] | None:
        """Run train window for one combination; validation runs later on top-N."""
        train_result = self._manager.run(
            venue_id=venue_id,
            symbol=symbol,
            interval=interval,
            end_at=split_at,
            config=config,
            run_id=f"{tune_id}-train-{seq}",
            record_task=False,
        )
        entry = self._entry(params, metric, train_result)
        entry["train_run_id"] = train_result.get("run_id")
        # Stash what the validation stage needs
        entry["_validation"] = {
            "params": params,
            "train_return": entry["total_return_pct"],
        }
        return entry

    def _apply_validation(
        self,
        ranked: list[dict[str, Any]],
        top_n: int,
    ) -> list[dict[str, Any]]:
        """Run validation window for top-N train performers and re-rank.

        Expects each entry to carry a "_validation" payload from _tune_with_validation.
        This method reuses the stored manager/tune context via closure state set
        in tune(); see _validation_context.
        """
        ctx = self._validation_context
        # Top-N by train score
        candidates = sorted(
            ranked,
            key=lambda r: (
                -(r["score"] if r["score"] is not None else math.inf)
                if ctx["reverse"] else (r["score"] if r["score"] is not None else math.inf)
            ),
        )[:top_n]
        validated: list[dict[str, Any]] = []
        for entry in candidates:
            params = entry["params"]
            config = self._with_params(ctx["base_config"], params)
            try:
                result = self._manager.run(
                    venue_id=ctx["venue_id"],
                    symbol=ctx["symbol"],
                    interval=ctx["interval"],
                    start_at=ctx["split_at"],
                    config=config,
                    run_id=f"{ctx['tune_id']}-validation-{len(validated)}",
                    record_task=False,
                )
            except Exception:
                entry["validation_status"] = "failed"
                entry["robust_score"] = None
                entry["overfit_guard_passed"] = False
                validated.append(entry)
                continue
            val_return = self._extract_metric(result, "total_return_pct")
            val_dd = self._extract_metric(result, "max_drawdown_pct")
            val_win = self._extract_metric(result, "win_rate_pct")
            # Robust score: validation metric minus drawdown penalty (mirrors research_runs)
            base_score = self._extract_metric(result, ctx["metric"])
            robust = (
                (base_score if base_score is not None else 0.0)
                - abs(val_dd if val_dd is not None else 0.0) * 0.25
                if ctx["metric"] in _HIGHER_IS_BETTER
                else -(
                    (base_score if base_score is not None else 0.0)
                    + abs(val_dd if val_dd is not None else 0.0) * 0.25
                )
            )
            train_return = entry.get("train_return", entry.get("total_return_pct"))
            # Overfitting guard: profitable on BOTH train and validation windows
            passed = (
                train_return is not None and val_return is not None
                and float(train_return) > 0 and float(val_return) > 0
            )
            entry.update({
                "validation_status": "completed",
                "validation_run_id": result.get("run_id"),
                "validation_total_return_pct": val_return,
                "validation_max_drawdown_pct": val_dd,
                "validation_win_rate_pct": val_win,
                "validation_trade_count": self._extract_trade_count(result),
                "robust_score": robust,
                "overfit_guard_passed": passed,
            })
            entry.pop("_validation", None)
            validated.append(entry)
        # Non-candidates keep train-only data, ranked below validated ones
        rest = [e for e in ranked if e not in candidates]
        for e in rest:
            e["validation_status"] = "skipped"
            e["robust_score"] = None
            e["overfit_guard_passed"] = False
            e.pop("_validation", None)
        return validated + rest

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
