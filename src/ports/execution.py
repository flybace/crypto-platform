"""Execution boundary; venue SDKs must remain behind this protocol."""

from dataclasses import dataclass
from typing import Protocol

from domain.trading import OrderIntent, OrderStatus


@dataclass(frozen=True, slots=True)
class OrderReceipt:
    request_id: str
    client_order_id: str
    venue_order_id: str
    status: OrderStatus


class ExecutionGateway(Protocol):
    def submit(self, intent: OrderIntent) -> OrderReceipt:
        """Submit one already-approved intent and return the venue result."""

