"""Risk configuration and immutable inputs to the risk pre-check."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .market import OrderBookSnapshot, _aware, _decimal
from .trading import Balance, ExecutionMode


def _non_negative(value: Decimal | int | str, field: str) -> Decimal:
    result = _decimal(value, field)
    if result < 0:
        raise ValueError(f"{field} must not be negative")
    return result


def _texts(values: frozenset[str] | set[str] | tuple[str, ...], field: str) -> frozenset[str]:
    result = frozenset(str(value).strip() for value in values if str(value).strip())
    if not result:
        return frozenset()
    return result


@dataclass(frozen=True, slots=True)
class RiskLimits:
    enabled: bool = False
    mode: ExecutionMode = ExecutionMode.DISABLED
    venue_allowlist: frozenset[str] = frozenset()
    instrument_allowlist: frozenset[str] = frozenset()
    quote_asset_allowlist: frozenset[str] = frozenset()
    max_order_notional_quote: Decimal = Decimal("0")
    max_daily_sell_notional_quote: Decimal = Decimal("0")
    max_daily_sell_ratio: Decimal = Decimal("0")
    min_base_reserve: Decimal | None = None
    allow_market_order: bool = False
    max_slippage_bps: Decimal = Decimal("0")
    max_market_age_seconds: int = 2
    max_open_orders: int = 1
    max_orders_per_minute: int = 1

    def __post_init__(self) -> None:
        mode = self.mode if isinstance(self.mode, ExecutionMode) else ExecutionMode(self.mode)
        venue_allowlist = frozenset(value.lower() for value in _texts(self.venue_allowlist, "venue_allowlist"))
        instrument_allowlist = _texts(self.instrument_allowlist, "instrument_allowlist")
        quote_asset_allowlist = frozenset(value.upper() for value in _texts(self.quote_asset_allowlist, "quote_asset_allowlist"))
        max_order = _non_negative(self.max_order_notional_quote, "max_order_notional_quote")
        max_daily = _non_negative(self.max_daily_sell_notional_quote, "max_daily_sell_notional_quote")
        max_ratio = _non_negative(self.max_daily_sell_ratio, "max_daily_sell_ratio")
        if max_ratio > 1:
            raise ValueError("max_daily_sell_ratio must be at most 1")
        reserve = None if self.min_base_reserve is None else _non_negative(self.min_base_reserve, "min_base_reserve")
        max_slippage = _non_negative(self.max_slippage_bps, "max_slippage_bps")
        if self.max_market_age_seconds <= 0:
            raise ValueError("max_market_age_seconds must be positive")
        if self.max_open_orders < 0 or self.max_orders_per_minute <= 0:
            raise ValueError("order limits have invalid values")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "venue_allowlist", venue_allowlist)
        object.__setattr__(self, "instrument_allowlist", instrument_allowlist)
        object.__setattr__(self, "quote_asset_allowlist", quote_asset_allowlist)
        object.__setattr__(self, "max_order_notional_quote", max_order)
        object.__setattr__(self, "max_daily_sell_notional_quote", max_daily)
        object.__setattr__(self, "max_daily_sell_ratio", max_ratio)
        object.__setattr__(self, "min_base_reserve", reserve)
        object.__setattr__(self, "max_slippage_bps", max_slippage)


@dataclass(frozen=True, slots=True)
class RiskContext:
    balance: Balance
    market: OrderBookSnapshot
    now: datetime
    daily_sell_notional_quote: Decimal = Decimal("0")
    daily_sold_base_quantity: Decimal = Decimal("0")
    daily_base_reference_quantity: Decimal = Decimal("0")
    open_order_count: int = 0
    unknown_order_count: int = 0
    recent_order_timestamps: tuple[datetime, ...] = ()

    def __post_init__(self) -> None:
        _aware(self.now, "now")
        values = {
            "daily_sell_notional_quote": _non_negative(self.daily_sell_notional_quote, "daily_sell_notional_quote"),
            "daily_sold_base_quantity": _non_negative(self.daily_sold_base_quantity, "daily_sold_base_quantity"),
            "daily_base_reference_quantity": _non_negative(self.daily_base_reference_quantity, "daily_base_reference_quantity"),
        }
        if self.open_order_count < 0 or self.unknown_order_count < 0:
            raise ValueError("order counts must not be negative")
        timestamps = tuple(self.recent_order_timestamps)
        for timestamp in timestamps:
            _aware(timestamp, "recent_order_timestamp")
        for field, value in values.items():
            object.__setattr__(self, field, value)
        object.__setattr__(self, "recent_order_timestamps", timestamps)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...] = ()

    @classmethod
    def allow(cls) -> "RiskDecision":
        return cls(allowed=True)

    @classmethod
    def deny(cls, *reasons: str) -> "RiskDecision":
        return cls(allowed=False, reasons=tuple(dict.fromkeys(reasons)))

    @property
    def primary_reason(self) -> str | None:
        return self.reasons[0] if self.reasons else None
