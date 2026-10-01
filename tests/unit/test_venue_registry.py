import pytest

from domain.market import MarketType
from domain.venue import Venue, VenueRegistry


def test_registry_normalizes_ids_and_returns_stable_order() -> None:
    okx = Venue(
        venue_id="OKX",
        display_name="OKX Spot",
        market_types=frozenset({MarketType.SPOT}),
        public_rest_base_url="https://www.okx.com/api/v5",
        public_ws_base_url="wss://ws.okx.com:8443/ws/v5/public",
    )
    binance = Venue(
        venue_id="Binance",
        display_name="Binance Spot",
        market_types=frozenset({MarketType.SPOT}),
        public_rest_base_url="https://api.binance.com",
        public_ws_base_url="wss://stream.binance.com:9443",
    )
    registry = VenueRegistry((okx, binance))

    assert registry.get("BINANCE") == binance
    assert [venue.venue_id for venue in registry.all()] == ["binance", "okx"]


def test_registry_rejects_duplicate_venue_ids() -> None:
    venue = Venue(
        venue_id="binance",
        display_name="Binance Spot",
        market_types=frozenset({MarketType.SPOT}),
        public_rest_base_url="https://api.binance.com",
        public_ws_base_url="wss://stream.binance.com:9443",
    )
    registry = VenueRegistry((venue,))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(venue)
