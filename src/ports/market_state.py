"""Durable boundary for the latest public market state."""

from typing import Protocol

from domain.market import OrderBookSnapshot
from domain.market_status import MarketStatus


class MarketStateStoreError(RuntimeError):
    """The shared market-state representation could not be read or written."""


class MarketStateStore(Protocol):
    def write_snapshot(self, snapshot: OrderBookSnapshot) -> None:
        """Persist one accepted, normalized order-book snapshot."""

    def write_status(self, status: MarketStatus) -> None:
        """Persist the latest connector status for a venue."""

    def read_status(self, venue_id: str) -> MarketStatus | None:
        """Return the latest persisted venue status, if one exists."""

    def read_snapshot(self, instrument_key: str) -> OrderBookSnapshot | None:
        """Return the latest persisted snapshot for one canonical instrument."""
