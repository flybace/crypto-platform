"""Cost-aware cross-market opportunity objects."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from .market import _aware, _decimal


def _non_negative(value: Decimal | int | str, field: str) -> Decimal:
    result = _decimal(value, field)
    if result < 0:
        raise ValueError(f"{field} must not be negative")
    return result


class OpportunityState(StrEnum):
    VALIDATED = "VALIDATED"
    BLOCKED = "BLOCKED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    venue_id: str
    maker_bps: Decimal
    taker_bps: Decimal
    as_of: datetime

    def __post_init__(self) -> None:
        venue_id = str(self.venue_id).strip().lower()
        maker_bps = _non_negative(self.maker_bps, "maker_bps")
        taker_bps = _non_negative(self.taker_bps, "taker_bps")
        _aware(self.as_of, "as_of")
        object.__setattr__(self, "venue_id", venue_id)
        object.__setattr__(self, "maker_bps", maker_bps)
        object.__setattr__(self, "taker_bps", taker_bps)


@dataclass(frozen=True, slots=True)
class CostModel:
    expected_slippage_bps: Decimal = Decimal("0")
    inventory_cost_quote: Decimal = Decimal("0")
    transfer_cost_quote: Decimal = Decimal("0")
    latency_buffer_quote: Decimal = Decimal("0")
    failure_risk_buffer_quote: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected_slippage_bps", _non_negative(self.expected_slippage_bps, "expected_slippage_bps"))
        for field in (
            "inventory_cost_quote",
            "transfer_cost_quote",
            "latency_buffer_quote",
            "failure_risk_buffer_quote",
        ):
            object.__setattr__(self, field, _non_negative(getattr(self, field), field))


@dataclass(frozen=True, slots=True)
class Opportunity:
    buy_venue_id: str
    sell_venue_id: str
    instrument_key: str
    quantity: Decimal
    buy_price: Decimal
    sell_price: Decimal
    gross_edge_quote: Decimal
    fees_quote: Decimal
    slippage_quote: Decimal
    other_costs_quote: Decimal
    net_edge_quote: Decimal
    created_at: datetime
    expires_at: datetime
    state: OpportunityState
    blocking_reason: str | None = None

    def __post_init__(self) -> None:
        buy_venue_id = str(self.buy_venue_id).strip().lower()
        sell_venue_id = str(self.sell_venue_id).strip().lower()
        instrument_key = str(self.instrument_key).strip()
        if not buy_venue_id or not sell_venue_id or not instrument_key:
            raise ValueError("opportunity identity fields must not be empty")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        _aware(self.created_at, "created_at")
        _aware(self.expires_at, "expires_at")
        object.__setattr__(self, "buy_venue_id", buy_venue_id)
        object.__setattr__(self, "sell_venue_id", sell_venue_id)
        object.__setattr__(self, "instrument_key", instrument_key)
        for field in (
            "quantity",
            "buy_price",
            "sell_price",
            "gross_edge_quote",
            "fees_quote",
            "slippage_quote",
            "other_costs_quote",
        ):
            object.__setattr__(self, field, _non_negative(getattr(self, field), field))
        object.__setattr__(self, "net_edge_quote", _decimal(self.net_edge_quote, "net_edge_quote"))
        state = self.state if isinstance(self.state, OpportunityState) else OpportunityState(self.state)
        object.__setattr__(self, "state", state)
