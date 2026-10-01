"""Deterministic OHLCV backtests for the first research workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from domain.candle import Candle


SUPPORTED_STRATEGIES = (
    "buy_and_hold",
    "sma_cross",
    "momentum",
    "inventory_exit",
    "trend_breakout",
    "rsi_rebound",
    "bollinger_breakout",
    "macd_reversal",
    "volume_momentum",
    "volatility_breakout",
)


STRATEGY_PARAMETER_SCHEMAS: dict[str, tuple[dict[str, object], ...]] = {
    "buy_and_hold": (),
    "sma_cross": (
        {"key": "fast_window", "label": "快线窗口", "type": "integer", "default": 10, "min": 2, "max": 500},
        {"key": "slow_window", "label": "慢线窗口", "type": "integer", "default": 30, "min": 3, "max": 1000},
    ),
    "momentum": (
        {"key": "lookback", "label": "动量窗口", "type": "integer", "default": 30, "min": 2, "max": 1000},
        {"key": "threshold", "label": "入场收益阈值", "type": "ratio", "default": "0.02", "min": 0, "max": 10},
    ),
    "inventory_exit": (
        {"key": "window", "label": "退出均线窗口", "type": "integer", "default": 10, "min": 2, "max": 500},
    ),
    "trend_breakout": (
        {"key": "window", "label": "突破窗口", "type": "integer", "default": 20, "min": 2, "max": 500},
        {"key": "min_return", "label": "最小区间收益", "type": "ratio", "default": "0", "min": 0, "max": 10},
    ),
    "rsi_rebound": (
        {"key": "period", "label": "RSI 周期", "type": "integer", "default": 14, "min": 2, "max": 200},
        {"key": "max_rsi", "label": "入场 RSI 上限", "type": "number", "default": 35, "min": 1, "max": 99},
        {"key": "exit_rsi", "label": "退出 RSI", "type": "number", "default": 65, "min": 1, "max": 99},
    ),
    "bollinger_breakout": (
        {"key": "window", "label": "布林窗口", "type": "integer", "default": 20, "min": 3, "max": 500},
        {"key": "std_multiplier", "label": "标准差倍数", "type": "number", "default": "2", "min": 0.5, "max": 6},
    ),
    "macd_reversal": (
        {"key": "fast", "label": "MACD 快线", "type": "integer", "default": 12, "min": 2, "max": 200},
        {"key": "slow", "label": "MACD 慢线", "type": "integer", "default": 26, "min": 3, "max": 500},
        {"key": "signal", "label": "MACD 信号线", "type": "integer", "default": 9, "min": 2, "max": 200},
    ),
    "volume_momentum": (
        {"key": "window", "label": "量能均值窗口", "type": "integer", "default": 20, "min": 2, "max": 500},
        {"key": "min_return", "label": "最小涨幅", "type": "ratio", "default": "0.01", "min": 0, "max": 10},
        {"key": "min_volume_ratio", "label": "最小放量倍数", "type": "number", "default": "1.2", "min": 0, "max": 100},
    ),
    "volatility_breakout": (
        {"key": "window", "label": "波动窗口", "type": "integer", "default": 20, "min": 3, "max": 500},
        {"key": "atr_multiplier", "label": "ATR 倍数", "type": "number", "default": "1.5", "min": 0, "max": 20},
    ),
}


def strategy_catalog() -> tuple[dict[str, object], ...]:
    definitions = (
        {
            "strategy_id": "buy_and_hold",
            "name": "买入并持有",
            "description": "在第一根可交易 K 线后买入，回测结束时退出，作为基准线。",
            "modes": ("backtest", "paper"),
        },
        {
            "strategy_id": "sma_cross",
            "name": "均线交叉",
            "description": "快线向上穿越慢线买入，向下穿越慢线卖出。",
            "modes": ("research", "backtest", "paper"),
        },
        {
            "strategy_id": "momentum",
            "name": "动量延续",
            "description": "过去窗口收益超过阈值时入场，动量转负时退出。",
            "modes": ("research", "backtest", "paper"),
        },
        {
            "strategy_id": "inventory_exit",
            "name": "库存退出",
            "description": "只在已有基础币库存时运行，价格跌破均线后卖出。",
            "modes": ("backtest", "paper", "sell-only"),
        },
        {
            "strategy_id": "trend_breakout",
            "name": "趋势突破",
            "description": "收盘突破此前区间高点且动量达到阈值时入场，跌破短均线退出。",
            "modes": ("research", "backtest", "paper"),
        },
        {
            "strategy_id": "rsi_rebound",
            "name": "RSI 超卖反弹",
            "description": "RSI 处于低位并出现修复时入场，RSI 回到高位或跌破均线时退出。",
            "modes": ("research", "backtest", "paper"),
        },
        {
            "strategy_id": "bollinger_breakout",
            "name": "布林突破",
            "description": "价格向上突破布林上轨时入场，回落到中轨下方退出。",
            "modes": ("research", "backtest", "paper"),
        },
        {
            "strategy_id": "macd_reversal",
            "name": "MACD 反转",
            "description": "MACD 柱线由负转正时入场，由正转负时退出。",
            "modes": ("research", "backtest", "paper"),
        },
        {
            "strategy_id": "volume_momentum",
            "name": "量价动量",
            "description": "价格上涨同时成交量超过历史均值时入场，动量转负时退出。",
            "modes": ("research", "backtest", "paper"),
        },
        {
            "strategy_id": "volatility_breakout",
            "name": "波动突破",
            "description": "价格突破区间并超过历史波动阈值时入场，回落到均线下方退出。",
            "modes": ("research", "backtest", "paper"),
        },
    )
    return tuple(
        {
            **definition,
            "version": "1.0.0",
            "parameter_schema": [dict(item) for item in STRATEGY_PARAMETER_SCHEMAS[definition["strategy_id"]]],
            "supports_parameter_search": definition["strategy_id"] not in {"buy_and_hold", "inventory_exit"},
            "data_level": "KLINE",
        }
        for definition in definitions
    )


def _decimal(value: Decimal | int | str, field: str) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be a decimal value") from error
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


@dataclass(frozen=True, slots=True)
class CandleBacktestConfig:
    strategy_id: str = "sma_cross"
    initial_quote: Decimal = Decimal("10000")
    initial_base: Decimal = Decimal("0")
    fee_bps: Decimal = Decimal("10")
    slippage_bps: Decimal = Decimal("5")
    fast_window: int = 10
    slow_window: int = 30
    allocation_ratio: Decimal = Decimal("1")
    momentum_threshold_pct: Decimal = Decimal("0.02")
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        strategy_id = str(self.strategy_id).strip().lower()
        initial_quote = _decimal(self.initial_quote, "initial_quote")
        initial_base = _decimal(self.initial_base, "initial_base")
        fee_bps = _decimal(self.fee_bps, "fee_bps")
        slippage_bps = _decimal(self.slippage_bps, "slippage_bps")
        allocation_ratio = _decimal(self.allocation_ratio, "allocation_ratio")
        threshold = _decimal(self.momentum_threshold_pct, "momentum_threshold_pct")
        if strategy_id not in SUPPORTED_STRATEGIES:
            raise ValueError(f"unknown backtest strategy: {strategy_id}")
        if initial_quote <= 0 or initial_base < 0:
            raise ValueError("initial balances are outside the allowed range")
        if fee_bps < 0 or slippage_bps < 0:
            raise ValueError("fee_bps and slippage_bps must not be negative")
        if not 0 < allocation_ratio <= 1:
            raise ValueError("allocation_ratio must be greater than zero and at most one")
        if self.fast_window < 2 or self.slow_window <= self.fast_window:
            raise ValueError("slow_window must be greater than fast_window >= 2")
        if threshold < 0:
            raise ValueError("momentum_threshold_pct must not be negative")
        if not isinstance(self.parameters, Mapping):
            raise ValueError("parameters must be an object")
        normalized_parameters = {str(key).strip(): value for key, value in self.parameters.items() if str(key).strip()}
        if len(normalized_parameters) > 40:
            raise ValueError("parameters contains too many entries")
        object.__setattr__(self, "strategy_id", strategy_id)
        object.__setattr__(self, "initial_quote", initial_quote)
        object.__setattr__(self, "initial_base", initial_base)
        object.__setattr__(self, "fee_bps", fee_bps)
        object.__setattr__(self, "slippage_bps", slippage_bps)
        object.__setattr__(self, "allocation_ratio", allocation_ratio)
        object.__setattr__(self, "momentum_threshold_pct", threshold)
        object.__setattr__(self, "parameters", normalized_parameters)


@dataclass(frozen=True, slots=True)
class CandleBacktestResult:
    run_id: str
    dataset_id: str
    strategy_id: str
    interval: str
    candle_count: int
    start_at: str
    end_at: str
    initial_equity: Decimal
    final_equity: Decimal
    total_return_pct: Decimal
    max_drawdown_pct: Decimal
    max_drawdown_quote: Decimal
    fees_quote: Decimal
    orders: int
    filled_orders: int
    round_trips: int
    winning_round_trips: int
    win_rate_pct: Decimal
    equity_curve: tuple[dict[str, str], ...]
    trade_log: tuple[dict[str, str], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "dataset_id": self.dataset_id,
            "strategy_id": self.strategy_id,
            "interval": self.interval,
            "candle_count": self.candle_count,
            "start_at": self.start_at,
            "end_at": self.end_at,
            "initial_equity": str(self.initial_equity),
            "final_equity": str(self.final_equity),
            "total_return_pct": str(self.total_return_pct),
            "max_drawdown_pct": str(self.max_drawdown_pct),
            "max_drawdown_quote": str(self.max_drawdown_quote),
            "fees_quote": str(self.fees_quote),
            "orders": self.orders,
            "filled_orders": self.filled_orders,
            "round_trips": self.round_trips,
            "winning_round_trips": self.winning_round_trips,
            "win_rate_pct": str(self.win_rate_pct),
            "equity_curve": list(self.equity_curve),
            "trade_log": list(self.trade_log),
        }


class CandleBacktestEngine:
    """Run a long-only, next-open execution model over verified candles."""

    def run(
        self,
        candles: Iterable[Candle],
        *,
        config: CandleBacktestConfig,
        run_id: str,
        dataset_id: str,
    ) -> CandleBacktestResult:
        rows = tuple(candles)
        self._validate_rows(rows, config)
        first = rows[0]
        last = rows[-1]
        initial_equity = config.initial_quote + config.initial_base * first.open
        cash = config.initial_quote
        base = config.initial_base
        entry_cost = Decimal("0")
        fee_rate = config.fee_bps / Decimal("10000")
        slippage_rate = config.slippage_bps / Decimal("10000")
        pending: tuple[str, str] | None = None
        fees_quote = Decimal("0")
        orders = 0
        filled_orders = 0
        round_trips = 0
        winning_round_trips = 0
        trade_log: list[dict[str, str]] = []
        equity_points: list[tuple[str, Decimal]] = []
        signals = self._signal_series(rows, config)

        def execute_buy(candle: Candle, reason: str) -> None:
            nonlocal cash, base, entry_cost, fees_quote, orders, filled_orders
            if cash <= 0 or base > 0:
                return
            raw_price = candle.open
            fill_price = raw_price * (Decimal("1") + slippage_rate)
            budget = cash * config.allocation_ratio
            quantity = budget / (fill_price * (Decimal("1") + fee_rate))
            if quantity <= 0:
                return
            gross = fill_price * quantity
            fee = gross * fee_rate
            total = gross + fee
            if total > cash:
                quantity = cash / (fill_price * (Decimal("1") + fee_rate))
                gross = fill_price * quantity
                fee = gross * fee_rate
                total = gross + fee
            if quantity <= 0:
                return
            cash -= total
            base += quantity
            entry_cost = total
            orders += 1
            filled_orders += 1
            fees_quote += fee
            trade_log.append(
                {
                    "side": "BUY",
                    "timestamp": candle.open_time.isoformat(),
                    "price": str(fill_price),
                    "quantity": str(quantity),
                    "fee_quote": str(fee),
                    "reason": reason,
                    "pnl_quote": "",
                }
            )

        def execute_sell(candle: Candle, raw_price: Decimal, reason: str) -> None:
            nonlocal cash, base, entry_cost, fees_quote, orders, filled_orders
            nonlocal round_trips, winning_round_trips
            if base <= 0:
                return
            fill_price = raw_price * (Decimal("1") - slippage_rate)
            quantity = base
            gross = fill_price * quantity
            fee = gross * fee_rate
            proceeds = gross - fee
            realized = proceeds - entry_cost if entry_cost > 0 else None
            cash += proceeds
            base = Decimal("0")
            entry_cost = Decimal("0")
            orders += 1
            filled_orders += 1
            fees_quote += fee
            if realized is not None:
                round_trips += 1
                if realized > 0:
                    winning_round_trips += 1
            trade_log.append(
                {
                    "side": "SELL",
                    "timestamp": candle.close_time.isoformat(),
                    "price": str(fill_price),
                    "quantity": str(quantity),
                    "fee_quote": str(fee),
                    "reason": reason,
                    "pnl_quote": "" if realized is None else str(realized),
                }
            )

        for index, candle in enumerate(rows):
            if pending is not None:
                side, reason = pending
                if side == "BUY":
                    execute_buy(candle, reason)
                else:
                    execute_sell(candle, candle.open, reason)
                pending = None

            signal = signals[index]
            if index == len(rows) - 1 and base > 0:
                execute_sell(candle, candle.close, "end_of_data")
            elif signal == "BUY" and base <= 0:
                pending = ("BUY", f"{config.strategy_id}:entry")
            elif signal == "SELL" and base > 0:
                pending = ("SELL", f"{config.strategy_id}:exit")

            equity_points.append((candle.close_time.isoformat(), cash + base * candle.close))

        final_equity = cash + base * last.close
        total_return = (final_equity / initial_equity - Decimal("1")) * Decimal("100")
        max_drawdown_quote, max_drawdown_pct = self._drawdown(equity_points)
        win_rate = (
            Decimal(winning_round_trips) / Decimal(round_trips) * Decimal("100")
            if round_trips
            else Decimal("0")
        )
        return CandleBacktestResult(
            run_id=str(run_id),
            dataset_id=str(dataset_id),
            strategy_id=config.strategy_id,
            interval=first.interval.value,
            candle_count=len(rows),
            start_at=first.open_time.isoformat(),
            end_at=last.close_time.isoformat(),
            initial_equity=initial_equity,
            final_equity=final_equity,
            total_return_pct=total_return,
            max_drawdown_pct=max_drawdown_pct,
            max_drawdown_quote=max_drawdown_quote,
            fees_quote=fees_quote,
            orders=orders,
            filled_orders=filled_orders,
            round_trips=round_trips,
            winning_round_trips=winning_round_trips,
            win_rate_pct=win_rate,
            equity_curve=self._compact_curve(equity_points),
            trade_log=tuple(trade_log[-40:]),
        )

    @classmethod
    def _signal_series(cls, rows: tuple[Candle, ...], config: CandleBacktestConfig) -> tuple[str | None, ...]:
        """Build signals once so long histories do not repeat indicator work."""
        if config.strategy_id != "macd_reversal":
            return tuple(cls._signal(rows, index, config) for index in range(len(rows)))

        fast = _integer_parameter(config.parameters, "fast", 12, minimum=2)
        slow = _integer_parameter(config.parameters, "slow", 26, minimum=3)
        signal_period = _integer_parameter(config.parameters, "signal", 9, minimum=2)
        if slow <= fast:
            raise ValueError("strategy parameter slow must be greater than fast")
        histogram = cls._macd_hist_series(rows, fast, slow, signal_period)
        signals: list[str | None] = []
        for index, current in enumerate(histogram):
            if index < slow + signal_period:
                signals.append(None)
                continue
            previous = histogram[index - 1]
            if previous <= 0 and current > 0:
                signals.append("BUY")
            elif previous >= 0 and current < 0:
                signals.append("SELL")
            else:
                signals.append(None)
        return tuple(signals)

    @staticmethod
    def _validate_rows(rows: tuple[Candle, ...], config: CandleBacktestConfig) -> None:
        required = CandleBacktestEngine.required_lookback(config)
        if len(rows) < max(2, required):
            raise ValueError(f"backtest requires at least {max(2, required)} candles")
        previous = rows[0]
        for current in rows[1:]:
            if current.open_time <= previous.open_time:
                raise ValueError("candles must be strictly chronological")
            if current.interval != previous.interval:
                raise ValueError("candles must use one interval")
            previous = current

    @classmethod
    def signal_at(cls, rows: Iterable[Candle], index: int, config: CandleBacktestConfig) -> str | None:
        """Return the signal at one point without executing a future candle."""
        prepared = tuple(rows)
        if index < 0 or index >= len(prepared):
            raise IndexError("signal index is outside the candle range")
        return cls._signal(prepared, index, config)

    @staticmethod
    def required_lookback(config: CandleBacktestConfig) -> int:
        params = config.parameters
        if config.strategy_id == "sma_cross":
            return _integer_parameter(params, "slow_window", config.slow_window, minimum=3)
        if config.strategy_id == "inventory_exit":
            return _integer_parameter(params, "window", config.fast_window, minimum=2)
        if config.strategy_id == "momentum":
            return _integer_parameter(params, "lookback", config.slow_window, minimum=2)
        if config.strategy_id in {"trend_breakout", "bollinger_breakout", "volume_momentum", "volatility_breakout"}:
            return _integer_parameter(params, "window", 20, minimum=2)
        if config.strategy_id == "rsi_rebound":
            return _integer_parameter(params, "period", 14, minimum=2) + 1
        if config.strategy_id == "macd_reversal":
            return max(_integer_parameter(params, "slow", 26, minimum=3), 3) + _integer_parameter(params, "signal", 9, minimum=2)
        return 2

    @staticmethod
    def _signal(rows: tuple[Candle, ...], index: int, config: CandleBacktestConfig) -> str | None:
        params = config.parameters
        if config.strategy_id == "buy_and_hold":
            return "BUY" if index == 0 else None
        if config.strategy_id == "momentum":
            lookback = _integer_parameter(params, "lookback", config.slow_window, minimum=2)
            threshold = _decimal_parameter(params, "threshold", config.momentum_threshold_pct, minimum=Decimal("0"))
            if index < lookback:
                return None
            change = rows[index].close / rows[index - lookback].close - Decimal("1")
            return "BUY" if change >= threshold else "SELL" if change < 0 else None
        if config.strategy_id == "inventory_exit":
            window = _integer_parameter(params, "window", config.fast_window, minimum=2)
            if index + 1 < window:
                return None
            average = sum((row.close for row in rows[index + 1 - window : index + 1]), Decimal("0")) / Decimal(window)
            return "SELL" if rows[index].close < average else None
        if config.strategy_id == "trend_breakout":
            window = _integer_parameter(params, "window", 20, minimum=2)
            minimum_return = _decimal_parameter(params, "min_return", Decimal("0"), minimum=Decimal("0"))
            if index < window:
                return None
            prior_high = max(row.high for row in rows[index - window : index])
            period_return = rows[index].close / rows[index - window].close - Decimal("1")
            if rows[index].close > prior_high and period_return >= minimum_return:
                return "BUY"
            return "SELL" if rows[index].close < CandleBacktestEngine._sma(rows, index, min(window, 10)) else None
        if config.strategy_id == "rsi_rebound":
            period = _integer_parameter(params, "period", 14, minimum=2)
            max_rsi = _decimal_parameter(params, "max_rsi", Decimal("35"), minimum=Decimal("0"))
            exit_rsi = _decimal_parameter(params, "exit_rsi", Decimal("65"), minimum=Decimal("0"))
            if index < period:
                return None
            rsi = CandleBacktestEngine._rsi(rows, index, period)
            previous_rsi = CandleBacktestEngine._rsi(rows, index - 1, period) if index > period else rsi
            if rsi <= max_rsi and rows[index].close > rows[index - 1].close and rsi >= previous_rsi:
                return "BUY"
            if rsi >= exit_rsi or rows[index].close < CandleBacktestEngine._sma(rows, index, min(period, index + 1)):
                return "SELL"
            return None
        if config.strategy_id == "bollinger_breakout":
            window = _integer_parameter(params, "window", 20, minimum=3)
            multiplier = _decimal_parameter(params, "std_multiplier", Decimal("2"), minimum=Decimal("0.1"))
            if index < window:
                return None
            current_band = CandleBacktestEngine._bollinger(rows, index, window, multiplier)
            previous_band = CandleBacktestEngine._bollinger(rows, index - 1, window, multiplier)
            if rows[index].close > current_band[1] and rows[index - 1].close <= previous_band[1]:
                return "BUY"
            return "SELL" if rows[index].close < current_band[0] else None
        if config.strategy_id == "macd_reversal":
            fast = _integer_parameter(params, "fast", 12, minimum=2)
            slow = _integer_parameter(params, "slow", 26, minimum=3)
            signal_period = _integer_parameter(params, "signal", 9, minimum=2)
            if slow <= fast or index < slow + signal_period:
                return None
            histogram = CandleBacktestEngine._macd_hist_series(rows, fast, slow, signal_period)
            current = histogram[index]
            previous = histogram[index - 1]
            if previous <= 0 and current > 0:
                return "BUY"
            if previous >= 0 and current < 0:
                return "SELL"
            return None
        if config.strategy_id == "volume_momentum":
            window = _integer_parameter(params, "window", 20, minimum=2)
            minimum_return = _decimal_parameter(params, "min_return", Decimal("0.01"), minimum=Decimal("0"))
            minimum_volume_ratio = _decimal_parameter(params, "min_volume_ratio", Decimal("1.2"), minimum=Decimal("0"))
            if index < window:
                return None
            average_volume = sum((row.volume for row in rows[index - window : index]), Decimal("0")) / Decimal(window)
            volume_ratio = rows[index].volume / average_volume if average_volume else Decimal("0")
            change = rows[index].close / rows[index - 1].close - Decimal("1")
            if change >= minimum_return and volume_ratio >= minimum_volume_ratio:
                return "BUY"
            return "SELL" if change < 0 else None
        if config.strategy_id == "volatility_breakout":
            window = _integer_parameter(params, "window", 20, minimum=3)
            multiplier = _decimal_parameter(params, "atr_multiplier", Decimal("1.5"), minimum=Decimal("0"))
            if index < window + 1:
                return None
            prior_high = max(row.high for row in rows[index - window : index])
            atr = CandleBacktestEngine._atr(rows, index, window)
            move = rows[index].close - rows[index - 1].close
            if rows[index].close > prior_high and move >= atr * multiplier:
                return "BUY"
            return "SELL" if rows[index].close < CandleBacktestEngine._sma(rows, index, min(window, 10)) else None
        fast_window = _integer_parameter(params, "fast_window", config.fast_window, minimum=2)
        slow_window = _integer_parameter(params, "slow_window", config.slow_window, minimum=3)
        if slow_window <= fast_window:
            raise ValueError("strategy parameter slow_window must be greater than fast_window")
        if index < slow_window:
            return None
        current_fast = CandleBacktestEngine._sma(rows, index, fast_window)
        current_slow = CandleBacktestEngine._sma(rows, index, slow_window)
        previous_fast = CandleBacktestEngine._sma(rows, index - 1, fast_window)
        previous_slow = CandleBacktestEngine._sma(rows, index - 1, slow_window)
        if previous_fast <= previous_slow and current_fast > current_slow:
            return "BUY"
        if previous_fast >= previous_slow and current_fast < current_slow:
            return "SELL"
        return None

    @staticmethod
    def _sma(rows: tuple[Candle, ...], index: int, window: int) -> Decimal:
        return sum((row.close for row in rows[index + 1 - window : index + 1]), Decimal("0")) / Decimal(window)

    @staticmethod
    def _rsi(rows: tuple[Candle, ...], index: int, period: int) -> Decimal:
        changes = [rows[position].close - rows[position - 1].close for position in range(index - period + 1, index + 1)]
        gains = sum((change for change in changes if change > 0), Decimal("0")) / Decimal(period)
        losses = sum((-change for change in changes if change < 0), Decimal("0")) / Decimal(period)
        if losses == 0:
            return Decimal("100")
        return Decimal("100") - Decimal("100") / (Decimal("1") + gains / losses)

    @staticmethod
    def _bollinger(rows: tuple[Candle, ...], index: int, window: int, multiplier: Decimal) -> tuple[Decimal, Decimal, Decimal]:
        values = [row.close for row in rows[index + 1 - window : index + 1]]
        mean = sum(values, Decimal("0")) / Decimal(window)
        variance = sum((value - mean) ** 2 for value in values) / Decimal(window)
        deviation = Decimal(str(float(variance) ** 0.5))
        return mean, mean + multiplier * deviation, mean - multiplier * deviation

    @staticmethod
    def _ema(values: list[Decimal], period: int) -> Decimal:
        result = values[0]
        alpha = Decimal("2") / Decimal(period + 1)
        for value in values[1:]:
            result = (value - result) * alpha + result
        return result

    @classmethod
    def _macd_hist(cls, rows: tuple[Candle, ...], index: int, fast: int, slow: int, signal_period: int) -> Decimal:
        series = cls._macd_hist_series(rows[: index + 1], fast, slow, signal_period)
        return series[-1] if series else Decimal("0")

    @classmethod
    def _macd_hist_series(
        cls,
        rows: tuple[Candle, ...],
        fast: int,
        slow: int,
        signal_period: int,
    ) -> tuple[Decimal, ...]:
        """Match the EMA seed used by the legacy calculation in O(n)."""
        if not rows:
            return ()
        fast_alpha = Decimal("2") / Decimal(fast + 1)
        slow_alpha = Decimal("2") / Decimal(slow + 1)
        signal_alpha = Decimal("2") / Decimal(signal_period + 1)
        fast_ema = rows[0].close
        slow_ema = rows[0].close
        signal_ema: Decimal | None = None
        histogram: list[Decimal] = [Decimal("0")] * len(rows)
        for index, row in enumerate(rows):
            if index:
                fast_ema = (row.close - fast_ema) * fast_alpha + fast_ema
                slow_ema = (row.close - slow_ema) * slow_alpha + slow_ema
            if index < slow - 1:
                continue
            macd_value = fast_ema - slow_ema
            signal_ema = macd_value if signal_ema is None else (macd_value - signal_ema) * signal_alpha + signal_ema
            macd_count = index - slow + 2
            if macd_count >= signal_period:
                histogram[index] = macd_value - signal_ema
        return tuple(histogram)

    @staticmethod
    def _atr(rows: tuple[Candle, ...], index: int, window: int) -> Decimal:
        start = max(1, index - window + 1)
        ranges = []
        for position in range(start, index + 1):
            previous_close = rows[position - 1].close
            ranges.append(max(rows[position].high - rows[position].low, abs(rows[position].high - previous_close), abs(rows[position].low - previous_close)))
        return sum(ranges, Decimal("0")) / Decimal(len(ranges) or 1)

    @staticmethod
    def _drawdown(points: list[tuple[str, Decimal]]) -> tuple[Decimal, Decimal]:
        peak = Decimal("0")
        max_quote = Decimal("0")
        max_pct = Decimal("0")
        for _, equity in points:
            peak = max(peak, equity)
            if peak <= 0:
                continue
            drawdown = peak - equity
            max_quote = max(max_quote, drawdown)
            max_pct = max(max_pct, drawdown / peak * Decimal("100"))
        return max_quote, max_pct

    @staticmethod
    def _compact_curve(points: list[tuple[str, Decimal]], maximum: int = 160) -> tuple[dict[str, str], ...]:
        if len(points) <= maximum:
            selected = points
        else:
            step = (len(points) - 1) / (maximum - 1)
            indexes = {round(index * step) for index in range(maximum)}
            selected = [points[index] for index in sorted(indexes)]
        return tuple({"timestamp": timestamp, "equity": str(equity)} for timestamp, equity in selected)


def _decimal_parameter(parameters: Mapping[str, Any], key: str, default: Decimal, *, minimum: Decimal | None = None) -> Decimal:
    raw = parameters.get(key, default)
    try:
        value = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"strategy parameter {key} must be numeric") from error
    if not value.is_finite() or (minimum is not None and value < minimum):
        raise ValueError(f"strategy parameter {key} is outside the allowed range")
    return value


def _integer_parameter(parameters: Mapping[str, Any], key: str, default: int, *, minimum: int) -> int:
    value = _decimal_parameter(parameters, key, Decimal(default), minimum=Decimal(minimum))
    if value != value.to_integral_value():
        raise ValueError(f"strategy parameter {key} must be an integer")
    return int(value)
