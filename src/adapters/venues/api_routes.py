"""Configurable public REST paths shared by venue adapters."""

from __future__ import annotations

from copy import deepcopy
from string import Formatter
from urllib.parse import urlsplit
from typing import Mapping


API_ROUTE_FIELDS = (
    "instruments_path",
    "tickers_path",
    "order_book_path",
    "candles_path",
    "time_path",
)

DEFAULT_API_ROUTES = {
    "binance": {
        "instruments_path": "/api/v3/exchangeInfo",
        "tickers_path": "/api/v3/ticker/24hr",
        "order_book_path": "/api/v3/depth",
        "candles_path": "/api/v3/klines",
        "time_path": "/api/v3/time",
    },
    "okx": {
        "instruments_path": "/api/v5/public/instruments",
        "tickers_path": "/api/v5/market/tickers",
        "order_book_path": "/api/v5/market/books",
        "candles_path": "/api/v5/market/history-candles",
        "time_path": "/api/v5/public/time",
    },
    "bybit": {
        "instruments_path": "/v5/market/instruments-info",
        "tickers_path": "/v5/market/tickers",
        "order_book_path": "/v5/market/orderbook",
        "candles_path": "/v5/market/kline",
        "time_path": "/v5/market/time",
    },
}


def default_api_route_config() -> dict[str, dict[str, str]]:
    return deepcopy(DEFAULT_API_ROUTES)


def normalize_api_route(value: str | None, *, field: str) -> str:
    """Validate one relative REST path without allowing query credentials."""
    if field not in API_ROUTE_FIELDS:
        raise ValueError(f"unsupported public API route field: {field}")
    raw = str(value or "").strip()
    if not raw:
        return ""
    if any(character.isspace() for character in raw):
        raise ValueError(f"{field} must be a path without whitespace")
    try:
        parsed = urlsplit(raw)
    except ValueError as error:
        raise ValueError(f"{field} must be a valid path") from error
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not raw.startswith("/"):
        raise ValueError(f"{field} must be an absolute path without query or fragment")
    try:
        fields = {
            name
            for _, name, _, _ in Formatter().parse(raw)
            if name
        }
    except ValueError as error:
        raise ValueError(f"{field} has an invalid template") from error
    if fields:
        raise ValueError(f"{field} must not contain template placeholders")
    return raw.rstrip("/") or "/"


def normalize_api_routes(values: Mapping[str, Mapping[str, str | None]]) -> dict[str, dict[str, str]]:
    """Normalize a possibly partial route map against the built-in defaults."""
    result = default_api_route_config()
    for venue, raw_values in values.items():
        venue_id = str(venue).strip().lower()
        if venue_id not in result or not isinstance(raw_values, Mapping):
            continue
        for field in API_ROUTE_FIELDS:
            if field in raw_values and raw_values[field] is not None:
                normalized = normalize_api_route(str(raw_values[field]), field=field)
                if normalized:
                    result[venue_id][field] = normalized
    return result


def route_config_for(venue_id: str, values: Mapping[str, str | None] | None = None) -> dict[str, str]:
    """Return one venue route map with defaults for omitted fields."""
    venue = str(venue_id).strip().lower()
    if venue not in DEFAULT_API_ROUTES:
        raise ValueError(f"unsupported public API route venue: {venue}")
    result = deepcopy(DEFAULT_API_ROUTES[venue])
    for field in API_ROUTE_FIELDS:
        if values is not None and field in values and values[field] is not None:
            normalized = normalize_api_route(str(values[field]), field=field)
            if normalized:
                result[field] = normalized
    return result
