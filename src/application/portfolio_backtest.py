"""Portfolio backtests over several verified candle datasets.

The candle engine remains the single source of truth for fills and costs. This
module only handles capital allocation, aggregation, and portfolio reporting.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Iterable
from uuid import uuid4

from application.history_storage import HistoryStorage, HistoryStorageError, StoredDataset
from application.candle_backtest import CandleBacktestConfig, CandleBacktestEngine, CandleBacktestResult


class PortfolioBacktestError(ValueError):
    """The selected portfolio cannot produce a trustworthy result."""


class PortfolioBacktestRunner:
    """Run one strategy across a bounded set of instruments with equal capital."""

    def __init__(self, storage: HistoryStorage, engine: CandleBacktestEngine | None = None) -> None:
        self._storage = storage
        self._engine = engine or CandleBacktestEngine()

    def run(
        self,
        datasets: Iterable[StoredDataset],
        *,
        config: CandleBacktestConfig,
        run_id: str | None = None,
        max_datasets: int = 20,
    ) -> dict[str, object]:
        selected = tuple(datasets)[: max(1, min(int(max_datasets), 50))]
        if not selected:
            raise PortfolioBacktestError("portfolio contains no datasets")

        loaded: list[tuple[StoredDataset, tuple]] = []
        failures: list[dict[str, str]] = []
        for dataset in selected:
            manifest = dataset.manifest
            if manifest.gap_count or manifest.duplicate_count:
                failures.append({"dataset_id": manifest.dataset_id, "reason": "history quality gate did not pass"})
                continue
            try:
                page = self._storage.read_all(dataset)
            except HistoryStorageError as error:
                failures.append({"dataset_id": manifest.dataset_id, "reason": str(error)})
                continue
            if len(page.items) < CandleBacktestEngine.required_lookback(config):
                failures.append({"dataset_id": manifest.dataset_id, "reason": "insufficient candles for strategy warmup"})
                continue
            loaded.append((dataset, page.items))
        if not loaded:
            raise PortfolioBacktestError("no portfolio dataset could produce a backtest")

        allocation = Decimal("1") / Decimal(len(loaded))
        results: list[tuple[StoredDataset, CandleBacktestResult]] = []
        for dataset, candles in loaded:
            child_config = replace(
                config,
                initial_quote=config.initial_quote * allocation,
                initial_base=config.initial_base * allocation,
            )
            child_result = self._engine.run(
                candles,
                config=child_config,
                run_id=f"{run_id or uuid4().hex}-{len(results) + 1}",
                dataset_id=dataset.manifest.dataset_id,
            )
            results.append((dataset, child_result))

        initial_equity = sum((result.initial_equity for _, result in results), Decimal("0"))
        final_equity = sum((result.final_equity for _, result in results), Decimal("0"))
        equity_points = self._aggregate_curves(results)
        max_drawdown_quote, max_drawdown_pct = self._drawdown(equity_points)
        total_return_pct = (final_equity / initial_equity - Decimal("1")) * Decimal("100") if initial_equity else Decimal("0")
        orders = sum(result.orders for _, result in results)
        round_trips = sum(result.round_trips for _, result in results)
        winning_round_trips = sum(result.winning_round_trips for _, result in results)
        created_at = datetime.now(UTC).isoformat()
        return {
            "run_id": run_id or uuid4().hex,
            "status": "completed" if not failures else "partial",
            "mode": "portfolio",
            "data_level": "KLINE",
            "strategy_id": config.strategy_id,
            "strategy_version": "1.0.0",
            "dataset_count": len(results),
            "failed_dataset_count": len(failures),
            "initial_equity": str(initial_equity),
            "final_equity": str(final_equity),
            "total_return_pct": str(total_return_pct),
            "max_drawdown_pct": str(max_drawdown_pct),
            "max_drawdown_quote": str(max_drawdown_quote),
            "fees_quote": str(sum((result.fees_quote for _, result in results), Decimal("0"))),
            "orders": orders,
            "filled_orders": sum(result.filled_orders for _, result in results),
            "round_trips": round_trips,
            "winning_round_trips": winning_round_trips,
            "win_rate_pct": str(Decimal(winning_round_trips) / Decimal(round_trips) * Decimal("100") if round_trips else Decimal("0")),
            "candle_count": sum(result.candle_count for _, result in results),
            "start_at": min(result.start_at for _, result in results),
            "end_at": max(result.end_at for _, result in results),
            "equity_curve": [{"timestamp": timestamp, "equity": str(equity)} for timestamp, equity in self._compact_curve(equity_points)],
            "trade_log": self._trade_log(results),
            "items": [self._item(dataset, result) for dataset, result in results],
            "failures": failures,
            "parameters": {
                "initial_quote": str(config.initial_quote),
                "initial_base": str(config.initial_base),
                "fee_bps": str(config.fee_bps),
                "slippage_bps": str(config.slippage_bps),
                "allocation_ratio": str(config.allocation_ratio),
                "momentum_threshold_pct": str(config.momentum_threshold_pct),
                "fast_window": config.fast_window,
                "slow_window": config.slow_window,
                "strategy_parameters": dict(config.parameters),
            },
            "created_at": created_at,
            "updated_at": created_at,
        }

    @staticmethod
    def _item(dataset: StoredDataset, result: CandleBacktestResult) -> dict[str, object]:
        return {
            "dataset_id": dataset.manifest.dataset_id,
            "venue_id": dataset.manifest.venue_id,
            "symbol": dataset.manifest.instrument_key.rsplit(":", 1)[-1],
            "interval": dataset.manifest.interval,
            "candle_count": result.candle_count,
            "initial_equity": str(result.initial_equity),
            "final_equity": str(result.final_equity),
            "total_return_pct": str(result.total_return_pct),
            "max_drawdown_pct": str(result.max_drawdown_pct),
            "fees_quote": str(result.fees_quote),
            "orders": result.orders,
            "round_trips": result.round_trips,
            "win_rate_pct": str(result.win_rate_pct),
        }

    @staticmethod
    def _trade_log(results: list[tuple[StoredDataset, CandleBacktestResult]]) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        for dataset, result in results:
            for trade in result.trade_log:
                entries.append({"venue_id": dataset.manifest.venue_id, "symbol": dataset.manifest.instrument_key.rsplit(":", 1)[-1], **trade})
        entries.sort(key=lambda item: str(item.get("timestamp", "")))
        return entries[-100:]

    @staticmethod
    def _aggregate_curves(results: list[tuple[StoredDataset, CandleBacktestResult]]) -> list[tuple[str, Decimal]]:
        timestamps = sorted({str(point["timestamp"]) for _, result in results for point in result.equity_curve})
        if not timestamps:
            return []
        values: list[tuple[str, Decimal]] = []
        for timestamp in timestamps:
            equity = Decimal("0")
            for _, result in results:
                current = result.initial_equity
                for point in result.equity_curve:
                    if str(point["timestamp"]) <= timestamp:
                        current = Decimal(str(point["equity"]))
                    else:
                        break
                equity += current
            values.append((timestamp, equity))
        return values

    @staticmethod
    def _drawdown(points: list[tuple[str, Decimal]]) -> tuple[Decimal, Decimal]:
        peak = Decimal("0")
        max_quote = Decimal("0")
        max_pct = Decimal("0")
        for _, equity in points:
            peak = max(peak, equity)
            if peak > 0:
                drawdown = peak - equity
                max_quote = max(max_quote, drawdown)
                max_pct = max(max_pct, drawdown / peak * Decimal("100"))
        return max_quote, max_pct

    @staticmethod
    def _compact_curve(points: list[tuple[str, Decimal]], maximum: int = 240) -> tuple[tuple[str, Decimal], ...]:
        if len(points) <= maximum:
            return tuple(points)
        step = (len(points) - 1) / (maximum - 1)
        indexes = {round(index * step) for index in range(maximum)}
        return tuple(points[index] for index in sorted(indexes))
