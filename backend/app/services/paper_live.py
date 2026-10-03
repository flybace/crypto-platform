"""Live paper-trading loop: strategy signals become simulated account orders.

This closes the gap between strategy replays and the simulated account.
On each tick the service:

1. loads the latest candles for the configured venue/symbol/interval,
2. computes the strategy signal at the last closed candle
   (``CandleBacktestEngine.signal_at``, never touches a future candle),
3. translates the signal into an order on the *simulated* paper account:
   BUY with no base position -> buy ``allocation_ratio`` of quote equity;
   SELL while holding base -> sell the whole base position,
4. submits through ``PaperTradingService.submit`` (simulated broker only).

Idempotency: each candle is traded at most once (``last_candle_time``),
and order request ids are deterministic per candle+side so a retried tick
never double-places.

Safety: this only ever touches the simulated paper broker. Real execution
stays DISABLED; nothing here can place an exchange order.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock
from typing import Any

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore
from application.candle_backtest import CandleBacktestConfig, CandleBacktestEngine
from application.history_storage import HistoryStorage

from .paper_trading import PaperTradingService
from .strategy_registry import StrategyRegistry


DEFAULT_LIVE: dict[str, Any] = {
    "enabled": False,
    "venue_id": "binance",
    "symbol": "BTC/USDT",
    "interval": "1h",
    "strategy_id": "macd_reversal",
    "strategy_parameters": {"fast": 8, "slow": 26, "signal": 7},
    "allocation_ratio": "1",
    "last_candle_time": None,
    "last_signal": None,
    "last_tick_at": None,
    "last_order_id": None,
    "trade_count": 0,
    "updated_at": None,
}

TRADE_HISTORY_LIMIT = 50
DUST = Decimal("0.00000001")
MIN_NOTIONAL = Decimal("5")


def _decimal(value: Any, name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be numeric") from error
    if not parsed.is_finite():
        raise ValueError(f"{name} must be finite")
    return parsed


def decide_order(
    *,
    signal: str | None,
    base_balance: Decimal,
    quote_balance: Decimal,
    price: Decimal,
    allocation_ratio: Decimal,
) -> dict[str, Any] | None:
    """Translate a strategy signal into an order spec (pure, unit-testable).

    Returns ``{"side": ..., "quantity": ...}`` or ``None`` when no trade
    is warranted. Never raises on market data; validation of the final
    order is left to the paper broker.
    """
    if price <= 0:
        return None
    if signal == "BUY" and base_balance <= DUST:
        notional = quote_balance * allocation_ratio
        quantity = notional / price
        if quantity <= 0 or quantity * price < MIN_NOTIONAL:
            return {"side": "BUY", "quantity": Decimal("0"), "skipped": "notional_below_minimum"}
        return {"side": "BUY", "quantity": quantity}
    if signal == "SELL" and base_balance > DUST:
        return {"side": "SELL", "quantity": base_balance}
    return None


class PaperLiveService:
    """Own the live-loop configuration and per-candle trading state."""

    def __init__(
        self,
        paper: PaperTradingService,
        strategies: StrategyRegistry,
        storage: HistoryStorage,
        *,
        state_path: str | Path | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self._paper = paper
        self._strategies = strategies
        self._storage = storage
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._config = deepcopy(DEFAULT_LIVE)
        self._trades: list[dict[str, Any]] = []
        self._lock = RLock()
        self._state_mtime_ns = 0
        self._load()

    def get(self) -> dict[str, Any]:
        self._refresh_external()
        with self._lock:
            return {
                **deepcopy(self._config),
                "recent_trades": deepcopy(self._trades[-10:][::-1]),
            }

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        self._refresh_external()
        with self._lock:
            next_config = deepcopy(self._config)
            for key, value in values.items():
                if value is not None and key in DEFAULT_LIVE and key not in {
                    "last_candle_time", "last_signal", "last_tick_at",
                    "last_order_id", "trade_count",
                }:
                    next_config[key] = value
            self._validate(next_config)
            next_config["updated_at"] = datetime.now(UTC).isoformat()
            self._config = next_config
            self._persist_locked()
            return self.get()

    def tick(self) -> dict[str, Any]:
        """Run one live-loop iteration. Safe to call on every scheduler tick."""
        self._refresh_external()
        with self._lock:
            config = deepcopy(self._config)
        if not config["enabled"]:
            return {"status": "disabled"}
        self._strategies.assert_enabled(str(config["strategy_id"]), "paper")
        venue = str(config["venue_id"]).strip().lower()
        symbol = str(config["symbol"]).strip().upper()
        interval = str(config["interval"]).strip().lower()
        base_asset, quote_asset = symbol.split("/")

        engine_config = CandleBacktestConfig(
            strategy_id=str(config["strategy_id"]),
            parameters=dict(config.get("strategy_parameters") or {}),
        )
        lookback = CandleBacktestEngine.required_lookback(engine_config)
        dataset = self._storage.find_dataset(
            venue_id=venue,
            instrument_key=f"{venue}:spot:{symbol}",
            interval=interval,
        )
        if dataset is None:
            return {"status": "no_dataset", "detail": "verified history dataset was not found"}
        if dataset.manifest.gap_count or dataset.manifest.duplicate_count:
            return {"status": "dataset_quality_fail", "detail": "history dataset quality gate did not pass"}
        page = self._storage.read_page(dataset, limit=lookback + 10, tail=True)
        rows = list(page.items)
        if len(rows) < lookback + 1:
            return {"status": "not_enough_candles", "candles": len(rows), "required": lookback + 1}
        candle = rows[-1]
        candle_id = candle.open_time.isoformat()
        now_iso = datetime.now(UTC).isoformat()

        with self._lock:
            if self._config["last_candle_time"] == candle_id:
                self._config["last_tick_at"] = now_iso
                self._persist_locked()
                return {"status": "no_new_candle", "candle_time": candle_id}
            self._config["last_candle_time"] = candle_id
            self._config["last_tick_at"] = now_iso

        signal = CandleBacktestEngine.signal_at(rows, len(rows) - 1, engine_config)
        balances = self._venue_balances(venue)
        base_balance = _decimal(balances.get(base_asset, "0"), "base_balance")
        quote_balance = _decimal(balances.get(quote_asset, "0"), "quote_balance")
        price = _decimal(candle.close, "close")
        allocation = _decimal(config["allocation_ratio"], "allocation_ratio")

        decision = decide_order(
            signal=signal,
            base_balance=base_balance,
            quote_balance=quote_balance,
            price=price,
            allocation_ratio=allocation,
        )
        result: dict[str, Any] = {
            "status": "checked",
            "candle_time": candle_id,
            "close": str(candle.close),
            "signal": signal,
            "base_balance": str(base_balance),
            "quote_balance": str(quote_balance),
        }
        order_spec = decision
        if order_spec is not None and order_spec.get("skipped"):
            result["status"] = "skipped"
            result["reason"] = order_spec["skipped"]
            order_spec = None
        if order_spec is not None:
            side = str(order_spec["side"])
            quantity = _decimal(order_spec["quantity"], "quantity")
            request_id = f"paper-live:{venue}:{symbol}:{interval}:{candle_id}:{side}"
            order = self._paper.submit(
                venue_id=venue,
                symbol=symbol,
                interval=interval,
                side=side,
                quantity=quantity,
                limit_price=None,
                request_id=request_id,
            )
            trade = {
                "candle_time": candle_id,
                "signal": signal,
                "side": side,
                "quantity": str(order_spec["quantity"]),
                "order_id": order.get("order_id"),
                "filled_price": str(order.get("filled_price") or order.get("average_price") or ""),
                "created_at": now_iso,
            }
            with self._lock:
                self._trades.append(trade)
                del self._trades[:-TRADE_HISTORY_LIMIT]
                self._config["last_signal"] = signal
                self._config["last_order_id"] = order.get("order_id")
                self._config["trade_count"] = int(self._config.get("trade_count") or 0) + 1
                self._persist_locked()
            result["status"] = "traded"
            result["trade"] = trade
        else:
            with self._lock:
                self._config["last_signal"] = signal
                self._persist_locked()
        return result

    def _venue_balances(self, venue: str) -> dict[str, str]:
        summary = self._paper.summary()
        accounts = summary.get("accounts") or []
        for item in accounts:
            if str(item.get("venue_id", "")).strip().lower() == venue:
                balances = item.get("balances") or {}
                return {str(k).upper(): str(v) for k, v in balances.items()}
        return {}

    @staticmethod
    def _validate(config: dict[str, Any]) -> None:
        if not isinstance(config.get("enabled"), bool):
            raise ValueError("enabled must be boolean")
        if str(config.get("venue_id", "")).strip().lower() not in {"binance", "okx", "bybit"}:
            raise ValueError("venue_id must be binance, okx, or bybit")
        if str(config.get("interval", "")).strip() not in {"1d", "1h", "5m"}:
            raise ValueError("interval must be 1d, 1h, or 5m")
        if "/" not in str(config.get("symbol", "")):
            raise ValueError("symbol must look like BTC/USDT")
        if not str(config.get("strategy_id", "")).strip():
            raise ValueError("strategy_id must not be empty")
        allocation = _decimal(config.get("allocation_ratio"), "allocation_ratio")
        if not Decimal("0") < allocation <= Decimal("1"):
            raise ValueError("allocation_ratio must be between 0 and 1")
        params = config.get("strategy_parameters")
        if params is not None and not isinstance(params, dict):
            raise ValueError("strategy_parameters must be an object")

    def _load(self) -> None:
        if self._state is None:
            return
        try:
            payload = self._state.load({"version": 1, "live": DEFAULT_LIVE, "trades": []})
        except JsonStateError as error:
            raise RuntimeError("paper live state is unreadable") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("live"), dict):
            raise RuntimeError("paper live state must contain an object")
        next_config = deepcopy(DEFAULT_LIVE)
        next_config.update(payload["live"])
        self._validate(next_config)
        self._config = next_config
        trades = payload.get("trades")
        self._trades = list(trades[-TRADE_HISTORY_LIMIT:]) if isinstance(trades, list) else []
        try:
            state_path = getattr(self._state, "path", None)
            self._state_mtime_ns = state_path.stat().st_mtime_ns if state_path is not None else 0
        except OSError:
            self._state_mtime_ns = 0

    def _persist_locked(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save({"version": 1, "live": self._config, "trades": self._trades})
            try:
                state_path = getattr(self._state, "path", None)
                self._state_mtime_ns = state_path.stat().st_mtime_ns if state_path is not None else 0
            except OSError:
                self._state_mtime_ns = 0
        except JsonStateError as error:
            raise RuntimeError("paper live state cannot be saved") from error

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
            with self._lock:
                self._load()
