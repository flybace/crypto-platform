"""Canonical market objects shared by every venue adapter."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from enum import StrEnum


def _decimal(value: Decimal | int | str, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be a decimal value") from error
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _positive(value: Decimal, field: str) -> Decimal:
    if value <= 0:
        raise ValueError(f"{field} must be greater than zero")
    return value


def _asset_code(value: str, field: str) -> str:
    result = str(value).strip().upper()
    if not result or any(character.isspace() for character in result) or "/" in result:
        raise ValueError(f"{field} must be a non-empty asset code")
    return result


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include timezone information")
    return value


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


class MarketType(StrEnum):
    SPOT = "spot"
    MARGIN = "margin"
    PERPETUAL = "perpetual"
    FUTURES = "futures"
    DEX = "dex"


@dataclass(frozen=True, slots=True)
class Instrument:
    """A venue-scoped instrument with rules needed for safe sizing."""

    venue_id: str
    market_type: MarketType
    base_asset: str
    quote_asset: str
    native_symbol: str
    price_tick: Decimal
    quantity_step: Decimal
    min_quantity: Decimal
    min_notional: Decimal

    def __post_init__(self) -> None:
        venue_id = str(self.venue_id).strip().lower()
        if not venue_id:
            raise ValueError("venue_id must not be empty")
        market_type = self.market_type if isinstance(self.market_type, MarketType) else MarketType(self.market_type)
        base_asset = _asset_code(self.base_asset, "base_asset")
        quote_asset = _asset_code(self.quote_asset, "quote_asset")
        if base_asset == quote_asset:
            raise ValueError("base_asset and quote_asset must differ")
        native_symbol = str(self.native_symbol).strip()
        if not native_symbol:
            raise ValueError("native_symbol must not be empty")

        decimals = {
            "price_tick": _positive(_decimal(self.price_tick, "price_tick"), "price_tick"),
            "quantity_step": _positive(_decimal(self.quantity_step, "quantity_step"), "quantity_step"),
            "min_quantity": _positive(_decimal(self.min_quantity, "min_quantity"), "min_quantity"),
            "min_notional": _positive(_decimal(self.min_notional, "min_notional"), "min_notional"),
        }
        object.__setattr__(self, "venue_id", venue_id)
        object.__setattr__(self, "market_type", market_type)
        object.__setattr__(self, "base_asset", base_asset)
        object.__setattr__(self, "quote_asset", quote_asset)
        object.__setattr__(self, "native_symbol", native_symbol)
        for field, value in decimals.items():
            object.__setattr__(self, field, value)

    @property
    def canonical_symbol(self) -> str:
        return f"{self.base_asset}/{self.quote_asset}"

    @property
    def key(self) -> str:
        return f"{self.venue_id}:{self.market_type.value}:{self.canonical_symbol}"

    def normalize_price(self, value: Decimal | int | str) -> Decimal:
        price = _positive(_decimal(value, "price"), "price")
        return _floor_to_step(price, self.price_tick)

    def normalize_quantity(self, value: Decimal | int | str) -> Decimal:
        quantity = _positive(_decimal(value, "quantity"), "quantity")
        return _floor_to_step(quantity, self.quantity_step)


@dataclass(frozen=True, slots=True)
class PriceLevel:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", _positive(_decimal(self.price, "price"), "price"))
        object.__setattr__(self, "quantity", _positive(_decimal(self.quantity, "quantity"), "quantity"))


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    """A validated L2 snapshot; deltas are applied by a venue adapter."""

    instrument: Instrument
    bids: tuple[PriceLevel, ...]
    asks: tuple[PriceLevel, ...]
    exchange_timestamp: datetime
    received_timestamp: datetime
    sequence: int

    def __post_init__(self) -> None:
        bids = tuple(self.bids)
        asks = tuple(self.asks)
        if self.sequence < 0:
            raise ValueError("sequence must not be negative")
        exchange_timestamp = _aware(self.exchange_timestamp, "exchange_timestamp")
        received_timestamp = _aware(self.received_timestamp, "received_timestamp")
        if any(current.price >= previous.price for previous, current in zip(bids, bids[1:])):
            raise ValueError("bids must be strictly descending")
        if any(current.price <= previous.price for previous, current in zip(asks, asks[1:])):
            raise ValueError("asks must be strictly ascending")
        if bids and asks and bids[0].price >= asks[0].price:
            raise ValueError("order book must not be crossed")
        object.__setattr__(self, "bids", bids)
        object.__setattr__(self, "asks", asks)
        object.__setattr__(self, "exchange_timestamp", exchange_timestamp)
        object.__setattr__(self, "received_timestamp", received_timestamp)

    @property
    def best_bid(self) -> PriceLevel | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> PriceLevel | None:
        return self.asks[0] if self.asks else None

    def age_seconds(self, now: datetime) -> float:
        _aware(now, "now")
        return (now - self.received_timestamp).total_seconds()
