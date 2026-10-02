"""Automated paper-trading follow: scheduled replays, performance tracking, alerts.

The follow loop re-runs the enabled paper-automation profile against the latest
verified history, compares the strategy return against a buy-and-hold benchmark
over the same dataset, and keeps a bounded timeline of snapshots. Alert codes are
raised when the strategy drops below a return threshold, breaches a drawdown
limit, or trails the market by more than the configured tolerance.

This is replay-only bookkeeping: it never places exchange orders and it never
touches real execution.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock
from typing import Any

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore


DEFAULT_FOLLOW: dict[str, Any] = {
    "enabled": False,
    "interval_seconds": 86400,
    "alert_min_return_pct": "0",
    "alert_max_drawdown_pct": "20",
    "alert_underperform_pct": "5",
    "last_follow_at": None,
    "last_dispatched_at": None,
    "last_follow_run_id": None,
    "updated_at": None,
}

SNAPSHOT_LIMIT = 60
MIN_INTERVAL_SECONDS = 3600
MAX_INTERVAL_SECONDS = 604800


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _decimal(value: Any, name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be numeric") from error
    if not parsed.is_finite():
        raise ValueError(f"{name} must be finite")
    return parsed


class PaperFollowService:
    """Persist follow configuration and the snapshot timeline.

    Cross-process reads use the state-file mtime so the scheduler, worker and
    web backend all observe each other's updates without shared memory.
    """

    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        state_store: StateStore | None = None,
        snapshot_limit: int = SNAPSHOT_LIMIT,
    ) -> None:
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._path = Path(state_path) if state_path else None
        self._limit = max(1, int(snapshot_limit))
        self._lock = RLock()
        self._config = deepcopy(DEFAULT_FOLLOW)
        self._snapshots: list[dict[str, Any]] = []
        self._mtime_ns = 0
        self._load()

    def get(self) -> dict[str, Any]:
        with self._lock:
            self._reload_if_changed_locked()
            return {
                "config": deepcopy(self._config),
                "snapshots": deepcopy(self._snapshots),
                "latest": deepcopy(self._snapshots[0]) if self._snapshots else None,
            }

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._reload_if_changed_locked()
            next_config = deepcopy(self._config)
            for key, value in values.items():
                if value is not None and key in DEFAULT_FOLLOW and key not in {
                    "last_follow_at",
                    "last_dispatched_at",
                    "last_follow_run_id",
                }:
                    next_config[key] = value
            self._validate_config(next_config)
            next_config["updated_at"] = _utcnow_iso()
            self._config = next_config
            self._persist_locked()
            return {
                "config": deepcopy(self._config),
                "snapshots": deepcopy(self._snapshots),
                "latest": deepcopy(self._snapshots[0]) if self._snapshots else None,
            }

    def should_run(self, now: datetime | None = None) -> bool:
        """True when follow is enabled and the interval has elapsed."""
        with self._lock:
            self._reload_if_changed_locked()
            if not self._config.get("enabled"):
                return False
            current = now or datetime.now(UTC)
            interval = int(self._config["interval_seconds"])
            last_follow = self._parse_time(self._config.get("last_follow_at"))
            if last_follow is not None and (current - last_follow).total_seconds() < interval:
                return False
            last_dispatched = self._parse_time(self._config.get("last_dispatched_at"))
            if last_dispatched is not None and (current - last_dispatched).total_seconds() < interval:
                return False
            return True

    def mark_dispatched(self) -> None:
        with self._lock:
            self._reload_if_changed_locked()
            self._config["last_dispatched_at"] = _utcnow_iso()
            self._persist_locked()

    def record_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Append one follow snapshot, evaluate alerts, persist."""
        with self._lock:
            self._reload_if_changed_locked()
            record = deepcopy(snapshot)
            record["at"] = str(record.get("at") or _utcnow_iso())
            alerts = self._evaluate_alerts(record)
            record["alerts"] = alerts
            self._snapshots.insert(0, record)
            self._snapshots = self._snapshots[: self._limit]
            self._config["last_follow_at"] = record["at"]
            self._config["last_follow_run_id"] = record.get("run_id")
            self._persist_locked()
            return deepcopy(record)

    @staticmethod
    def _validate_config(config: dict[str, Any]) -> None:
        if not isinstance(config.get("enabled"), bool):
            raise ValueError("enabled must be boolean")
        try:
            interval = int(config.get("interval_seconds"))
        except (TypeError, ValueError) as error:
            raise ValueError("interval_seconds must be an integer") from error
        if interval < MIN_INTERVAL_SECONDS or interval > MAX_INTERVAL_SECONDS:
            raise ValueError(
                f"interval_seconds must be between {MIN_INTERVAL_SECONDS} and {MAX_INTERVAL_SECONDS}"
            )
        for key in ("alert_min_return_pct", "alert_max_drawdown_pct", "alert_underperform_pct"):
            value = _decimal(config.get(key), key)
            if value < 0:
                raise ValueError(f"{key} must be non-negative")

    def _evaluate_alerts(self, record: dict[str, Any]) -> list[str]:
        alerts: list[str] = []
        strategy_return = _decimal(record.get("strategy_return_pct", "0"), "strategy_return_pct")
        market_return = _decimal(record.get("market_return_pct", "0"), "market_return_pct")
        max_drawdown = _decimal(record.get("max_drawdown_pct", "0"), "max_drawdown_pct")
        if strategy_return < _decimal(self._config["alert_min_return_pct"], "alert_min_return_pct"):
            alerts.append("return_below_threshold")
        if max_drawdown > _decimal(self._config["alert_max_drawdown_pct"], "alert_max_drawdown_pct"):
            alerts.append("drawdown_breach")
        excess = strategy_return - market_return
        if excess < -_decimal(self._config["alert_underperform_pct"], "alert_underperform_pct"):
            alerts.append("underperforms_market")
        return alerts

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed

    def _load(self) -> None:
        if self._state is None:
            return
        try:
            payload = self._state.load({"version": 1, "config": DEFAULT_FOLLOW, "snapshots": []})
        except JsonStateError as error:
            raise RuntimeError("paper follow state is unreadable") from error
        if not isinstance(payload, dict):
            raise RuntimeError("paper follow state must contain an object")
        next_config = deepcopy(DEFAULT_FOLLOW)
        raw_config = payload.get("config")
        if isinstance(raw_config, dict):
            next_config.update({key: raw_config[key] for key in DEFAULT_FOLLOW if key in raw_config})
        self._validate_config(next_config)
        snapshots = payload.get("snapshots")
        self._config = next_config
        self._snapshots = [dict(item) for item in snapshots if isinstance(item, dict)][: self._limit]
        self._mtime_ns = self._current_mtime_ns()

    def _persist_locked(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save(
                {"version": 1, "config": self._config, "snapshots": self._snapshots}
            )
            self._mtime_ns = self._current_mtime_ns()
        except JsonStateError as error:
            raise RuntimeError("paper follow state cannot be saved") from error

    def _reload_if_changed_locked(self) -> None:
        if self._state is None or self._path is None:
            return
        try:
            current = self._path.stat().st_mtime_ns
        except OSError:
            return
        if current and current != self._mtime_ns:
            self._load()

    def _current_mtime_ns(self) -> int:
        if self._path is None:
            return 0
        try:
            return self._path.stat().st_mtime_ns
        except OSError:
            return 0


def _strategy_parameters_of(strategy_run: dict[str, Any]) -> dict[str, Any]:
    """Extract the strategy-specific parameters recorded by the replay run."""
    parameters = strategy_run.get("parameters")
    if isinstance(parameters, dict):
        nested = parameters.get("strategy_parameters")
        if isinstance(nested, dict):
            return {str(key): value for key, value in nested.items()}
    return {}


def execute_paper_follow(
    *,
    paper_automation: Any,
    paper_trading: Any,
    strategy_registry: Any,
    follow_service: PaperFollowService,
    run_id: str,
) -> dict[str, Any]:
    """Run one paper-follow cycle: strategy replay + buy-and-hold benchmark.

    Shared by the task worker and the backend's inline path so both record
    snapshots through the same alert evaluation.
    """
    from application.candle_backtest import CandleBacktestConfig

    automation = paper_automation.get()
    if not automation.get("enabled"):
        raise ValueError("paper automation is disabled")
    strategy_id = str(automation.get("strategy_id", ""))
    strategy_registry.assert_enabled(strategy_id, "paper")
    strategy_registry.assert_enabled("buy_and_hold", "paper")

    strategy_outcome = paper_automation.run_now(run_id=run_id, record_task=False)
    strategy_run = strategy_outcome["run"]

    benchmark_config = CandleBacktestConfig(
        strategy_id="buy_and_hold",
        initial_quote=Decimal(str(automation.get("initial_quote", "10000"))),
        initial_base=Decimal(str(automation.get("initial_base", "0"))),
        fee_bps=Decimal(str(automation.get("fee_bps", "10"))),
        slippage_bps=Decimal(str(automation.get("slippage_bps", "5"))),
        fast_window=10,
        slow_window=30,
        allocation_ratio=Decimal(str(automation.get("allocation_ratio", "1"))),
        momentum_threshold_pct=Decimal(str(automation.get("momentum_threshold_pct", "0.02"))),
        parameters={},
    )
    benchmark_run = paper_trading.run_strategy(
        venue_id=str(automation.get("venue_id", "binance")),
        symbol=str(automation.get("symbol", "BTC/USDT")),
        interval=str(automation.get("interval", "1h")),
        config=benchmark_config,
        run_id=f"{run_id}-benchmark",
        record_task=False,
    )

    strategy_return = _decimal(strategy_run.get("total_return_pct", "0"), "total_return_pct")
    market_return = _decimal(benchmark_run.get("total_return_pct", "0"), "total_return_pct")
    snapshot = {
        "run_id": str(run_id),
        "strategy_run_id": str(strategy_run.get("run_id", "")),
        "benchmark_run_id": str(benchmark_run.get("run_id", "")),
        "strategy_id": strategy_id,
        "venue_id": str(automation.get("venue_id", "")),
        "symbol": str(automation.get("symbol", "")),
        "interval": str(automation.get("interval", "")),
        "dataset_id": str(strategy_run.get("dataset_id", "")),
        "start_at": str(strategy_run.get("start_at", "")),
        "end_at": str(strategy_run.get("end_at", "")),
        "candle_count": int(strategy_run.get("candle_count", 0)),
        "strategy_return_pct": str(strategy_return),
        "market_return_pct": str(market_return),
        "excess_return_pct": str(strategy_return - market_return),
        "max_drawdown_pct": str(_decimal(strategy_run.get("max_drawdown_pct", "0"), "max_drawdown_pct")),
        "orders": int(strategy_run.get("orders", 0)),
        "win_rate_pct": str(strategy_run.get("win_rate_pct", "0")),
        "strategy_parameters": _strategy_parameters_of(strategy_run),
    }
    return follow_service.record_snapshot(snapshot)
