"""Paper two-leg execution state machine objects."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from .market import _aware, _decimal
from .paper import PaperOrder
from .trading import OrderIntent, Side


class TwoLegState(StrEnum):
    DETECTED = "DETECTED"
    VALIDATED = "VALIDATED"
    LEG_A_SUBMITTED = "LEG_A_SUBMITTED"
    LEG_A_FILLED = "LEG_A_FILLED"
    LEG_B_SUBMITTED = "LEG_B_SUBMITTED"
    COMPLETED = "COMPLETED"
    PARTIAL_FILL = "PARTIAL_FILL"
    HEDGE_REQUIRED = "HEDGE_REQUIRED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    ABORTED = "ABORTED"


@dataclass(frozen=True, slots=True)
class TwoLegPlan:
    buy_intent: OrderIntent
    sell_intent: OrderIntent
    max_unhedged_quantity: Decimal
    max_unhedged_duration: timedelta

    def __post_init__(self) -> None:
        if self.buy_intent.side is not Side.BUY or self.sell_intent.side is not Side.SELL:
            raise ValueError("two-leg plan requires BUY then SELL intents")
        if self.buy_intent.instrument.canonical_symbol != self.sell_intent.instrument.canonical_symbol:
            raise ValueError("two-leg instruments must share a canonical symbol")
        if self.buy_intent.venue_id == self.sell_intent.venue_id:
            raise ValueError("two-leg venues must differ")
        quantity = _decimal(self.max_unhedged_quantity, "max_unhedged_quantity")
        if quantity < 0 or self.max_unhedged_duration <= timedelta(0):
            raise ValueError("two-leg risk bounds are invalid")
        _aware(self.buy_intent.generated_at, "buy_intent.generated_at")
        object.__setattr__(self, "max_unhedged_quantity", quantity)


@dataclass(frozen=True, slots=True)
class TwoLegExecution:
    state: TwoLegState
    buy_order: PaperOrder | None
    sell_order: PaperOrder | None
    unhedged_quantity: Decimal
    transitions: tuple[TwoLegState, ...]
    reason: str | None = None
