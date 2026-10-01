"""Paper-only orchestration for a buy-then-sell cross-market plan."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from domain.market import OrderBookSnapshot
from domain.paper import PaperOrderStatus
from domain.two_leg import TwoLegExecution, TwoLegPlan, TwoLegState

from .paper import PaperBroker


class TwoLegPaperExecutor:
    def __init__(self, buy_broker: PaperBroker, sell_broker: PaperBroker) -> None:
        self._buy_broker = buy_broker
        self._sell_broker = sell_broker

    def execute(
        self,
        plan: TwoLegPlan,
        buy_market: OrderBookSnapshot,
        sell_market: OrderBookSnapshot,
        now: datetime,
        fail_sell: bool = False,
    ) -> TwoLegExecution:
        transitions = [TwoLegState.DETECTED, TwoLegState.VALIDATED, TwoLegState.LEG_A_SUBMITTED]
        buy_order = self._buy_broker.submit(plan.buy_intent, buy_market, now)
        if buy_order.status is PaperOrderStatus.REJECTED or buy_order.filled_quantity <= 0:
            transitions.append(TwoLegState.ABORTED)
            return TwoLegExecution(TwoLegState.ABORTED, buy_order, None, Decimal("0"), tuple(transitions), buy_order.reason)
        if buy_order.remaining_quantity > 0:
            transitions.append(TwoLegState.PARTIAL_FILL)
        else:
            transitions.extend((TwoLegState.LEG_A_FILLED, TwoLegState.LEG_B_SUBMITTED))

        sell_intent = replace(
            plan.sell_intent,
            request_id=f"{plan.sell_intent.request_id}-{buy_order.order_id}",
            quantity=buy_order.filled_quantity,
        )
        if fail_sell:
            transitions.append(TwoLegState.HEDGE_REQUIRED)
            return TwoLegExecution(
                TwoLegState.HEDGE_REQUIRED,
                buy_order,
                None,
                buy_order.filled_quantity,
                tuple(transitions),
                "SELL_LEG_FAILED",
            )

        sell_order = self._sell_broker.submit(sell_intent, sell_market, now)
        if sell_order.status is PaperOrderStatus.FILLED:
            transitions.append(TwoLegState.COMPLETED)
            return TwoLegExecution(TwoLegState.COMPLETED, buy_order, sell_order, Decimal("0"), tuple(transitions))
        unhedged = buy_order.filled_quantity - sell_order.filled_quantity
        transitions.append(TwoLegState.RECONCILIATION_REQUIRED)
        reason = sell_order.reason or "SELL_LEG_PARTIAL"
        return TwoLegExecution(TwoLegState.RECONCILIATION_REQUIRED, buy_order, sell_order, unhedged, tuple(transitions), reason)
