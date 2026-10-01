"""Bounded strategy-by-dataset matrix research runs."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock
from uuid import uuid4

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore
from application.candle_backtest import CandleBacktestConfig, CandleBacktestEngine, strategy_catalog
from application.history_storage import HistoryStorage, HistoryStorageError
from application.task_lifecycle import TaskLedger, TaskLifecycle


class StrategyMatrixError(ValueError):
    """Raised when a matrix cannot be created or run safely."""


class StrategyMatrixService:
    """Persist matrix definitions and execute a bounded deterministic grid."""

    def __init__(
        self,
        storage: HistoryStorage,
        registry,
        *,
        state_path: str | Path | None = None,
        state_store: StateStore | None = None,
        task_store: TaskLedger | None = None,
    ) -> None:
        self._storage = storage
        self._registry = registry
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._matrices: dict[str, dict[str, object]] = {}
        self._lock = RLock()
        self._engine = CandleBacktestEngine()
        self._task_store = task_store
        self._load()

    def list(self, limit: int = 50) -> list[dict[str, object]]:
        with self._lock:
            self._refresh_external_locked()
            records = sorted(self._matrices.values(), key=lambda item: str(item.get("updated_at", "")), reverse=True)
            return [deepcopy(item) for item in records[: max(1, min(int(limit), 100))]]

    def get(self, matrix_id: str) -> dict[str, object] | None:
        with self._lock:
            self._refresh_external_locked()
            item = self._matrices.get(str(matrix_id))
            return deepcopy(item) if item is not None else None

    def create(
        self,
        *,
        name: str,
        strategy_ids: list[str],
        dataset_ids: list[str],
        interval: str,
        config: CandleBacktestConfig,
        parameter_sets: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, object]:
        with self._lock:
            self._refresh_external_locked()
        title = str(name).strip()
        if not title:
            raise StrategyMatrixError("matrix name must not be empty")
        normalized_strategies = list(dict.fromkeys(str(item).strip().lower() for item in strategy_ids if str(item).strip()))
        normalized_datasets = list(dict.fromkeys(str(item).strip() for item in dataset_ids if str(item).strip()))
        if not normalized_strategies or len(normalized_strategies) > 10:
            raise StrategyMatrixError("matrix must contain between 1 and 10 strategies")
        if not normalized_datasets or len(normalized_datasets) > 20:
            raise StrategyMatrixError("matrix must contain between 1 and 20 datasets")
        self._validate_strategies(normalized_strategies)
        if str(interval).strip().lower() not in {"1d", "1h", "5m"}:
            raise StrategyMatrixError("interval must be 1d, 1h, or 5m")
        parameter_sets = deepcopy(parameter_sets or {})
        unknown = sorted(set(parameter_sets) - set(normalized_strategies))
        if unknown:
            raise StrategyMatrixError(f"parameter_sets contain unknown strategies: {', '.join(unknown)}")
        now = datetime.now(UTC).isoformat()
        matrix_id = f"matrix-{uuid4().hex}"
        record = {
            "matrix_id": matrix_id,
            "name": title[:120],
            "strategy_ids": normalized_strategies,
            "dataset_ids": normalized_datasets,
            "interval": str(interval).strip().lower(),
            "base_config": self._config_dict(config),
            "parameter_sets": parameter_sets,
            "status": "draft",
            "last_run": None,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._matrices[matrix_id] = record
            self._persist_locked()
            return deepcopy(record)

    def run(
        self,
        matrix_id: str,
        *,
        run_id: str | None = None,
        record_task: bool = True,
    ) -> dict[str, object]:
        with self._lock:
            self._refresh_external_locked()
            matrix = self._matrices.get(str(matrix_id))
            if matrix is None:
                raise KeyError(matrix_id)
            matrix = deepcopy(matrix)
        strategy_ids = [str(item) for item in matrix["strategy_ids"]]
        dataset_ids = [str(item) for item in matrix["dataset_ids"]]
        items: list[dict[str, object]] = []
        blocked = 0
        for dataset_id in dataset_ids:
            dataset = self._dataset(dataset_id)
            if dataset is None:
                blocked += len(strategy_ids)
                for strategy_id in strategy_ids:
                    items.append({"strategy_id": strategy_id, "dataset_id": dataset_id, "status": "blocked", "reason": "dataset was not found"})
                continue
            if dataset.manifest.interval != matrix["interval"]:
                blocked += len(strategy_ids)
                for strategy_id in strategy_ids:
                    items.append({"strategy_id": strategy_id, "dataset_id": dataset_id, "status": "blocked", "reason": "dataset interval does not match matrix"})
                continue
            if dataset.manifest.gap_count or dataset.manifest.duplicate_count:
                blocked += len(strategy_ids)
                for strategy_id in strategy_ids:
                    items.append({"strategy_id": strategy_id, "dataset_id": dataset_id, "status": "blocked", "reason": "dataset quality gate did not pass"})
                continue
            try:
                page = self._storage.read_all(dataset)
            except (HistoryStorageError, ValueError) as error:
                blocked += len(strategy_ids)
                for strategy_id in strategy_ids:
                    items.append({"strategy_id": strategy_id, "dataset_id": dataset_id, "status": "blocked", "reason": str(error)})
                continue
            if not page.items:
                blocked += len(strategy_ids)
                for strategy_id in strategy_ids:
                    items.append({"strategy_id": strategy_id, "dataset_id": dataset_id, "status": "blocked", "reason": "dataset is empty"})
                continue
            for strategy_id in strategy_ids:
                try:
                    self._registry.assert_enabled(strategy_id, "backtest")
                    parameters = dict(matrix.get("parameter_sets", {}).get(strategy_id, {}))
                    config = self._config(matrix["base_config"], strategy_id, parameters)
                    result = self._engine.run(
                        page.items,
                        config=config,
                        run_id=f"matrix-run-{uuid4().hex[:12]}",
                        dataset_id=dataset_id,
                    )
                except (ValueError, KeyError) as error:
                    blocked += 1
                    items.append({"strategy_id": strategy_id, "dataset_id": dataset_id, "status": "blocked", "reason": str(error)})
                    continue
                items.append(
                    {
                        "strategy_id": strategy_id,
                        "dataset_id": dataset_id,
                        "status": "completed",
                        "total_return_pct": str(result.total_return_pct),
                        "max_drawdown_pct": str(result.max_drawdown_pct),
                        "fees_quote": str(result.fees_quote),
                        "orders": result.orders,
                        "round_trips": result.round_trips,
                        "win_rate_pct": str(result.win_rate_pct),
                        "final_equity": str(result.final_equity),
                        "parameters": parameters,
                    }
                )
        completed = [item for item in items if item.get("status") == "completed"]
        completed.sort(key=lambda item: self._number(item.get("total_return_pct")), reverse=True)
        run = {
            "run_id": str(run_id or f"matrix-run-{uuid4().hex}"),
            "status": "completed" if completed and not blocked else "partial" if completed else "blocked",
            "created_at": datetime.now(UTC).isoformat(),
            "item_count": len(items),
            "completed_count": len(completed),
            "blocked_count": blocked,
            "items": items,
            "best": deepcopy(completed[0]) if completed else None,
            "research_only": True,
        }
        with self._lock:
            stored = self._matrices.get(str(matrix_id))
            if stored is not None:
                stored["status"] = run["status"]
                stored["last_run"] = deepcopy(run)
                stored["updated_at"] = datetime.now(UTC).isoformat()
                self._persist_locked()
        if record_task:
            TaskLifecycle(
                self._task_store,
                f"strategy-matrix:{run['run_id']}",
                kind="strategy_matrix",
                title="策略矩阵研究",
                payload={"matrix_id": matrix_id, "run_id": run["run_id"]},
            ).completed(
                result={
                    "matrix_id": matrix_id,
                    "run_id": run["run_id"],
                    "status": run["status"],
                    "completed_count": run["completed_count"],
                    "blocked_count": run["blocked_count"],
                },
                progress={
                    "completed": run["completed_count"],
                    "total": run["item_count"],
                    "percent": 100.0,
                },
            )
        return {"matrix": self.get(matrix_id), "run": run}

    def summary(self) -> dict[str, object]:
        items = self.list(100)
        runs = [item.get("last_run") for item in items if isinstance(item.get("last_run"), dict)]
        return {
            "matrix_count": len(items),
            "run_count": len(runs),
            "completed_run_count": sum(1 for item in runs if item.get("status") == "completed"),
            "items": items,
        }

    def _dataset(self, dataset_id: str):
        try:
            return next((item for item in self._storage.list_datasets() if item.manifest.dataset_id == dataset_id), None)
        except HistoryStorageError as error:
            raise StrategyMatrixError("verified history is unavailable") from error

    @staticmethod
    def _validate_strategies(strategy_ids: list[str]) -> None:
        known = {str(item["strategy_id"]) for item in strategy_catalog()}
        unknown = sorted(set(strategy_ids) - known)
        if unknown:
            raise StrategyMatrixError(f"unknown strategies: {', '.join(unknown)}")

    @staticmethod
    def _config_dict(config: CandleBacktestConfig) -> dict[str, object]:
        return {
            "initial_quote": str(config.initial_quote),
            "initial_base": str(config.initial_base),
            "fee_bps": str(config.fee_bps),
            "slippage_bps": str(config.slippage_bps),
            "fast_window": config.fast_window,
            "slow_window": config.slow_window,
            "allocation_ratio": str(config.allocation_ratio),
            "momentum_threshold_pct": str(config.momentum_threshold_pct),
        }

    @staticmethod
    def _config(base: dict[str, object], strategy_id: str, parameters: dict[str, object]) -> CandleBacktestConfig:
        return CandleBacktestConfig(
            strategy_id=strategy_id,
            initial_quote=Decimal(str(base["initial_quote"])),
            initial_base=Decimal(str(base["initial_base"])),
            fee_bps=Decimal(str(base["fee_bps"])),
            slippage_bps=Decimal(str(base["slippage_bps"])),
            fast_window=int(base["fast_window"]),
            slow_window=int(base["slow_window"]),
            allocation_ratio=Decimal(str(base["allocation_ratio"])),
            momentum_threshold_pct=Decimal(str(base["momentum_threshold_pct"])),
            parameters=parameters,
        )

    @staticmethod
    def _number(value: object) -> Decimal:
        try:
            result = Decimal(str(value or "-Infinity"))
        except (InvalidOperation, ValueError, TypeError):
            return Decimal("-Infinity")
        return result if result.is_finite() else Decimal("-Infinity")

    def _load(self) -> None:
        if self._state is None:
            return
        with self._lock:
            self._refresh_external_locked()

    def _refresh_external_locked(self) -> None:
        if self._state is None:
            return
        try:
            payload = self._state.load({"version": 1, "matrices": []})
        except JsonStateError as error:
            raise RuntimeError("strategy matrix state is unreadable") from error
        records = payload.get("matrices", []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            raise RuntimeError("strategy matrix state must contain an array")
        loaded: dict[str, dict[str, object]] = {}
        for record in records:
            if isinstance(record, dict) and str(record.get("matrix_id", "")).strip():
                loaded[str(record["matrix_id"])] = deepcopy(record)
        self._matrices = loaded

    def _persist_locked(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save({"version": 1, "matrices": list(self._matrices.values())})
        except JsonStateError as error:
            raise RuntimeError("strategy matrix state cannot be saved") from error
