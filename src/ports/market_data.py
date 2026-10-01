"""Public market-data boundary."""

from typing import Protocol

from domain.market import Instrument, OrderBookSnapshot
from domain.market_status import MarketStatus


class MarketDataGateway(Protocol):
    def fetch_snapshot(self, instrument: Instrument) -> OrderBookSnapshot:
        """Return the latest normalized snapshot for an instrument."""

    def status(self, venue_id: str) -> MarketStatus:
        """Return the connector status for a venue."""
