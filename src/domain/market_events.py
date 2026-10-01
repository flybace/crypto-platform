"""Canonical incremental market events emitted by public venue streams."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .market import Instrument, _aware, _decimal


def _non_negative(value: Decimal | int | str, field: str) -> Decimal:
    result = _decimal(value, field)
    if result < 0:
        raise ValueError(f"{field} must not be negative")
    return result


@dataclass(frozen=True, slots=True)
class PriceLevelUpdate:
    """One price-level replacement; quantity zero removes the level."""

    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        price = _decimal(self.price, "price")
        if price <= 0:
            raise ValueError("price must be greater than zero")
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "quantity", _non_negative(self.quantity, "quantity"))


@dataclass(frozen=True, slots=True)
class OrderBookDelta:
    """A venue-normalized L2 update with its native sequence interval."""

    instrument: Instrument
    bids: tuple[PriceLevelUpdate, ...]
    asks: tuple[PriceLevelUpdate, ...]
    exchange_timestamp: datetime
    received_timestamp: datetime
    first_sequence: int
    last_sequence: int
    previous_sequence: int | None = None

    def __post_init__(self) -> None:
        bids = tuple(self.bids)
        asks = tuple(self.asks)
        first_sequence = int(self.first_sequence)
        last_sequence = int(self.last_sequence)
        if first_sequence < 0 or last_sequence < 0:
            raise ValueError("sequence values must not be negative")
        if first_sequence > last_sequence:
            raise ValueError("first_sequence must not exceed last_sequence")
        previous_sequence = self.previous_sequence
        if previous_sequence is not None:
            previous_sequence = int(previous_sequence)
            if previous_sequence < -1:
                raise ValueError("previous_sequence must be -1 or greater")
        for side, levels in (("bids", bids), ("asks", asks)):
            prices = [level.price for level in levels]
            if len(prices) != len(set(prices)):
                raise ValueError(f"{side} contains duplicate price levels")
        _aware(self.exchange_timestamp, "exchange_timestamp")
        _aware(self.received_timestamp, "received_timestamp")
        object.__setattr__(self, "bids", bids)
        object.__setattr__(self, "asks", asks)
        object.__setattr__(self, "first_sequence", first_sequence)
        object.__setattr__(self, "last_sequence", last_sequence)
        object.__setattr__(self, "previous_sequence", previous_sequence)

    @property
    def key(self) -> str:
        return self.instrument.key
