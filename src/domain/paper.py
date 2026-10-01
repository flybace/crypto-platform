"""Paper-account and paper-order value objects."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from .market import _aware, _decimal
from .trading import OrderIntent, Side


class PaperOrderStatus(StrEnum):
    OPEN = "OPEN"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


def _non_negative(value: Decimal | int | str, field: str) -> Decimal:
    result = _decimal(value, field)
    if result < 0:
        raise ValueError(f"{field} must not be negative")
    return result


@dataclass(frozen=True, slots=True)
class PaperFill:
    fill_id: str
    order_id: str
    side: Side
    price: Decimal
    quantity: Decimal
    fee_amount: Decimal
    fee_asset: str
    filled_at: datetime

    def __post_init__(self) -> None:
        side = self.side if isinstance(self.side, Side) else Side(self.side)
        price = _decimal(self.price, "price")
        quantity = _non_negative(self.quantity, "quantity")
        fee_amount = _non_negative(self.fee_amount, "fee_amount")
        if price <= 0 or quantity <= 0:
            raise ValueError("fill price and quantity must be positive")
        _aware(self.filled_at, "filled_at")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "fee_amount", fee_amount)
        object.__setattr__(self, "fee_asset", str(self.fee_asset).strip().upper())


@dataclass(frozen=True, slots=True)
class PaperOrder:
    order_id: str
    intent: OrderIntent
    status: PaperOrderStatus
    filled_quantity: Decimal
    remaining_quantity: Decimal
    fills: tuple[PaperFill, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        status = self.status if isinstance(self.status, PaperOrderStatus) else PaperOrderStatus(self.status)
        filled = _non_negative(self.filled_quantity, "filled_quantity")
        remaining = _non_negative(self.remaining_quantity, "remaining_quantity")
        if filled + remaining != self.intent.quantity:
            raise ValueError("paper order quantities do not reconcile")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "filled_quantity", filled)
        object.__setattr__(self, "remaining_quantity", remaining)
        object.__setattr__(self, "fills", tuple(self.fills))


class PaperAccount:
    """Mutable aggregate whose balance changes are only caused by fills."""

    def __init__(self, account_id: str, balances: dict[str, Decimal | int | str] | None = None) -> None:
        self.account_id = str(account_id).strip()
        if not self.account_id:
            raise ValueError("account_id must not be empty")
        self._balances: dict[str, Decimal] = {}
        for asset, amount in (balances or {}).items():
            self._balances[str(asset).strip().upper()] = _non_negative(amount, f"balance[{asset}]")

    def available(self, asset: str) -> Decimal:
        return self._balances.get(str(asset).strip().upper(), Decimal("0"))

    def snapshot(self) -> dict[str, Decimal]:
        return dict(self._balances)

    def apply_fill(self, intent: OrderIntent, fill: PaperFill) -> None:
        instrument = intent.instrument
        base = instrument.base_asset
        quote = instrument.quote_asset
        gross = fill.price * fill.quantity
        if fill.side is Side.SELL:
            self._debit(base, fill.quantity)
            self._credit(quote, gross - fill.fee_amount if fill.fee_asset == quote else gross)
            if fill.fee_asset != quote:
                self._debit(fill.fee_asset, fill.fee_amount)
        else:
            self._debit(quote, gross + fill.fee_amount if fill.fee_asset == quote else gross)
            self._credit(base, fill.quantity)
            if fill.fee_asset != quote:
                self._debit(fill.fee_asset, fill.fee_amount)

    def _credit(self, asset: str, amount: Decimal) -> None:
        if amount < 0:
            raise ValueError("fill credit cannot be negative")
        self._balances[asset] = self.available(asset) + amount

    def _debit(self, asset: str, amount: Decimal) -> None:
        if self.available(asset) < amount:
            raise ValueError(f"insufficient paper balance: {asset}")
        self._balances[asset] = self.available(asset) - amount
