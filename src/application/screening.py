"""Deterministic OHLCV screening over verified history datasets."""

from __future__ import annotations

import math
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from threading import RLock
from uuid import uuid4

from application.history_storage import HistoryStorage, StoredDataset
from application.pool_catalog import PoolCatalog
from application.task_lifecycle import TaskLedger, TaskLifecycle
from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore


class ScreeningService:
    def __init__(
        self,
        storage: HistoryStorage,
        pools: PoolCatalog,
        *,
        state_path: str | None = None,
        state_store: StateStore | None = None,
        task_store: TaskLedger | None = None,
    ) -> None:
        self._storage = storage
        self._pools = pools
        self._task_store = task_store
        self._runs: dict[str, dict[str, object]] = {}
        self._lock = RLock()
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._load()

    def run(
        self,
        *,
        pool_id: str,
        interval: str | None,
        lookback: int,
        min_return_pct: Decimal,
        max_volatility_pct: Decimal | None,
        min_quote_volume: Decimal,
        min_momentum_pct: Decimal,
        limit: int,
        run_id: str | None = None,
        record_task: bool = True,
    ) -> dict[str, object]:
        if lookback < 2 or lookback > 10000:
            raise ValueError("lookback must be between 2 and 10000")
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        if min_return_pct < 0 or min_quote_volume < 0 or min_momentum_pct < 0:
            raise ValueError("screening thresholds must not be negative")
        if max_volatility_pct is not None and max_volatility_pct < 0:
            raise ValueError("max_volatility_pct must not be negative")

        datasets = self._pools.datasets_for(pool_id)
        selected_interval = None if interval in (None, "", "all") else str(interval).strip().lower()
        candidates: list[dict[str, object]] = []
        blocked = 0
        for dataset in datasets:
            if selected_interval and dataset.manifest.interval != selected_interval:
                continue
            if dataset.manifest.gap_count or dataset.manifest.duplicate_count:
                blocked += 1
                continue
            page = self._storage.read_all(dataset)
            rows = page.items[-lookback:]
            if len(rows) < 2:
                blocked += 1
                continue
            metrics = self._metrics(dataset, rows)
            reasons = []
            if Decimal(metrics["period_return_pct"]) < min_return_pct:
                reasons.append("RETURN_BELOW_THRESHOLD")
            if max_volatility_pct is not None and Decimal(metrics["volatility_pct"]) > max_volatility_pct:
                reasons.append("VOLATILITY_ABOVE_LIMIT")
            if Decimal(metrics["average_quote_volume"]) < min_quote_volume:
                reasons.append("VOLUME_BELOW_THRESHOLD")
            if Decimal(metrics["momentum_pct"]) < min_momentum_pct:
                reasons.append("MOMENTUM_BELOW_THRESHOLD")
            metrics["passed"] = not reasons
            metrics["reasons"] = reasons
            candidates.append(metrics)

        candidates.sort(key=lambda item: (bool(item["passed"]), Decimal(str(item["score"]))), reverse=True)
        result = {
            "run_id": str(run_id or uuid4().hex).strip(),
            "pool_id": pool_id,
            "interval": selected_interval or "all",
            "lookback": lookback,
            "filters": {
                "min_return_pct": str(min_return_pct),
                "max_volatility_pct": None if max_volatility_pct is None else str(max_volatility_pct),
                "min_quote_volume": str(min_quote_volume),
                "min_momentum_pct": str(min_momentum_pct),
            },
            "dataset_count": len(candidates),
            "blocked_dataset_count": blocked,
            "candidate_count": sum(1 for item in candidates if item["passed"]),
            "items": candidates[:limit],
            "created_at": datetime.now(UTC).isoformat(),
            "status": "completed",
        }
        with self._lock:
            self._refresh_external_locked()
            self._runs[str(result["run_id"])] = result
            self._persist()
        if record_task:
            TaskLifecycle(
                self._task_store,
                f"screening:{result['run_id']}",
                kind="screening",
                title="历史指标筛选",
                payload={"pool_id": pool_id, "interval": result["interval"], "lookback": lookback},
            ).completed(
                result={
                    "run_id": result["run_id"],
                    "pool_id": pool_id,
                    "dataset_count": result["dataset_count"],
                    "candidate_count": result["candidate_count"],
                    "blocked_dataset_count": result["blocked_dataset_count"],
                },
                progress={
                    "completed": result["dataset_count"],
                    "total": result["dataset_count"] + result["blocked_dataset_count"],
                    "candidates": result["candidate_count"],
                    "percent": 100.0,
                },
            )
        return deepcopy(result)

    def list(self, limit: int = 30) -> list[dict[str, object]]:
        with self._lock:
            self._refresh_external_locked()
            records = sorted(self._runs.values(), key=lambda item: str(item["created_at"]), reverse=True)
            return [deepcopy(item) for item in records[: max(1, min(int(limit), 100))]]

    def get(self, run_id: str) -> dict[str, object] | None:
        with self._lock:
            self._refresh_external_locked()
            record = self._runs.get(str(run_id))
            return None if record is None else deepcopy(record)

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
            raise RuntimeError("screening run state is unreadable") from error
        raw_runs = payload.get("runs", []) if isinstance(payload, dict) else []
        if not isinstance(raw_runs, list):
            raise RuntimeError("screening run state must contain an array")
        loaded: dict[str, dict[str, object]] = {}
        for raw_run in raw_runs:
            if isinstance(raw_run, dict) and str(raw_run.get("run_id", "")).strip():
                loaded[str(raw_run["run_id"])] = deepcopy(raw_run)
        self._runs = loaded

    def _persist(self) -> None:
        if self._state is not None:
            try:
                self._state.save({"version": 1, "runs": list(self._runs.values())})
            except JsonStateError as error:
                raise RuntimeError("screening run state cannot be saved") from error

    @classmethod
    def _metrics(cls, dataset: StoredDataset, rows) -> dict[str, object]:
        first = rows[0]
        last = rows[-1]
        period_return = last.close / first.close - Decimal("1")
        momentum_window = min(len(rows), 20)
        momentum = last.close / rows[-momentum_window].close - Decimal("1")
        returns = [rows[index].close / rows[index - 1].close - Decimal("1") for index in range(1, len(rows))]
        average_return = sum(returns, Decimal("0")) / Decimal(len(returns))
        variance = sum((value - average_return) ** 2 for value in returns) / Decimal(len(returns))
        annual_periods = Decimal("365") if last.interval.value == "1d" else Decimal("365") * Decimal("24")
        volatility = Decimal(str(math.sqrt(float(variance)) * math.sqrt(float(annual_periods))))
        peak = first.close
        max_drawdown = Decimal("0")
        for row in rows:
            peak = max(peak, row.close)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - row.close) / peak)
        average_volume = sum((row.quote_volume for row in rows), Decimal("0")) / Decimal(len(rows))
        score = period_return * Decimal("100") + momentum * Decimal("50") - volatility * Decimal("10")
        return {
            "dataset_id": dataset.manifest.dataset_id,
            "venue_id": dataset.manifest.venue_id,
            "symbol": str(dataset.manifest.instrument_key).rsplit(":", 1)[-1],
            "interval": dataset.manifest.interval,
            "row_count": len(rows),
            "start_at": first.open_time.isoformat(),
            "end_at": last.close_time.isoformat(),
            "last_close": str(last.close),
            "period_return_pct": str(period_return * Decimal("100")),
            "momentum_pct": str(momentum * Decimal("100")),
            "volatility_pct": str(volatility * Decimal("100")),
            "max_drawdown_pct": str(max_drawdown * Decimal("100")),
            "average_quote_volume": str(average_volume),
            "score": str(score),
        }
