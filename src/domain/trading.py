"""Order and balance value objects; no venue SDK types belong here."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from .market import Instrument, _aware, _decimal, _positive


def _required_text(value: str, field: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{field} must not be empty")
    return result


class ExecutionMode(StrEnum):
    DISABLED = "DISABLED"
    SELL_ONLY = "SELL_ONLY"
    BUY_SELL = "BUY_SELL"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    LIMIT_IOC = "LIMIT_IOC"
    MARKET = "MARKET"


class OrderStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class Balance:
    asset: str
    available: Decimal
    total: Decimal

    def __post_init__(self) -> None:
        asset = _required_text(self.asset, "asset").upper()
        available = _positive_or_zero(self.available, "available")
        total = _positive_or_zero(self.total, "total")
        if available > total:
            raise ValueError("available balance cannot exceed total balance")
        object.__setattr__(self, "asset", asset)
        object.__setattr__(self, "available", available)
        object.__setattr__(self, "total", total)


def _positive_or_zero(value: Decimal | int | str, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be a decimal value") from error
    if not result.is_finite() or result < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return result


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """A strategy output; only the application layer may submit it."""

    request_id: str
    account_id: str
    venue_id: str
    instrument: Instrument
    side: Side
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None
    strategy_id: str
    strategy_version: str
    authorization_id: str
    generated_at: datetime
    reference_price: Decimal | None = None
    max_slippage_bps: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        request_id = _required_text(self.request_id, "request_id")
        account_id = _required_text(self.account_id, "account_id")
        venue_id = _required_text(self.venue_id, "venue_id").lower()
        if venue_id != self.instrument.venue_id:
            raise ValueError("venue_id must match instrument.venue_id")
        side = self.side if isinstance(self.side, Side) else Side(self.side)
        order_type = self.order_type if isinstance(self.order_type, OrderType) else OrderType(self.order_type)
        quantity = _positive(_decimal(self.quantity, "quantity"), "quantity")
        limit_price = None if self.limit_price is None else _positive(_decimal(self.limit_price, "limit_price"), "limit_price")
        if order_type is OrderType.MARKET and limit_price is not None:
            raise ValueError("market orders must not have a limit_price")
        if order_type is not OrderType.MARKET and limit_price is None:
            raise ValueError("limit orders require a limit_price")
        reference_price = None if self.reference_price is None else _positive(_decimal(self.reference_price, "reference_price"), "reference_price")
        max_slippage_bps = _positive_or_zero(self.max_slippage_bps, "max_slippage_bps")
        _aware(self.generated_at, "generated_at")
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "account_id", account_id)
        object.__setattr__(self, "venue_id", venue_id)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "order_type", order_type)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "limit_price", limit_price)
        object.__setattr__(self, "strategy_id", _required_text(self.strategy_id, "strategy_id"))
        object.__setattr__(self, "strategy_version", _required_text(self.strategy_version, "strategy_version"))
        object.__setattr__(self, "authorization_id", _required_text(self.authorization_id, "authorization_id"))
        object.__setattr__(self, "reference_price", reference_price)
        object.__setattr__(self, "max_slippage_bps", max_slippage_bps)

    @property
    def notional(self) -> Decimal:
        if self.limit_price is None:
            raise ValueError("market order notional requires an execution price")
        return self.quantity * self.limit_price
