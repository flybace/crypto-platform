"""In-process backtest runs over verified historical datasets."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from threading import RLock
from uuid import uuid4

from application.candle_backtest import CandleBacktestConfig, CandleBacktestEngine
from application.history_storage import HistoryStorage, HistoryStorageError
from application.task_lifecycle import TaskLedger, TaskLifecycle
from backend.app.services.history_requests import normalize_symbol

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore


class BacktestDataError(ValueError):
    """The requested series cannot produce a trustworthy backtest."""


class BacktestRunManager:
    def __init__(
        self,
        storage: HistoryStorage,
        *,
        state_path: str | None = None,
        state_store: StateStore | None = None,
        task_store: TaskLedger | None = None,
    ) -> None:
        self._storage = storage
        self._engine = CandleBacktestEngine()
        self._task_store = task_store
        self._runs: dict[str, dict[str, object]] = {}
        self._lock = RLock()
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._load()

    def dataset_time_range(
        self,
        *,
        venue_id: str,
        symbol: str,
        interval: str,
    ) -> tuple[object, object]:
        """Return (start_at, end_at) of the verified history dataset.

        Used by parameter tuning to split train/validation windows.
        Raises BacktestDataError when the dataset is missing or fails the quality gate.
        """
        try:
            venue = str(venue_id).strip().lower()
            base, quote, _ = normalize_symbol(venue, symbol)
            instrument_key = f"{venue}:spot:{base}/{quote}"
            dataset = self._storage.find_dataset(
                venue_id=venue,
                instrument_key=instrument_key,
                interval=interval,
            )
        except HistoryStorageError as error:
            raise BacktestDataError("verified history is unavailable") from error
        if dataset is None:
            raise BacktestDataError("verified history dataset was not found")
        if dataset.manifest.gap_count or dataset.manifest.duplicate_count:
            raise BacktestDataError("history dataset quality gate did not pass")
        return (dataset.manifest.start_at, dataset.manifest.end_at)

    def run(
        self,
        *,
        venue_id: str,
        symbol: str,
        interval: str,
        start_at=None,
        end_at=None,
        config: CandleBacktestConfig,
        run_id: str | None = None,
        record_task: bool = True,
    ) -> dict[str, object]:
        try:
            venue = str(venue_id).strip().lower()
            base, quote, _ = normalize_symbol(venue, symbol)
            instrument_key = f"{venue}:spot:{base}/{quote}"
            dataset = self._storage.find_dataset(
                venue_id=venue,
                instrument_key=instrument_key,
                interval=interval,
            )
        except HistoryStorageError as error:
            raise BacktestDataError("verified history is unavailable") from error
        if dataset is None:
            raise BacktestDataError("verified history dataset was not found")
        if dataset.manifest.gap_count or dataset.manifest.duplicate_count:
            raise BacktestDataError("history dataset quality gate did not pass")
        try:
            page = self._storage.read_all(dataset, start_at=start_at, end_at=end_at)
        except HistoryStorageError as error:
            raise BacktestDataError("verified history is unavailable") from error
        if not page.items:
            raise BacktestDataError("backtest range contains no candles")
        run_id = str(run_id or uuid4().hex).strip()
        if not run_id:
            raise BacktestDataError("run_id must not be empty")
        try:
            result = self._engine.run(
                page.items,
                config=config,
                run_id=run_id,
                dataset_id=dataset.manifest.dataset_id,
            )
        except ValueError as error:
            raise BacktestDataError(str(error)) from error
        now = datetime.now(UTC).isoformat()
        record = result.as_dict()
        record.update(
            {
                "status": "completed",
                "created_at": now,
                "updated_at": now,
                "dataset": self._storage.dataset_dict(dataset),
                "parameters": {
                    "venue_id": venue,
                    "symbol": f"{base}/{quote}",
                    "interval": interval,
                    "start_at": None if start_at is None else start_at.isoformat(),
                    "end_at": None if end_at is None else end_at.isoformat(),
                    "initial_quote": str(config.initial_quote),
                    "initial_base": str(config.initial_base),
                    "fee_bps": str(config.fee_bps),
                    "slippage_bps": str(config.slippage_bps),
                    "fast_window": config.fast_window,
                    "slow_window": config.slow_window,
                    "allocation_ratio": str(config.allocation_ratio),
                    "momentum_threshold_pct": str(config.momentum_threshold_pct),
                    "strategy_parameters": dict(config.parameters),
                },
                "strategy_version": "1.0.0",
                "data_level": "KLINE",
            }
        )
        with self._lock:
            self._runs[run_id] = record
            self._persist_locked()
        if record_task:
            TaskLifecycle(
                self._task_store,
                f"backtest:{run_id}",
                kind="backtest",
                title="策略回测",
                payload={"run_id": run_id, "dataset_id": dataset.manifest.dataset_id},
            ).completed(
                result={
                    "run_id": run_id,
                    "dataset_id": dataset.manifest.dataset_id,
                    "total_return_pct": record.get("total_return_pct"),
                    "max_drawdown_pct": record.get("max_drawdown_pct"),
                    "orders": record.get("orders", 0),
                }
            )
        return self.get(run_id)

    def get(self, run_id: str) -> dict[str, object] | None:
        with self._lock:
            self._refresh_external_locked()
            record = self._runs.get(str(run_id))
            return None if record is None else deepcopy(record)

    def list(self, limit: int = 30) -> list[dict[str, object]]:
        with self._lock:
            self._refresh_external_locked()
            records = sorted(self._runs.values(), key=lambda item: str(item.get("created_at", "")), reverse=True)
            return [deepcopy(record) for record in records[: max(1, min(int(limit), 100))]]

    def summary(self, limit: int = 100) -> dict[str, object]:
        records = self.list(limit)
        completed = [item for item in records if item.get("status") == "completed"]
        returns = [self._decimal(item.get("total_return_pct")) for item in completed]
        best = self._ranked(completed, reverse=True)[:1]
        return {
            "run_count": len(records),
            "completed_count": len(completed),
            "strategy_count": len({str(item.get("strategy_id")) for item in completed}),
            "trade_count": sum(int(item.get("orders", 0) or 0) for item in completed),
            "average_return_pct": str(sum(returns, Decimal("0")) / Decimal(len(returns))) if returns else "0",
            "best_run": best[0] if best else None,
            "latest_run": records[0] if records else None,
        }

    def best(self, limit: int = 10) -> list[dict[str, object]]:
        with self._lock:
            self._refresh_external_locked()
            records = [item for item in self._runs.values() if item.get("status") == "completed"]
            return [
                deepcopy(item)
                for item in self._ranked(records, reverse=True)[: max(1, min(int(limit), 100))]
            ]

    def delete(self, run_id: str) -> bool:
        with self._lock:
            self._refresh_external_locked()
            if str(run_id) not in self._runs:
                return False
            del self._runs[str(run_id)]
            self._persist_locked()
            return True

    def clear(self) -> int:
        with self._lock:
            self._refresh_external_locked()
            count = len(self._runs)
            self._runs.clear()
            self._persist_locked()
            return count

    def _load(self) -> None:
        if self._state is None:
            return
        with self._lock:
            self._refresh_external_locked()

    def _refresh_external_locked(self) -> None:
        if self._state is None:
            return
        try:
            payload = self._state.load({"version": 1, "runs": []})
        except JsonStateError as error:
            raise RuntimeError("backtest run state is unreadable") from error
        raw_runs = payload.get("runs", []) if isinstance(payload, dict) else []
        if not isinstance(raw_runs, list):
            raise RuntimeError("backtest run state must contain an array")
        loaded: dict[str, dict[str, object]] = {}
        for raw_run in raw_runs:
            if isinstance(raw_run, dict) and str(raw_run.get("run_id", "")).strip():
                loaded[str(raw_run["run_id"])] = deepcopy(raw_run)
        self._runs = loaded

    def _persist_locked(self) -> None:
        if self._state is not None:
            try:
                self._state.save({"version": 1, "runs": list(self._runs.values())})
            except JsonStateError as error:
                raise RuntimeError("backtest run state cannot be saved") from error

    @staticmethod
    def _decimal(value: object) -> Decimal:
        try:
            parsed = Decimal(str(value or "0"))
        except (InvalidOperation, ValueError):
            return Decimal("-Infinity")
        return parsed if parsed.is_finite() else Decimal("-Infinity")

    @classmethod
    def _ranked(cls, records: list[dict[str, object]], *, reverse: bool) -> list[dict[str, object]]:
        return sorted(
            records,
            key=lambda item: (cls._decimal(item.get("total_return_pct")), str(item.get("created_at", ""))),
            reverse=reverse,
        )
