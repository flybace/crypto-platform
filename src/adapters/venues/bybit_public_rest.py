"""Bybit V5 Spot public REST gateway."""

from collections.abc import Callable, Mapping
from datetime import datetime

from domain.market import Instrument, MarketType, OrderBookSnapshot
from ports.rest import JsonRestTransport

from .bybit_spot import BybitSpotPublicAdapter
from .public_rest_base import PublicRestGatewayBase


class BybitSpotPublicRestGateway(PublicRestGatewayBase):
    def __init__(
        self,
        transport: JsonRestTransport,
        *,
        depth_limit: int = 50,
        clock: Callable[[], datetime] | None = None,
        api_routes: Mapping[str, str | None] | None = None,
    ) -> None:
        if int(depth_limit) not in BybitSpotPublicAdapter.SUPPORTED_DEPTHS:
            raise ValueError("Bybit Spot depth_limit must be 1, 50, or 200")
        super().__init__("bybit", transport, clock, api_routes=api_routes)
        self._adapter = BybitSpotPublicAdapter()
        self._depth_limit = int(depth_limit)

    def fetch_instrument(self, native_symbol: str) -> Instrument:
        symbol = str(native_symbol).strip().upper()
        if not symbol:
            raise ValueError("native_symbol must not be empty")
        payload = self._get_json(
            self._route("instruments_path"),
            {"category": "spot", "symbol": symbol},
        )
        try:
            self._validate_response(payload)
            result = payload.get("result")
            items = result.get("list") if isinstance(result, dict) else None
            if not isinstance(items, list) or not items:
                raise ValueError("Bybit instruments response has no list")
            return self._adapter.instrument_from_metadata(items[0])
        except (KeyError, TypeError, ValueError) as error:
            self._invalid_payload(error)

    def fetch_snapshot(self, instrument: Instrument) -> OrderBookSnapshot:
        self._validate_instrument(instrument)
        received_at = self._now()
        payload = self._get_json(
            self._route("order_book_path"),
            {
                "category": "spot",
                "symbol": instrument.native_symbol,
                "limit": str(self._depth_limit),
            },
        )
        try:
            self._validate_response(payload)
            snapshot = self._adapter.normalize_order_book(payload, instrument, received_at)
        except (KeyError, TypeError, ValueError) as error:
            self._invalid_payload(error)
        return self._complete(snapshot)

    @staticmethod
    def _validate_response(payload: dict[str, object]) -> None:
        if payload.get("retCode") not in (None, 0, "0"):
            raise ValueError("Bybit public REST response returned an error code")

    @staticmethod
    def _validate_instrument(instrument: Instrument) -> None:
        if instrument.venue_id != "bybit" or instrument.market_type is not MarketType.SPOT:
            raise ValueError("Bybit Spot gateway received an unsupported instrument")
