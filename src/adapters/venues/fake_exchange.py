"""Deterministic gateway used to test the execution boundary."""

from domain.trading import OrderIntent, OrderStatus, Side
from ports.execution import OrderReceipt


class FakeExchangeGateway:
    """Record approved submissions and reject unsupported directions."""

    def __init__(self) -> None:
        self.submissions: list[OrderIntent] = []

    def submit(self, intent: OrderIntent) -> OrderReceipt:
        if intent.side is not Side.SELL:
            raise ValueError("FakeExchangeGateway accepts SELL intents only")
        self.submissions.append(intent)
        sequence = len(self.submissions)
        return OrderReceipt(
            request_id=intent.request_id,
            client_order_id=intent.request_id,
            venue_order_id=f"fake-{sequence:06d}",
            status=OrderStatus.ACCEPTED,
        )
