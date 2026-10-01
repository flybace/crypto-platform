"""OKX Spot public REST gateway."""

from collections.abc import Callable, Mapping
from datetime import datetime

from domain.market import Instrument, MarketType, OrderBookSnapshot
from ports.rest import JsonRestTransport

from .okx_spot import OkxSpotPublicAdapter
from .public_rest_base import PublicRestGatewayBase


class OkxSpotPublicRestGateway(PublicRestGatewayBase):
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
        super().__init__("okx", transport, clock, api_routes=api_routes)
        self._adapter = OkxSpotPublicAdapter()
        self._depth_limit = depth_limit

    def fetch_instrument(self, native_symbol: str) -> Instrument:
        inst_id = str(native_symbol).strip().upper()
        if not inst_id:
            raise ValueError("native_symbol must not be empty")
        payload = self._get_json(
            self._route("instruments_path"),
            {"instType": "SPOT", "instId": inst_id},
        )
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            self._invalid_payload(ValueError("OKX instruments response has no data"))
        try:
            return self._adapter.instrument_from_metadata(data[0])
        except (KeyError, TypeError, ValueError) as error:
            self._invalid_payload(error)

    def fetch_snapshot(self, instrument: Instrument) -> OrderBookSnapshot:
        self._validate_instrument(instrument)
        received_at = self._now()
        payload = self._get_json(
            self._route("order_book_path"),
            {"instId": instrument.native_symbol, "sz": str(self._depth_limit)},
        )
        try:
            if payload.get("code") not in (None, "0", 0):
                raise ValueError("OKX public REST response returned an error code")
            snapshot = self._adapter.normalize_order_book(payload, instrument, received_at)
        except (KeyError, TypeError, ValueError) as error:
            self._invalid_payload(error)
        return self._complete(snapshot)

    @staticmethod
    def _validate_instrument(instrument: Instrument) -> None:
        if instrument.venue_id != "okx" or instrument.market_type is not MarketType.SPOT:
            raise ValueError("OKX Spot gateway received an unsupported instrument")
