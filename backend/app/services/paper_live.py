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
    ) -> None:
        self._paper = paper
        self._strategies = strategies
        self._storage = storage
        self._regime = regime_service
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
    ) -> None:
        self._strategies = strategies
        self._storage = storage
        self._state = state_store
        self._paper_factory = paper_factory  # (instance_id) -> PaperTradingService
        self._regime = regime_service
        self._instances: dict[str, PaperLiveService] = {}
        self._lock = RLock()
        self._load()

    def _instance_state_store(self, instance_id: str) -> StateStore | None:
        if self._state is None or not hasattr(self._state, "path"):
            return None
        # 从 manager 的 state 路径派生实例路径
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
        )
        if config:
            # 恢复已保存的配置（不触发校验失败就跳过）
            try:
                svc.update({k: v for k, v in config.items() if k in DEFAULT_LIVE})
            except ValueError:
                pass
        return svc

    def list_instances(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"instance_id": iid, **svc.get()}
                for iid, svc in sorted(self._instances.items())
            ]

    def create_instance(self, values: dict[str, Any]) -> dict[str, Any]:
        import uuid
        instance_id = f"live-{uuid.uuid4().hex[:8]}"
        with self._lock:
            svc = self._build_instance(instance_id)
            # 应用用户配置
            svc.update(values)
            self._instances[instance_id] = svc
            self._persist_locked()
            return {"instance_id": instance_id, **svc.get()}

    def update_instance(self, instance_id: str, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            svc = self._instances.get(instance_id)
            if svc is None:
                raise KeyError(f"instance not found: {instance_id}")
            result = svc.update(values)
            self._persist_locked()
            return {"instance_id": instance_id, **result}

    def delete_instance(self, instance_id: str) -> None:
        with self._lock:
            if instance_id not in self._instances:
                raise KeyError(f"instance not found: {instance_id}")
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
        results = []
        with self._lock:
            instances = list(self._instances.items())
        for instance_id, svc in instances:
            try:
                outcome = svc.tick()
                results.append({"instance_id": instance_id, **outcome})
            except Exception as error:
                results.append({"instance_id": instance_id, "status": "tick_failed", "error": str(error)})
        return results
