"""Binance Spot public REST gateway."""

from collections.abc import Callable, Mapping
from datetime import datetime

from domain.market import Instrument, MarketType, OrderBookSnapshot
from ports.rest import JsonRestTransport

from .binance_spot import BinanceSpotPublicAdapter
from .public_rest_base import PublicRestGatewayBase


class BinanceSpotPublicRestGateway(PublicRestGatewayBase):
    def __init__(
        self,
        transport: JsonRestTransport,
        *,
        depth_limit: int = 100,
        clock: Callable[[], datetime] | None = None,
        api_routes: Mapping[str, str | None] | None = None,
    ) -> None:
        if depth_limit <= 0:
            raise ValueError("depth_limit must be positive")
        super().__init__("binance", transport, clock, api_routes=api_routes)
        self._adapter = BinanceSpotPublicAdapter()
        self._depth_limit = depth_limit

    def fetch_instrument(self, native_symbol: str) -> Instrument:
        symbol = str(native_symbol).strip().upper()
        if not symbol:
            raise ValueError("native_symbol must not be empty")
        payload = self._get_json(self._route("instruments_path"), {"symbol": symbol})
        symbols = payload.get("symbols")
        if not isinstance(symbols, list) or not symbols:
            self._invalid_payload(ValueError("Binance exchangeInfo response has no symbols"))
        try:
            return self._adapter.instrument_from_exchange_info(symbols[0])
        except (KeyError, TypeError, ValueError) as error:
            self._invalid_payload(error)

    def fetch_snapshot(self, instrument: Instrument) -> OrderBookSnapshot:
        self._validate_instrument(instrument)
        received_at = self._now()
        payload = self._get_json(
            self._route("order_book_path"),
            {"symbol": instrument.native_symbol, "limit": str(self._depth_limit)},
        )
        try:
            snapshot = self._adapter.normalize_order_book(payload, instrument, received_at)
        except (KeyError, TypeError, ValueError) as error:
            self._invalid_payload(error)
        return self._complete(snapshot)

    @staticmethod
    def _validate_instrument(instrument: Instrument) -> None:
        if instrument.venue_id != "binance" or instrument.market_type is not MarketType.SPOT:
            raise ValueError("Binance Spot gateway received an unsupported instrument")
