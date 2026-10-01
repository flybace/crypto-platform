"""Strategy metadata and signal objects without execution side effects."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .market import Instrument, _aware, _decimal, _positive
from .trading import ExecutionMode, Side


class StrategyMode:
    OBSERVE = "observe"
    RESEARCH = "research"
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE_PENDING = "live-pending"
    LIVE_ENABLED = "live-enabled"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy_id: str
    version: str
    mode: str
    parameters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        strategy_id = str(self.strategy_id).strip()
        version = str(self.version).strip()
        mode = str(self.mode).strip().lower()
        if not strategy_id or not version:
            raise ValueError("strategy_id and version must not be empty")
        valid_modes = {
            StrategyMode.OBSERVE,
            StrategyMode.RESEARCH,
            StrategyMode.BACKTEST,
            StrategyMode.PAPER,
            StrategyMode.LIVE_PENDING,
            StrategyMode.LIVE_ENABLED,
            StrategyMode.PAUSED,
            StrategyMode.STOPPED,
        }
        if mode not in valid_modes:
            raise ValueError(f"unknown strategy mode: {mode}")
        object.__setattr__(self, "strategy_id", strategy_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "parameters", tuple(self.parameters))


@dataclass(frozen=True, slots=True)
class StrategySignal:
    signal_id: str
    strategy: StrategySpec
    account_id: str
    instrument: Instrument
    side: Side
    quantity: Decimal
    limit_price: Decimal
    reference_price: Decimal
    max_slippage_bps: Decimal
    generated_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.strategy.mode in {StrategyMode.OBSERVE, StrategyMode.RESEARCH, StrategyMode.PAUSED, StrategyMode.STOPPED}:
            raise ValueError("strategy mode cannot emit an execution signal")
        if not str(self.signal_id).strip() or not str(self.account_id).strip():
            raise ValueError("signal_id and account_id must not be empty")
        if self.expires_at <= self.generated_at:
            raise ValueError("signal expires_at must be after generated_at")
        _aware(self.generated_at, "generated_at")
        _aware(self.expires_at, "expires_at")
        side = self.side if isinstance(self.side, Side) else Side(self.side)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "quantity", _positive(_decimal(self.quantity, "quantity"), "quantity"))
        object.__setattr__(self, "limit_price", _positive(_decimal(self.limit_price, "limit_price"), "limit_price"))
        object.__setattr__(self, "reference_price", _positive(_decimal(self.reference_price, "reference_price"), "reference_price"))
        slippage = _decimal(self.max_slippage_bps, "max_slippage_bps")
        if slippage < 0:
            raise ValueError("max_slippage_bps must not be negative")
        object.__setattr__(self, "max_slippage_bps", slippage)


class InventorySellStrategy:
    """Generate a sell signal only; conversion to an order is another use case."""

    def __init__(self, spec: StrategySpec) -> None:
        self._spec = spec

    def generate(
        self,
        *,
        signal_id: str,
        account_id: str,
        instrument: Instrument,
        quantity: Decimal,
        limit_price: Decimal,
        reference_price: Decimal,
        max_slippage_bps: Decimal,
        generated_at: datetime,
        ttl: timedelta = timedelta(seconds=1),
    ) -> StrategySignal:
        if self._spec.mode not in {StrategyMode.BACKTEST, StrategyMode.PAPER, StrategyMode.LIVE_PENDING, StrategyMode.LIVE_ENABLED}:
            raise ValueError("strategy mode cannot emit an execution signal")
        if ttl <= timedelta(0):
            raise ValueError("signal ttl must be positive")
        return StrategySignal(
            signal_id=signal_id,
            strategy=self._spec,
            account_id=account_id,
            instrument=instrument,
            side=Side.SELL,
            quantity=quantity,
            limit_price=limit_price,
            reference_price=reference_price,
            max_slippage_bps=max_slippage_bps,
            generated_at=generated_at,
            expires_at=generated_at + ttl,
        )
