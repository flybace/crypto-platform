"""Shared validation and transport handling for public candle gateways."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from domain.candle import Candle, HistoryQuery
from ports.rest import JsonValueRestTransport, PublicJsonResponse, PublicRestError

from .api_routes import route_config_for


class HistoryGatewayBase:
    def __init__(
        self,
        venue_id: str,
        transport: JsonValueRestTransport,
        *,
        page_limit: int,
        max_pages: int,
        pause_seconds: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
        api_routes: Mapping[str, str | None] | None = None,
    ) -> None:
        if page_limit <= 0 or max_pages <= 0:
            raise ValueError("page_limit and max_pages must be positive")
        if pause_seconds < 0:
            raise ValueError("pause_seconds must not be negative")
        self.venue_id = str(venue_id).strip().lower()
        self.source = f"{self.venue_id}-public-history"
        self._transport = transport
        self._page_limit = page_limit
        self._max_pages = max_pages
        self._pause_seconds = pause_seconds
        self._sleep = sleep
        self._api_routes = route_config_for(self.venue_id, api_routes)
        self._raw_responses: list[PublicJsonResponse] = []

    def _validate(self, query: HistoryQuery) -> None:
        if query.venue_id != self.venue_id:
            raise ValueError(f"history gateway belongs to {self.venue_id}")
        if query.market_type.value != "spot":
            raise ValueError("public history currently supports spot markets only")

    def _get_value(self, path: str, params: dict[str, str]):
        response = self._get_response(path, params)
        self._raw_responses.append(response)
        return response.payload

    def _route(self, field: str) -> str:
        return self._api_routes[field]

    def reset_raw_responses(self) -> None:
        """Start a fresh capture for one logical historical download."""
        self._raw_responses.clear()

    def drain_raw_responses(self) -> tuple[PublicJsonResponse, ...]:
        """Return and clear successful pages captured during the last request."""
        responses = tuple(self._raw_responses)
        self._raw_responses.clear()
        return responses

    def _get_response(self, path: str, params: dict[str, str]) -> PublicJsonResponse:
        with_metadata = getattr(self._transport, "get_json_value_with_metadata", None)
        if callable(with_metadata):
            response = with_metadata(path, params)
            if isinstance(response, PublicJsonResponse):
                return response
            return PublicJsonResponse(path=path, params=params, payload=response)
        return PublicJsonResponse(
            path=path,
            params=params,
            payload=self._transport.get_json_value(path, params),
            received_at=datetime.now(UTC),
        )

    def _pause(self) -> None:
        if self._pause_seconds:
            self._sleep(self._pause_seconds)

    @staticmethod
    def _unique_sorted(candles: list[Candle]) -> tuple[Candle, ...]:
        unique = {candle.open_time_ms: candle for candle in candles}
        return tuple(unique[key] for key in sorted(unique))

    def _pagination_limit_reached(self, cursor: int, end_ms: int, page_count: int) -> None:
        if cursor < end_ms and page_count >= self._max_pages:
            raise PublicRestError("PAGINATION_LIMIT", "historical request exceeded the page limit")

    @staticmethod
    def _upstream_error(message: str) -> PublicRestError:
        return PublicRestError("UPSTREAM_REJECTED", message)

    def close(self) -> None:
        close = getattr(self._transport, "close", None)
        if callable(close):
            close()

    def update_proxy(self, proxy: str | None) -> None:
        """Apply a saved proxy when the underlying transport supports reloads."""
        update = getattr(self._transport, "set_proxy", None)
        if callable(update):
            update(proxy)

    def update_base_url(self, base_url: str) -> None:
        """Apply a saved history endpoint when the transport supports reloads."""
        update = getattr(self._transport, "set_base_url", None)
        if callable(update):
            update(base_url)

    def update_api_routes(self, api_routes: Mapping[str, str | None]) -> None:
        """Apply public history path changes without rebuilding the gateway."""
        self._api_routes = route_config_for(self.venue_id, api_routes)
