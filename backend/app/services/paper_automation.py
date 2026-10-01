"""Controlled paper-strategy automation, kept separate from live execution."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore
from application.candle_backtest import CandleBacktestConfig

from .paper_trading import PaperTradingService
from .strategy_registry import StrategyRegistry


DEFAULT_AUTOMATION: dict[str, Any] = {
    "enabled": False,
    "venue_id": "binance",
    "symbol": "BTC/USDT",
    "interval": "1h",
    "strategy_id": "sma_cross",
    "initial_quote": "10000",
    "initial_base": "0",
    "fee_bps": "10",
    "slippage_bps": "5",
    "fast_window": 10,
    "slow_window": 30,
    "allocation_ratio": "1",
    "momentum_threshold_pct": "0.02",
    "strategy_parameters": {},
    "last_run_id": None,
    "updated_at": None,
}


class PaperAutomationService:
    """Persist a bounded paper replay profile and run it on demand.

    This is intentionally a manual-triggered replay. It does not run a
    daemon, place exchange orders, or silently turn on real execution.
    """

    def __init__(
        self,
        paper: PaperTradingService,
        strategies: StrategyRegistry,
        *,
        state_path: str | Path | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self._paper = paper
        self._strategies = strategies
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._config = deepcopy(DEFAULT_AUTOMATION)
        self._state_mtime_ns = 0
        self._load()

    def get(self) -> dict[str, Any]:
        self._refresh_external()
        return deepcopy(self._config)

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        self._refresh_external()
        next_config = deepcopy(self._config)
        next_config.update({key: value for key, value in values.items() if value is not None})
        self._validate(next_config)
        from datetime import UTC, datetime

        next_config["updated_at"] = datetime.now(UTC).isoformat()
        self._config = next_config
        self._persist()
        return self.get()

    def run_now(
        self,
        *,
        run_id: str | None = None,
        config: dict[str, Any] | None = None,
        record_task: bool = True,
    ) -> dict[str, Any]:
        self._refresh_external()
        active_config = deepcopy(config) if config is not None else self._config
        self._validate(active_config)
        if not active_config["enabled"]:
            raise ValueError("paper automation is disabled")
        self._strategies.assert_enabled(str(active_config["strategy_id"]), "paper")
        config = CandleBacktestConfig(
            strategy_id=str(active_config["strategy_id"]),
            initial_quote=Decimal(str(active_config["initial_quote"])),
            initial_base=Decimal(str(active_config["initial_base"])),
            fee_bps=Decimal(str(active_config["fee_bps"])),
            slippage_bps=Decimal(str(active_config["slippage_bps"])),
            fast_window=int(active_config["fast_window"]),
            slow_window=int(active_config["slow_window"]),
            allocation_ratio=Decimal(str(active_config["allocation_ratio"])),
            momentum_threshold_pct=Decimal(str(active_config["momentum_threshold_pct"])),
            parameters=dict(active_config.get("strategy_parameters") or {}),
        )
        result = self._paper.run_strategy(
            venue_id=str(active_config["venue_id"]),
            symbol=str(active_config["symbol"]),
            interval=str(active_config["interval"]),
            config=config,
            run_id=run_id,
            record_task=record_task,
        )
        self._config["last_run_id"] = result["run_id"]
        self._persist()
        return {"automation": self.get(), "run": result}

    @staticmethod
    def _validate(config: dict[str, Any]) -> None:
        if not isinstance(config.get("enabled"), bool):
            raise ValueError("enabled must be boolean")
        if str(config.get("venue_id", "")).strip().lower() not in {"binance", "okx", "bybit"}:
            raise ValueError("venue_id must be binance, okx, or bybit")
        if str(config.get("interval", "")).strip() not in {"1d", "1h", "5m"}:
            raise ValueError("interval must be 1d, 1h, or 5m")
        if not str(config.get("symbol", "")).strip():
            raise ValueError("symbol must not be empty")
        for key in ("initial_quote", "initial_base", "fee_bps", "slippage_bps", "allocation_ratio", "momentum_threshold_pct"):
            try:
                value = Decimal(str(config.get(key)))
            except Exception as error:
                raise ValueError(f"{key} must be numeric") from error
            if not value.is_finite() or value < 0:
                raise ValueError(f"{key} must be a non-negative finite number")
        if Decimal(str(config["initial_quote"])) <= 0:
            raise ValueError("initial_quote must be positive")
        if not Decimal(str(config["allocation_ratio"])) or Decimal(str(config["allocation_ratio"])) > 1:
            raise ValueError("allocation_ratio must be between 0 and 1")
        try:
            fast = int(config["fast_window"])
            slow = int(config["slow_window"])
        except (TypeError, ValueError) as error:
            raise ValueError("strategy windows must be integers") from error
        if fast < 2 or slow <= fast:
            raise ValueError("slow_window must be greater than fast_window")
        if not isinstance(config.get("strategy_parameters", {}), dict):
            raise ValueError("strategy_parameters must be an object")

    def _load(self) -> None:
        if self._state is None:
            return
        try:
            payload = self._state.load({"version": 1, "automation": DEFAULT_AUTOMATION})
        except JsonStateError as error:
            raise RuntimeError("paper automation state is unreadable") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("automation", {}), dict):
            raise RuntimeError("paper automation state must contain an object")
        next_config = deepcopy(DEFAULT_AUTOMATION)
        next_config.update(payload["automation"])
        self._validate(next_config)
        self._config = next_config
        try:
            state_path = getattr(self._state, "path", None)
            self._state_mtime_ns = state_path.stat().st_mtime_ns if state_path is not None else 0
        except OSError:
            self._state_mtime_ns = 0

    def _persist(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save({"version": 1, "automation": self._config})
            try:
                state_path = getattr(self._state, "path", None)
                self._state_mtime_ns = state_path.stat().st_mtime_ns if state_path is not None else 0
            except OSError:
                self._state_mtime_ns = 0
        except JsonStateError as error:
            raise RuntimeError("paper automation state cannot be saved") from error

    def _refresh_external(self) -> None:
        if self._state is None:
            return
        if not hasattr(self._state, "path"):
            self._load()
            return
        try:
            current_mtime = self._state.path.stat().st_mtime_ns
        except OSError:
            return
        if current_mtime and current_mtime != self._state_mtime_ns:
            self._load()
