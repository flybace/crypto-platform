"""Research orchestration for portfolio runs, comparisons, and bounded tuning."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import json
import os
import tempfile
from itertools import product
from pathlib import Path
from threading import RLock
from uuid import uuid4

from application.candle_backtest import CandleBacktestConfig, CandleBacktestEngine, strategy_catalog
from application.history_storage import HistoryStorage, HistoryStorageError, StoredDataset
from application.portfolio_backtest import PortfolioBacktestError, PortfolioBacktestRunner
from application.pool_catalog import PoolCatalog
from application.screening import ScreeningService
from application.task_lifecycle import TaskLedger, TaskLifecycle
from backend.app.services.history_requests import normalize_symbol
from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore


class ResearchRunError(ValueError):
    """A research request cannot be evaluated with verified history."""


class ResearchRunService:
    """Keep bounded research results durable without coupling them to execution."""

    def __init__(
        self,
        storage: HistoryStorage,
        pools: PoolCatalog,
        screening: ScreeningService | None = None,
        *,
        state_path: str | Path | None = None,
        state_store: StateStore | None = None,
        task_store: TaskLedger | None = None,
    ) -> None:
        self._storage = storage
        self._pools = pools
        self._screening = screening
        self._engine = CandleBacktestEngine()
        self._portfolio = PortfolioBacktestRunner(storage, self._engine)
        self._runs: dict[str, dict[str, object]] = {}
        self._lock = RLock()
        self._state_path = Path(state_path) if state_store is None and state_path else None
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._task_store = task_store
        self._load()

    def list(self, limit: int = 30) -> list[dict[str, object]]:
        with self._lock:
            self._refresh_external_locked()
            records = sorted(self._runs.values(), key=lambda item: str(item.get("created_at", "")), reverse=True)
            return [deepcopy(item) for item in records[: max(1, min(int(limit), 100))]]

    def get(self, run_id: str) -> dict[str, object] | None:
        with self._lock:
            self._refresh_external_locked()
            item = self._runs.get(str(run_id))
            return None if item is None else deepcopy(item)

    def portfolio(
        self,
        *,
        pool_id: str,
        interval: str,
        strategy_id: str,
        config: CandleBacktestConfig,
        screening_run_id: str | None = None,
        max_datasets: int = 20,
        run_id: str | None = None,
        record_task: bool = True,
    ) -> dict[str, object]:
        pool = self._pools.get(pool_id)
        if pool is None:
            raise ResearchRunError("pool was not found")
        candidate_ids: set[str] | None = None
        if screening_run_id:
            if self._screening is None:
                raise ResearchRunError("screening service is not configured")
            screening = self._screening.get(screening_run_id)
            if screening is None:
                raise ResearchRunError("screening run was not found")
            if str(screening.get("pool_id", "")) != pool_id:
                raise ResearchRunError("screening run belongs to a different pool")
            screening_interval = str(screening.get("interval", "all")).strip().lower()
            if screening_interval not in {"all", interval}:
                raise ResearchRunError("screening run interval does not match portfolio interval")
            raw_items = screening.get("items", [])
            candidate_ids = {
                str(item["dataset_id"])
                for item in raw_items
                if isinstance(item, dict)
                and item.get("passed") is True
                and str(item.get("dataset_id", "")).strip()
            }
            if not candidate_ids:
                raise ResearchRunError("screening run has no passed datasets")
        datasets = [
            dataset
            for dataset in self._pools.datasets_for(pool_id)
            if dataset.manifest.interval == interval
            and not dataset.manifest.gap_count
            and not dataset.manifest.duplicate_count
            and (candidate_ids is None or dataset.manifest.dataset_id in candidate_ids)
        ][: max(1, min(int(max_datasets), 50))]
        try:
            result = self._portfolio.run(datasets, config=replace(config, strategy_id=strategy_id), max_datasets=max_datasets)
        except (PortfolioBacktestError, HistoryStorageError) as error:
            raise ResearchRunError(str(error)) from error
        result.update({"kind": "portfolio", "pool_id": pool_id, "screening_run_id": screening_run_id})
        if run_id:
            result["run_id"] = str(run_id).strip()
        self._save(result, record_task=record_task)
        return deepcopy(result)

    def compare(
        self,
        *,
        venue_id: str,
        symbol: str,
        interval: str,
        strategy_ids: list[str],
        config: CandleBacktestConfig,
        parameters: dict[str, dict[str, object]] | None = None,
        run_id: str | None = None,
        record_task: bool = True,
    ) -> dict[str, object]:
        dataset = self._dataset(venue_id, symbol, interval)
        candles = self._candles(dataset)
        normalized_ids = self._strategy_ids(strategy_ids)
        items: list[dict[str, object]] = []
        for strategy_id in normalized_ids:
            strategy_parameters = dict((parameters or {}).get(strategy_id, {}))
            child_config = replace(config, strategy_id=strategy_id, parameters=strategy_parameters)
            try:
                result = self._engine.run(candles, config=child_config, run_id=f"compare-{uuid4().hex[:12]}", dataset_id=dataset.manifest.dataset_id)
            except ValueError as error:
                items.append({"strategy_id": strategy_id, "status": "blocked", "reason": str(error)})
                continue
            items.append({
                "strategy_id": strategy_id,
                "strategy_version": "1.0.0",
                "status": "completed",
                "total_return_pct": str(result.total_return_pct),
                "max_drawdown_pct": str(result.max_drawdown_pct),
                "fees_quote": str(result.fees_quote),
                "orders": result.orders,
                "round_trips": result.round_trips,
                "win_rate_pct": str(result.win_rate_pct),
                "final_equity": str(result.final_equity),
                "score": str(self._score(result)),
                "parameters": strategy_parameters,
            })
        items.sort(key=lambda item: Decimal(str(item.get("score", "-999999"))), reverse=True)
        result = self._envelope(
            "strategy_compare",
            {
                "venue_id": dataset.manifest.venue_id,
                "symbol": dataset.manifest.instrument_key.rsplit(":", 1)[-1],
                "interval": interval,
                "dataset": self._storage.dataset_dict(dataset),
                "strategy_ids": normalized_ids,
                "items": items,
                "best_strategy_id": items[0]["strategy_id"] if items and items[0].get("status") == "completed" else None,
            },
            run_id=run_id,
        )
        self._save(result, record_task=record_task)
        return deepcopy(result)

    def tune(
        self,
        *,
        venue_id: str,
        symbol: str,
        interval: str,
        strategy_id: str,
        config: CandleBacktestConfig,
        parameter_grid: dict[str, list[object]],
        max_runs: int = 30,
        run_id: str | None = None,
        record_task: bool = True,
    ) -> dict[str, object]:
        dataset = self._dataset(venue_id, symbol, interval)
        candles = self._candles(dataset)
        base_parameters = dict(config.parameters)
        variants = self._variants(parameter_grid, max_runs)
        if not variants:
            raise ResearchRunError("parameter_grid must contain at least one bounded variant")
        required = max(
            CandleBacktestEngine.required_lookback(
                replace(config, strategy_id=strategy_id, parameters={**base_parameters, **variant})
            )
            for variant in variants
        )
        split = int(len(candles) * 0.7)
        if split < required or len(candles) - split < required:
            raise ResearchRunError("history is too short for train and validation tuning windows")
        train = candles[:split]
        validation = candles[split:]
        trials: list[dict[str, object]] = []
        for index, variant in enumerate(variants, start=1):
            merged_parameters = {**base_parameters, **variant}
            child_config = replace(config, strategy_id=strategy_id, parameters=merged_parameters)
            try:
                train_result = self._engine.run(train, config=child_config, run_id=f"tune-train-{index}", dataset_id=dataset.manifest.dataset_id)
                validation_result = self._engine.run(validation, config=child_config, run_id=f"tune-validation-{index}", dataset_id=dataset.manifest.dataset_id)
            except ValueError as error:
                trials.append({"trial": index, "status": "blocked", "parameters": merged_parameters, "reason": str(error)})
                continue
            robust_score = self._score(validation_result) - abs(validation_result.max_drawdown_pct) * Decimal("0.25")
            trials.append({
                "trial": index,
                "status": "completed",
                "parameters": merged_parameters,
                "robust_score": str(robust_score),
                "train": self._metrics(train_result),
                "validation": self._metrics(validation_result),
            })
        completed = [item for item in trials if item.get("status") == "completed"]
        completed.sort(key=lambda item: Decimal(str(item.get("robust_score", "-999999"))), reverse=True)
        result = self._envelope(
            "parameter_tune",
            {
                "venue_id": dataset.manifest.venue_id,
                "symbol": dataset.manifest.instrument_key.rsplit(":", 1)[-1],
                "interval": interval,
                "strategy_id": strategy_id,
                "dataset": self._storage.dataset_dict(dataset),
                "train_end_at": train[-1].close_time.isoformat(),
                "validation_start_at": validation[0].open_time.isoformat(),
                "trial_count": len(trials),
                "completed_trial_count": len(completed),
                "best": completed[0] if completed else None,
                "trials": completed[: max(1, min(int(max_runs), 100))] + [item for item in trials if item.get("status") != "completed"],
            },
            run_id=run_id,
        )
        self._save(result, record_task=record_task)
        return deepcopy(result)

    def strategy_screen(
        self,
        *,
        pool_id: str,
        interval: str,
        strategy_id: str,
        config: CandleBacktestConfig,
        lookback: int = 200,
        limit: int = 50,
        run_id: str | None = None,
        record_task: bool = True,
    ) -> dict[str, object]:
        if lookback < 2 or lookback > 10000:
            raise ResearchRunError("lookback must be between 2 and 10000")
        pool = self._pools.get(pool_id)
        if pool is None:
            raise ResearchRunError("pool was not found")
        items: list[dict[str, object]] = []
        blocked = 0
        child_config = replace(config, strategy_id=strategy_id)
        for dataset in self._pools.datasets_for(pool_id):
            if dataset.manifest.interval != interval or dataset.manifest.gap_count or dataset.manifest.duplicate_count:
                continue
            try:
                rows = self._candles(dataset)
            except ResearchRunError:
                blocked += 1
                continue
            rows = rows[-lookback:]
            if len(rows) < CandleBacktestEngine.required_lookback(child_config):
                blocked += 1
                continue
            signal = self._engine.signal_at(rows, len(rows) - 1, child_config)
            first = rows[0].close
            last = rows[-1].close
            period_return = (last / first - Decimal("1")) * Decimal("100")
            items.append({
                "dataset_id": dataset.manifest.dataset_id,
                "venue_id": dataset.manifest.venue_id,
                "symbol": dataset.manifest.instrument_key.rsplit(":", 1)[-1],
                "interval": interval,
                "signal": signal or "NONE",
                "passed": signal == "BUY",
                "last_close": str(last),
                "period_return_pct": str(period_return),
                "row_count": len(rows),
                "as_of": rows[-1].close_time.isoformat(),
            })
        items.sort(key=lambda item: (bool(item["passed"]), Decimal(str(item["period_return_pct"]))), reverse=True)
        result = self._envelope(
            "strategy_screen",
            {
                "pool_id": pool_id,
                "interval": interval,
                "strategy_id": strategy_id,
                "lookback": lookback,
                "dataset_count": len(items),
                "blocked_dataset_count": blocked,
                "candidate_count": sum(1 for item in items if item["passed"]),
                "items": items[: max(1, min(int(limit), 500))],
            },
            run_id=run_id,
        )
        self._save(result, record_task=record_task)
        return deepcopy(result)

    def _dataset(self, venue_id: str, symbol: str, interval: str) -> StoredDataset:
        try:
            base, quote, _ = normalize_symbol(venue_id, symbol)
            dataset = self._storage.find_dataset(
                venue_id=str(venue_id).strip().lower(),
                instrument_key=f"{str(venue_id).strip().lower()}:spot:{base}/{quote}",
                interval=str(interval).strip().lower(),
            )
        except (HistoryStorageError, ValueError) as error:
            raise ResearchRunError(str(error)) from error
        if dataset is None:
            raise ResearchRunError("verified history dataset was not found")
        if dataset.manifest.gap_count or dataset.manifest.duplicate_count:
            raise ResearchRunError("history dataset quality gate did not pass")
        return dataset

    def _candles(self, dataset: StoredDataset) -> tuple:
        try:
            page = self._storage.read_all(dataset)
        except HistoryStorageError as error:
            raise ResearchRunError("verified history is unavailable") from error
        if not page.items:
            raise ResearchRunError("verified history range is unavailable")
        return page.items

    @staticmethod
    def _strategy_ids(values: list[str]) -> list[str]:
        known = {str(item["strategy_id"]) for item in strategy_catalog()}
        result = []
        for value in values:
            key = str(value).strip().lower()
            if key in known and key not in result:
                result.append(key)
        if not result:
            raise ResearchRunError("strategy_ids must contain at least one known strategy")
        return result[:10]

    @staticmethod
    def _variants(parameter_grid: dict[str, list[object]], max_runs: int) -> list[dict[str, object]]:
        if not isinstance(parameter_grid, dict):
            raise ResearchRunError("parameter_grid must be an object")
        keys = [str(key).strip() for key in parameter_grid if str(key).strip()]
        values: list[list[object]] = []
        for key in keys:
            raw = parameter_grid[key]
            if not isinstance(raw, list) or not raw or len(raw) > 20:
                raise ResearchRunError(f"parameter_grid.{key} must be a non-empty list of at most 20 values")
            values.append(raw)
        if not keys:
            return []
        combinations = list(product(*values))
        if len(combinations) > max(1, min(int(max_runs), 100)):
            raise ResearchRunError("parameter grid exceeds max_runs")
        return [dict(zip(keys, combination)) for combination in combinations]

    @staticmethod
    def _score(result) -> Decimal:
        return result.total_return_pct - abs(result.max_drawdown_pct) * Decimal("0.5") + min(result.win_rate_pct, Decimal("100")) * Decimal("0.02")

    @staticmethod
    def _metrics(result) -> dict[str, object]:
        return {
            "total_return_pct": str(result.total_return_pct),
            "max_drawdown_pct": str(result.max_drawdown_pct),
            "fees_quote": str(result.fees_quote),
            "orders": result.orders,
            "round_trips": result.round_trips,
            "win_rate_pct": str(result.win_rate_pct),
            "final_equity": str(result.final_equity),
        }

    @staticmethod
    def _envelope(kind: str, payload: dict[str, object], *, run_id: str | None = None) -> dict[str, object]:
        now = datetime.now(UTC).isoformat()
        return {"run_id": str(run_id or f"{kind}-{uuid4().hex}"), "kind": kind, "status": "completed", "created_at": now, "updated_at": now, **payload}

    def _save(self, result: dict[str, object], *, record_task: bool = True) -> None:
        with self._lock:
            self._runs[str(result["run_id"])] = deepcopy(result)
            completed = int(result.get("completed_trial_count", result.get("candidate_count", result.get("dataset_count", 1))) or 0)
            total = int(result.get("trial_count", result.get("dataset_count", 1)) or 1)
            if record_task:
                TaskLifecycle(
                    self._task_store,
                    f"research:{result['run_id']}",
                    kind="research",
                    title="研究任务",
                    payload={"run_id": result["run_id"], "kind": result.get("kind", "research")},
                ).completed(
                    result={
                        "run_id": result["run_id"],
                        "kind": result.get("kind", "research"),
                        "status": result.get("status", "completed"),
                    },
                    progress={
                        "completed": max(0, min(completed, total)),
                        "total": max(1, total),
                        "percent": 100.0,
                    },
                )
            if self._state is None:
                return
            if self._state_path is None:
                try:
                    self._state.save({"version": 1, "runs": list(self._runs.values())})
                except JsonStateError as error:
                    raise RuntimeError("research run state cannot be saved") from error
                return
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary: str | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=self._state_path.parent, prefix=f".{self._state_path.name}.", suffix=".tmp", mode="w", encoding="utf-8", delete=False) as handle:
                    temporary = handle.name
                    json.dump({"version": 1, "runs": list(self._runs.values())}, handle, ensure_ascii=False, indent=2, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self._state_path)
            finally:
                if temporary:
                    try:
                        os.unlink(temporary)
                    except FileNotFoundError:
                        pass

    def _load(self) -> None:
        if self._state is None:
            return
        with self._lock:
            self._refresh_external_locked()

    def _refresh_external_locked(self) -> None:
        if self._state is None:
            return
        if self._state_path is None:
            try:
                payload = self._state.load({"version": 1, "runs": []})
            except JsonStateError as error:
                raise RuntimeError("research run state is unreadable") from error
        else:
            if not self._state_path.exists():
                return
            try:
                payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise RuntimeError("research run state is unreadable") from error
        records = payload.get("runs", []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            raise RuntimeError("research run state must contain an array")
        loaded: dict[str, dict[str, object]] = {}
        for record in records:
            if isinstance(record, dict) and str(record.get("run_id", "")).strip():
                loaded[str(record["run_id"])] = deepcopy(record)
        self._runs = loaded
