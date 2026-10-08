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
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
import logging
from pathlib import Path
from threading import RLock
from typing import Any

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore
from application.candle_backtest import CandleBacktestConfig, CandleBacktestEngine
from application.history_storage import HistoryStorage

from .paper_trading import PaperTradingService
from .strategy_registry import StrategyRegistry

logger = logging.getLogger(__name__)


DEFAULT_LIVE: dict[str, Any] = {
    "enabled": False,
    # 自动交易：实例级开关，默认关闭。实际下单还需要全局开关
    # （TradingSettingsStore）同时打开，两者是与门关系。
    "auto_trading": False,
    "venue_id": "binance",
    "symbol": "BTC/USDT",
    "pool_id": None,              # 币池实例：选用交易池后按池成分逐币运行
    "parent_pool_id": None,       # 池子实例：归属的父实例 id（自动生成，不可手动设置）
    "interval": "1h",
    "strategy_id": "macd_reversal",
    "strategy_parameters": {"fast": 8, "slow": 26, "signal": 7},
    "allocation_ratio": "1",
    # 风控参数（0 表示关闭该项）
    "max_position_ratio": "1",      # 持仓上限：base 市值占总权益的最大比例
    "stop_loss_pct": "0",           # 止损：持仓浮亏超该比例强制平仓，如 "0.05"
    "daily_max_loss_pct": "0",      # 单日最大亏损：当日权益回撤超该比例则停牌至次日 UTC 0 点
    # 运行状态
    "last_candle_time": None,
    "last_signal": None,
    "last_tick_at": None,
    "last_order_id": None,
    "trade_count": 0,
    "entry_price": None,            # 当前持仓的开仓均价（空仓时为 None）
    "day_start_equity": None,       # 当日 UTC 0 点权益（用于计算单日盈亏）
    "risk_halt_until": None,        # 风控停牌至该时间（ISO）
    "last_risk_event": None,        # 最近一次风控动作描述
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


def check_risk(
    *,
    signal: str | None,
    base_balance: Decimal,
    quote_balance: Decimal,
    price: Decimal,
    entry_price: Decimal | None,
    day_start_equity: Decimal | None,
    max_position_ratio: Decimal,
    stop_loss_pct: Decimal,
    daily_max_loss_pct: Decimal,
    risk_halt_until: str | None,
    now: datetime,
) -> dict[str, Any]:
    """风控检查（纯函数，可单测）。

    返回 {"action": "allow"} 或 {"action": "halt", "reason": ...}
    或 {"action": "force_sell", "reason": ...} 或 {"action": "cap_buy", "max_notional": ...}。
    """
    equity = quote_balance + base_balance * price
    # 1. 停牌检查
    if risk_halt_until:
        try:
            halt_until = datetime.fromisoformat(risk_halt_until)
            if now < halt_until:
                return {"action": "halt", "reason": "risk_halt_active"}
        except ValueError:
            pass
    # 2. 单日最大亏损
    if daily_max_loss_pct > 0 and day_start_equity and day_start_equity > 0:
        day_pnl_ratio = (equity - day_start_equity) / day_start_equity
        if day_pnl_ratio <= -daily_max_loss_pct:
            # 停牌至次日 UTC 0 点
            next_day = (now.replace(hour=0, minute=0, second=0, microsecond=0)
                        + timedelta(days=1))
            return {
                "action": "halt",
                "reason": "daily_max_loss",
                "halt_until": next_day.isoformat(),
                "day_pnl_ratio": str(day_pnl_ratio),
            }
    # 3. 止损：持仓且浮亏超限 → 强制平仓
    if (stop_loss_pct > 0 and base_balance > DUST
            and entry_price and entry_price > 0):
        loss_ratio = (price - entry_price) / entry_price
        if loss_ratio <= -stop_loss_pct:
            return {
                "action": "force_sell",
                "reason": "stop_loss",
                "loss_ratio": str(loss_ratio),
            }
    # 4. 仓位上限：BUY 时检查
    if signal == "BUY" and max_position_ratio < 1:
        # 买入后仓位市值占比不超过上限
        # 这里返回允许的最大名义金额，由调用方截断
        max_position_value = equity * max_position_ratio
        current_position_value = base_balance * price
        allowed_additional = max_position_value - current_position_value
        if allowed_additional <= 0:
            return {"action": "halt", "reason": "position_limit_reached"}
        return {"action": "cap_buy", "max_notional": str(allowed_additional)}
    return {"action": "allow"}


def decide_order(
    *,
    signal: str | None,
    base_balance: Decimal,
    quote_balance: Decimal,
    price: Decimal,
    allocation_ratio: Decimal,
    max_buy_notional: Decimal | None = None,
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
        if max_buy_notional is not None:
            notional = min(notional, max_buy_notional)
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
        regime_service=None,
        auto_trading_checker=None,
    ) -> None:
        self._paper = paper
        self._strategies = strategies
        self._storage = storage
        self._regime = regime_service
        # 全局自动交易开关的读取函数 () -> bool。未注入时默认关闭（fail-closed），
        # 保证开关语义：不显式接线就不下单。
        self._auto_trading_checker = auto_trading_checker or (lambda: False)
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
                "account": self._account_snapshot(),
            }

    def _account_snapshot(self) -> dict[str, Any] | None:
        """返回本实例独立模拟账户在当前 venue 的余额快照（前端可见）。"""
        paper = self._paper
        if paper is None:
            return None
        try:
            venue_id = self._config.get("venue_id")
            # PaperTradingService._accounts 是 venue_id -> PaperAccount
            account = paper._accounts.get(venue_id)  # noqa: SLF001
            if account is None:
                return None
            return {
                "venue_id": venue_id,
                "balances": {k: str(v) for k, v in account.snapshot().items()},
            }
        except Exception:
            return None

    def get_config(self) -> dict[str, Any]:
        """返回纯配置（不含 recent_trades），用于 Manager 持久化实例列表。"""
        self._refresh_external()
        with self._lock:
            return deepcopy(self._config)

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        self._refresh_external()
        with self._lock:
            next_config = deepcopy(self._config)
            for key, value in values.items():
                if value is not None and key in DEFAULT_LIVE and key not in {
                    "last_candle_time", "last_signal", "last_tick_at",
                    "last_order_id", "trade_count", "entry_price",
                    "day_start_equity", "risk_halt_until", "last_risk_event",
                }:
                    next_config[key] = value
            self._validate(next_config)
            next_config["updated_at"] = datetime.now(UTC).isoformat()
            self._config = next_config
            self._persist_locked()
            return self.get()

    def _auto_trading_effective(self) -> tuple[bool, str]:
        """两级开关是否同时打开。返回 (是否允许下单, 阻挡方)。

        阻挡方: "none"（都开）、"instance"（实例开关未开）、"global"（全局开关未开）。
        """
        with self._lock:
            instance_on = bool(self._config.get("auto_trading"))
        if not instance_on:
            return False, "instance"
        try:
            global_on = bool(self._auto_trading_checker())
        except Exception:
            global_on = False
        return (True, "none") if global_on else (False, "global")

    def tick(self) -> dict[str, Any]:
        """Run one live-loop iteration. Safe to call on every scheduler tick."""
        self._refresh_external()
        with self._lock:
            config = deepcopy(self._config)
        if not config["enabled"]:
            return {"status": "disabled"}
        if config.get("pool_id"):
            return {"status": "pool_parent", "detail": "pool members run as child instances"}
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

        # ---- 风控检查 ----
        now_dt = datetime.now(UTC)
        entry_price = _decimal(config["entry_price"], "entry_price") if config.get("entry_price") else None
        day_start_equity = _decimal(config["day_start_equity"], "day_start_equity") if config.get("day_start_equity") else None
        # 每日 UTC 0 点重置日初权益
        day_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        if day_start_equity is None:
            day_start_equity = quote_balance + base_balance * price
            with self._lock:
                self._config["day_start_equity"] = str(day_start_equity)
                self._persist_locked()
        risk = check_risk(
            signal=signal,
            base_balance=base_balance,
            quote_balance=quote_balance,
            price=price,
            entry_price=entry_price,
            day_start_equity=day_start_equity,
            max_position_ratio=_decimal(config["max_position_ratio"], "max_position_ratio"),
            stop_loss_pct=_decimal(config["stop_loss_pct"], "stop_loss_pct"),
            daily_max_loss_pct=_decimal(config["daily_max_loss_pct"], "daily_max_loss_pct"),
            risk_halt_until=config.get("risk_halt_until"),
            now=now_dt,
        )
        risk_action = risk["action"]
        if risk_action == "halt":
            halt_until = risk.get("halt_until")
            with self._lock:
                if halt_until:
                    self._config["risk_halt_until"] = halt_until
                self._config["last_risk_event"] = f"{risk['reason']} @ {now_iso}"
                self._persist_locked()
            return {
                "status": "risk_halted",
                "reason": risk["reason"],
                "candle_time": candle_id,
            }
        # 止损强制平仓：覆盖原信号
        if risk_action == "force_sell":
            signal = "SELL"
            with self._lock:
                self._config["last_risk_event"] = f"stop_loss @ {now_iso} loss={risk.get('loss_ratio')}"
                self._persist_locked()
        # 仓位上限：截断买入金额
        max_buy_notional = Decimal(risk["max_notional"]) if risk_action == "cap_buy" else None

        # 市场状态：只影响开仓（BUY），永不拦截平仓（SELL）
        regime_info = None
        effective_allocation = allocation
        if signal == "BUY" and self._regime is not None:
            regime_info = self._regime.evaluate()
            if regime_info.blocks_new_positions:
                with self._lock:
                    self._config["last_risk_event"] = f"regime_blocked({regime_info.regime}) @ {now_iso} {regime_info.reason}"
                    self._persist_locked()
                result = {
                    "status": "regime_blocked",
                    "reason": regime_info.reason,
                    "regime": regime_info.regime,
                    "score": regime_info.score,
                    "candle_time": candle_id,
                    "close": str(candle.close),
                    "signal": signal,
                }
                return result
            if regime_info.factor < 1.0:
                effective_allocation = allocation * Decimal(str(regime_info.factor))

        decision = decide_order(
            signal=signal,
            base_balance=base_balance,
            quote_balance=quote_balance,
            price=price,
            allocation_ratio=effective_allocation,
            max_buy_notional=max_buy_notional,
        )
        result: dict[str, Any] = {
            "status": "checked",
            "candle_time": candle_id,
            "close": str(candle.close),
            "signal": signal,
            "base_balance": str(base_balance),
            "quote_balance": str(quote_balance),
        }
        if regime_info is not None:
            result["regime"] = regime_info.regime
            result["regime_score"] = regime_info.score
            result["regime_factor"] = regime_info.factor
        order_spec = decision
        if order_spec is not None and order_spec.get("skipped"):
            result["status"] = "skipped"
            result["reason"] = order_spec["skipped"]
            order_spec = None
        if order_spec is not None:
            # 自动交易门禁：两级开关（实例级 + 全局）都打开才允许下单。
            # 未开时只记录信号，不动模拟账户——止损强制卖同样受门禁，
            # 语义统一：开关关 = 引擎不自动下任何单。
            allowed, held_by = self._auto_trading_effective()
            if not allowed:
                with self._lock:
                    self._config["last_signal"] = signal
                    self._persist_locked()
                result["status"] = "held"
                result["reason"] = "auto_trading_disabled"
                result["held_by"] = held_by
                return result
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
                # 更新开仓价：买入记入场价，卖出清空
                if side == "BUY":
                    filled = _decimal(trade["filled_price"] or price, "filled_price")
                    prev_entry = _decimal(self._config["entry_price"], "entry_price") if self._config.get("entry_price") else None
                    prev_qty = base_balance
                    new_qty = _decimal(trade["quantity"], "quantity")
                    if prev_entry and prev_qty > DUST:
                        # 加权平均（虽然策略通常是全仓进出，保留通用逻辑）
                        total_qty = prev_qty + new_qty
                        self._config["entry_price"] = str((prev_entry * prev_qty + filled * new_qty) / total_qty)
                    else:
                        self._config["entry_price"] = str(filled)
                elif side == "SELL":
                    self._config["entry_price"] = None
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
        if not isinstance(config.get("auto_trading"), bool):
            raise ValueError("auto_trading must be boolean")
        if str(config.get("venue_id", "")).strip().lower() not in {"binance", "okx", "bybit"}:
            raise ValueError("venue_id must be binance, okx, or bybit")
        if str(config.get("interval", "")).strip() not in {"1d", "1h", "5m"}:
            raise ValueError("interval must be 1d, 1h, or 5m")
        if not config.get("pool_id") and "/" not in str(config.get("symbol", "")):
            raise ValueError("symbol must look like BTC/USDT")
        if not str(config.get("strategy_id", "")).strip():
            raise ValueError("strategy_id must not be empty")
        allocation = _decimal(config.get("allocation_ratio"), "allocation_ratio")
        if not Decimal("0") < allocation <= Decimal("1"):
            raise ValueError("allocation_ratio must be between 0 and 1")
        max_pos = _decimal(config.get("max_position_ratio"), "max_position_ratio")
        if not Decimal("0") < max_pos <= Decimal("1"):
            raise ValueError("max_position_ratio must be between 0 and 1")
        stop_loss = _decimal(config.get("stop_loss_pct"), "stop_loss_pct")
        if not Decimal("0") <= stop_loss < Decimal("1"):
            raise ValueError("stop_loss_pct must be between 0 and 1")
        daily_loss = _decimal(config.get("daily_max_loss_pct"), "daily_max_loss_pct")
        if not Decimal("0") <= daily_loss < Decimal("1"):
            raise ValueError("daily_max_loss_pct must be between 0 and 1")
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


class PaperLiveManager:
    """管理多个独立策略实例。每个实例有独立的配置、状态和模拟账户。"""

    def __init__(
        self,
        *,
        strategies: StrategyRegistry,
        storage: HistoryStorage,
        state_store: StateStore | None = None,
        paper_factory=None,
        regime_service=None,
        instance_state_store_factory=None,
        coin_pool_service=None,
        auto_trading_checker=None,
        paper_eligibility_checker=None,
    ) -> None:
        self._strategies = strategies
        self._storage = storage
        self._state = state_store
        self._paper_factory = paper_factory  # (instance_id) -> PaperTradingService
        self._regime = regime_service
        self._coin_pools = coin_pool_service
        # 全局自动交易开关读取函数，透传给每个策略实例
        self._auto_trading_checker = auto_trading_checker
        # 策略准入漏斗检查函数 (strategy_id, params) -> (eligible, reason)。
        # 未接入时默认放行（由调用方如 main.py 显式接入）。
        self._paper_eligibility_checker = (
            paper_eligibility_checker or (lambda strategy_id, params: (True, ""))
        )
        # (instance_id) -> StateStore：实例运行时状态的持久化位置。
        # 生产环境的 state_store 是 SqlStateStore（没有 .path），无法再从
        # manager 路径派生实例文件，必须由调用方显式提供。
        self._instance_state_factory = instance_state_store_factory
        self._instances: dict[str, PaperLiveService] = {}
        self._lock = RLock()
        self._load()

    def _instance_state_store(self, instance_id: str) -> StateStore | None:
        if self._instance_state_factory is not None:
            return self._instance_state_factory(instance_id)
        if self._state is None or not hasattr(self._state, "path"):
            return None
        # 从 manager 的 state 路径派生实例路径（仅 JsonStateStore 可用）
        base = self._state.path
        return JsonStateStore(str(base).replace("paper-live-manager.json", f"paper-live-{instance_id}.json"))

    def _load(self) -> None:
        if self._state is None:
            return
        try:
            payload = self._state.load({"version": 1, "instances": {}})
        except JsonStateError:
            payload = {"version": 1, "instances": {}}
        instances = payload.get("instances", {}) if isinstance(payload, dict) else {}
        with self._lock:
            for instance_id, config in instances.items():
                self._instances[instance_id] = self._build_instance(instance_id, config)

    def _persist_locked(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save({
                "version": 1,
                "instances": {iid: svc.get_config() for iid, svc in self._instances.items()},
            })
        except JsonStateError as error:
            raise RuntimeError("paper live manager state cannot be saved") from error

    def _build_instance(self, instance_id: str, config: dict | None = None) -> PaperLiveService:
        paper = self._paper_factory(instance_id) if self._paper_factory else None
        svc = PaperLiveService(
            paper,
            self._strategies,
            self._storage,
            state_store=self._instance_state_store(instance_id),
            regime_service=self._regime,
            auto_trading_checker=self._auto_trading_checker,
        )
        if config:
            # 恢复已保存的配置（不触发校验失败就跳过）
            try:
                svc.update({k: v for k, v in config.items() if k in DEFAULT_LIVE})
            except ValueError:
                pass
        return svc

    def _check_paper_eligible(self, strategy_id: str, params: dict | None) -> tuple[bool, str]:
        """查策略准入漏斗：是否达到 paper_approved（可自动交易）。

        漏斗 key 是 strategy_id + 参数哈希——参数变了就是新版本，要重过漏斗。
        """
        eligible, reason = self._paper_eligibility_checker(
            str(strategy_id or ""), dict(params or {})
        )
        return bool(eligible), str(reason or "")

    def _require_paper_eligible(self, strategy_id: str, params: dict | None, *, action: str) -> None:
        eligible, reason = self._check_paper_eligible(strategy_id, params)
        if not eligible:
            raise ValueError(
                f"策略未通过准入漏斗，无法{action}：{reason}。"
                "请先在回测页提交评级，按 评分→复测→跨池验证→准入 晋级到 paper_approved。"
            )

    def list_instances(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"instance_id": iid, **svc.get()}
                for iid, svc in sorted(self._instances.items())
            ]

    def create_instance(self, values: dict[str, Any]) -> dict[str, Any]:
        import uuid
        pool_id = values.get("pool_id")
        if pool_id:
            # 门禁：只有已确认的交易池能被策略实例选用
            if self._coin_pools is None:
                raise ValueError("coin pool service is unavailable")
            self._coin_pools.assert_tradable(str(pool_id))
        instance_id = f"live-{uuid.uuid4().hex[:8]}"
        with self._lock:
            svc = self._build_instance(instance_id)
            # 应用用户配置
            svc.update(values)
            cfg = svc.get_config()
            # 联动漏斗：建实例时就打开自动交易的，策略必须已过 paper_approved；
            # 先检查再落盘，避免半创建状态。
            if cfg.get("auto_trading"):
                self._require_paper_eligible(
                    cfg.get("strategy_id"), cfg.get("strategy_parameters"),
                    action="创建并开启自动交易",
                )
            self._instances[instance_id] = svc
            self._persist_locked()
            return {"instance_id": instance_id, **svc.get()}

    @staticmethod
    def _child_instance_id(parent_id: str, symbol: str) -> str:
        sanitized = str(symbol).upper().replace("/", "_").replace("-", "_")
        return f"{parent_id}__{sanitized}"

    def _sync_pool_children(self, parent_id: str, parent_config: dict[str, Any]) -> list[str]:
        """按币池当前成分同步子实例：新增成分建子实例，掉出成分的停用。

        每个子实例有独立模拟账户，与多策略实例隔离原则一致。
        """
        if self._coin_pools is None:
            return []
        pool_id = str(parent_config.get("pool_id") or "").strip()
        if not pool_id:
            return []
        try:
            pool = self._coin_pools.get(pool_id)
        except Exception:
            return []
        if pool is None or pool.get("role") != "trading":
            return []
        members = [str(s).upper() for s in pool.get("current_members", [])]
        wanted = {self._child_instance_id(parent_id, symbol) for symbol in members}
        synced: list[str] = []
        with self._lock:
            # 新增：为每个池成分创建子实例（继承父实例的策略与风控参数）
            for symbol in members:
                child_id = self._child_instance_id(parent_id, symbol)
                if child_id in self._instances:
                    continue
                child = self._build_instance(child_id)
                child_config = {
                    k: v for k, v in parent_config.items()
                    if k in DEFAULT_LIVE and k not in {
                        "last_candle_time", "last_signal", "last_tick_at",
                        "last_order_id", "trade_count", "entry_price",
                        "day_start_equity", "risk_halt_until", "last_risk_event",
                        "pool_id",
                    }
                }
                child_config["symbol"] = symbol
                child_config["pool_id"] = None
                child_config["parent_pool_id"] = parent_id
                child_config["enabled"] = bool(parent_config.get("enabled"))
                try:
                    child.update(child_config)
                except ValueError:
                    continue
                self._instances[child_id] = child
                synced.append(child_id)
            # 掉出成分的子实例：停用（保留持仓记录，不自动删）
            for child_id, child_svc in list(self._instances.items()):
                cfg = child_svc.get_config()
                if cfg.get("parent_pool_id") == parent_id and child_id not in wanted:
                    if cfg.get("enabled"):
                        try:
                            child_svc.update({"enabled": False})
                        except ValueError:
                            pass
            self._persist_locked()
        return synced

    def _sync_all_pool_children(self) -> None:
        with self._lock:
            parents = [
                (iid, svc.get_config())
                for iid, svc in self._instances.items()
                if svc.get_config().get("pool_id")
            ]
        for parent_id, parent_config in parents:
            try:
                self._sync_pool_children(parent_id, parent_config)
            except Exception:
                continue

    def update_instance(self, instance_id: str, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            svc = self._instances.get(instance_id)
            if svc is None:
                raise KeyError(f"instance not found: {instance_id}")
            current = svc.get_config()
            new_strategy_id = values.get("strategy_id", current.get("strategy_id"))
            new_params = values.get("strategy_parameters", current.get("strategy_parameters"))
            strategy_changed = (
                str(new_strategy_id or "") != str(current.get("strategy_id") or "")
                or (new_params or {}) != (current.get("strategy_parameters") or {})
            )
            notice = None
            if values.get("auto_trading") is True:
                # 显式开启自动交易 → 策略（新参数版本）必须已过 paper_approved
                self._require_paper_eligible(new_strategy_id, new_params, action="开启自动交易")
            elif strategy_changed and current.get("auto_trading"):
                # 策略/参数变更 = 新版本，需重过漏斗；先自动关闭自动交易（安全方向）
                eligible, _ = self._check_paper_eligible(new_strategy_id, new_params)
                if not eligible:
                    values = {**values, "auto_trading": False}
                    notice = (
                        "策略/参数变更后为新版本，需重新通过准入漏斗，"
                        "已自动关闭自动交易"
                    )
            result = svc.update(values)
            self._persist_locked()
            payload = {"instance_id": instance_id, **result}
            if notice:
                payload["_notice"] = notice
            return payload

    def delete_instance(self, instance_id: str) -> None:
        with self._lock:
            if instance_id not in self._instances:
                raise KeyError(f"instance not found: {instance_id}")
            # 级联删除该池父实例的子实例
            children = [
                iid for iid, svc in self._instances.items()
                if svc.get_config().get("parent_pool_id") == instance_id
            ]
            for child_id in children:
                del self._instances[child_id]
            del self._instances[instance_id]
            self._persist_locked()

    def get_instance(self, instance_id: str) -> PaperLiveService | None:
        with self._lock:
            return self._instances.get(instance_id)

    def reset_instance_account(self, instance_id: str, balances: dict | None = None) -> dict[str, Any]:
        """重置指定实例的独立模拟账户为干净状态（默认纯 USDT）。"""
        with self._lock:
            svc = self._instances.get(instance_id)
            if svc is None:
                raise KeyError(f"instance not found: {instance_id}")
            paper = svc._paper  # noqa: SLF001
            if paper is None:
                raise RuntimeError("instance has no paper account")
            result = paper.reset(balances, venue_id=svc.get_config().get("venue_id"))
            # 重置后清掉策略的持仓记忆（entry_price 等），避免风控用旧价格
            cfg = svc.get_config()
            cfg["entry_price"] = None
            cfg["day_start_equity"] = None
            svc.update(cfg)
            return result

    def tick_all(self) -> list[dict[str, Any]]:
        """对所有启用的实例各跑一轮。引擎每 60 秒调用一次。"""
        self._sync_all_pool_children()
        results = []
        with self._lock:
            instances = list(self._instances.items())
        for instance_id, svc in instances:
            try:
                outcome = svc.tick()
                results.append({"instance_id": instance_id, **outcome})
            except Exception as error:
                results.append({"instance_id": instance_id, "status": "tick_failed", "error": str(error)})
        # tick 只落盘各实例文件；manager 快照若不更新，
        # paper-live-manager.json 的 last_tick_at / last_candle_time 等
        # 会一直停留在上次 create/update 的时刻。引擎每轮同步一次，
        # 快照落盘失败只记日志，不影响 tick 结果本身。
        try:
            with self._lock:
                self._persist_locked()
        except Exception as error:
            logger.warning("paper live manager snapshot persist failed: %s", error)
        return results
