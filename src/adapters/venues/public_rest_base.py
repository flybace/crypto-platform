"""Shared status and error handling for public REST gateways."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, NoReturn

from domain.market import OrderBookSnapshot
from domain.market_status import MarketConnectionState, MarketStatus
from ports.rest import JsonRestTransport, PublicRestError

from .api_routes import route_config_for


class PublicRestGatewayBase:
    def __init__(
        self,
        venue_id: str,
        transport: JsonRestTransport,
        clock: Callable[[], datetime] | None = None,
        *,
        api_routes: Mapping[str, str | None] | None = None,
    ) -> None:
        self._venue_id = str(venue_id).strip().lower()
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(UTC))
        self._api_routes = route_config_for(self._venue_id, api_routes)
        self._status = MarketStatus(venue_id=self._venue_id, state=MarketConnectionState.DISCONNECTED, reason="NOT_CONNECTED")

    def status(self, venue_id: str) -> MarketStatus:
        if str(venue_id).strip().lower() != self._venue_id:
            raise ValueError(f"gateway belongs to {self._venue_id}")
        return self._status

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return an aware datetime")
        return value

    def _get_json(self, path: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        try:
            return self._transport.get_json(path, params)
        except PublicRestError as error:
            self._mark_failure(error.kind)
            raise

    def _route(self, field: str) -> str:
        return self._api_routes[field]

    def update_api_routes(self, api_routes: Mapping[str, str | None]) -> None:
        """Apply public REST path changes without rebuilding the gateway."""
        self._api_routes = route_config_for(self._venue_id, api_routes)

    def _complete(self, snapshot: OrderBookSnapshot) -> OrderBookSnapshot:
        self._status = MarketStatus(
            venue_id=self._venue_id,
            state=MarketConnectionState.CONNECTED,
            last_received_at=snapshot.received_timestamp,
            last_sequence=snapshot.sequence,
        )
        return snapshot

    def _invalid_payload(self, error: Exception) -> NoReturn:
        self._mark_failure("INVALID_PAYLOAD")
        raise error

    def _mark_failure(self, reason: str) -> None:
        current = self._status
        state = MarketConnectionState.DISCONNECTED if reason == "NETWORK_ERROR" else MarketConnectionState.DEGRADED
        self._status = MarketStatus(
            venue_id=self._venue_id,
            state=state,
            last_received_at=current.last_received_at,
            last_sequence=current.last_sequence,
            reason=reason,
        )
