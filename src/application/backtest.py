"""Small deterministic paper-backed runner for fixed intent replays."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from domain.market import OrderBookSnapshot
from domain.paper import PaperOrderStatus
from domain.trading import OrderIntent

from .paper import PaperBroker


@dataclass(frozen=True, slots=True)
class BacktestResult:
    run_id: str
    dataset_id: str
    submitted_orders: int
    filled_orders: int
    filled_quantity: Decimal
    fees_quote: Decimal
    final_balances: dict[str, Decimal]


class PaperBacktestRunner:
    def __init__(self, broker: PaperBroker) -> None:
        self._broker = broker

    def run(
        self,
        *,
        run_id: str,
        dataset_id: str,
        intents: Iterable[OrderIntent],
        markets: dict[str, OrderBookSnapshot],
    ) -> BacktestResult:
        submitted = 0
        filled_orders = 0
        filled_quantity = Decimal("0")
        fees_quote = Decimal("0")
        for intent in intents:
            market = markets[intent.instrument.key]
            order = self._broker.submit(intent, market, market.received_timestamp)
            submitted += 1
            if order.status in {PaperOrderStatus.FILLED, PaperOrderStatus.PARTIALLY_FILLED}:
                filled_orders += 1
            filled_quantity += order.filled_quantity
            fees_quote += sum((fill.fee_amount for fill in order.fills), Decimal("0"))
        return BacktestResult(
            run_id=str(run_id),
            dataset_id=str(dataset_id),
            submitted_orders=submitted,
            filled_orders=filled_orders,
            filled_quantity=filled_quantity,
            fees_quote=fees_quote,
            final_balances=self._broker.account.snapshot(),
        )
