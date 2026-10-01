"""Public spot-instrument discovery with a bounded, durable cache."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock
from typing import Any

from adapters.standalone.state_store import JsonStateError, JsonStateStore
from adapters.venues.api_routes import normalize_api_routes
from adapters.venues.httpx_transport import HttpxJsonTransport
from ports.rest import JsonRestTransport, PublicRestError


VENUE_LABELS = {"binance": "Binance", "okx": "OKX", "bybit": "Bybit"}
VENUES = frozenset(VENUE_LABELS)
DEFAULT_CACHE_TTL_SECONDS = 900
DEFAULT_MAX_PAGES = 20


class InstrumentCatalogService:
    """Read public spot listings and expose normalized order constraints.

    The cache is only a usability fallback. A cached listing is marked as
    such in the response and never implies that the market is currently
    tradable or that an order can pass the execution risk gates.
    """

    def __init__(
        self,
        *,
        base_urls: Mapping[str, str] | None = None,
        proxies: Mapping[str, str | None] | None = None,
        timeout_seconds: float = 10.0,
        trust_env: bool = False,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        max_pages: int = DEFAULT_MAX_PAGES,
        state_path: str | Path | None = None,
        transports: Mapping[str, JsonRestTransport] | None = None,
        api_routes: Mapping[str, Mapping[str, str | None]] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if cache_ttl_seconds <= 0 or max_pages <= 0:
            raise ValueError("cache_ttl_seconds and max_pages must be positive")
        self._cache_ttl_seconds = int(cache_ttl_seconds)
        self._max_pages = int(max_pages)
        self._lock = RLock()
        self._state = JsonStateStore(state_path) if state_path else None
        configured = {str(key).strip().lower(): value for key, value in (transports or {}).items()}
        configured_proxies = {str(key).strip().lower(): value for key, value in (proxies or {}).items()}
        self._api_routes = normalize_api_routes(api_routes or {})
        self._transports: dict[str, JsonRestTransport] = dict(configured)
        for venue_id, base_url in (base_urls or {}).items():
            normalized = str(venue_id).strip().lower()
            if normalized in VENUES and normalized not in self._transports:
                self._transports[normalized] = HttpxJsonTransport(
                    str(base_url),
                    timeout_seconds=timeout_seconds,
                    trust_env=trust_env,
                    proxy=configured_proxies.get(normalized),
                )
        self._cache: dict[str, dict[str, object]] = {}
        self._load()

    def list(
        self,
        venue_id: str,
        *,
        quote_asset: str = "USDT",
        search: str = "",
        limit: int = 80,
        refresh: bool = False,
    ) -> dict[str, object]:
        venue = self._venue(venue_id)
        quote = self._asset(quote_asset, "quote_asset")
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        needle = str(search).strip().upper()
        with self._lock:
            cached = self._cache.get(venue)
            if cached is not None and not refresh and self._is_fresh(cached.get("fetched_at")):
                return self._response(venue, cached, quote, needle, limit, status="CACHED")

        try:
            items = self._fetch(venue)
        except PublicRestError as error:
            with self._lock:
                cached = self._cache.get(venue)
                if cached is not None:
                    result = self._response(venue, cached, quote, needle, limit, status="CACHED")
                    result["last_error"] = self._error_dict(error)
                    return result
            return self._blocked_response(venue, quote, needle, limit, error)
        except (TypeError, ValueError, KeyError) as error:
            public_error = PublicRestError("INVALID_PAYLOAD", "public instrument response was invalid")
            with self._lock:
                cached = self._cache.get(venue)
                if cached is not None:
                    result = self._response(venue, cached, quote, needle, limit, status="CACHED")
                    result["last_error"] = self._error_dict(public_error)
                    return result
            return self._blocked_response(venue, quote, needle, limit, public_error)

        fetched_at = datetime.now(UTC).isoformat()
        entry = {"fetched_at": fetched_at, "items": items}
        with self._lock:
            self._cache[venue] = entry
            self._persist_locked()
            return self._response(venue, entry, quote, needle, limit, status="LIVE")

    def close(self) -> None:
        closed: set[int] = set()
        for transport in self._transports.values():
            identity = id(transport)
            if identity in closed:
                continue
            closed.add(identity)
            close = getattr(transport, "close", None)
            if callable(close):
                close()

    def update_proxies(self, proxies: Mapping[str, str | None]) -> None:
        """Apply new public REST proxies and force a fresh catalog fetch."""
        with self._lock:
            for venue, transport in self._transports.items():
                update = getattr(transport, "set_proxy", None)
                if callable(update):
                    update(proxies.get(venue))
            self._cache.clear()

    def update_endpoints(self, base_urls: Mapping[str, str]) -> None:
        """Apply new public REST endpoints and force a fresh catalog fetch."""
        with self._lock:
            for venue, transport in self._transports.items():
                if venue not in base_urls:
                    continue
                update = getattr(transport, "set_base_url", None)
                if callable(update):
                    update(base_urls[venue])
            self._cache.clear()

    def update_network(
        self,
        base_urls: Mapping[str, str],
        proxies: Mapping[str, str | None],
        api_routes: Mapping[str, Mapping[str, str | None]] | None = None,
    ) -> None:
        """Apply endpoint and proxy changes and invalidate the catalog cache."""
        self.update_endpoints(base_urls)
        self.update_proxies(proxies)
        if api_routes is not None:
            self.update_api_routes(api_routes)

    def update_api_routes(self, api_routes: Mapping[str, Mapping[str, str | None]]) -> None:
        """Apply public REST path changes and invalidate the catalog cache."""
        normalized = normalize_api_routes(api_routes)
        with self._lock:
            self._api_routes = normalized
            self._cache.clear()

    def _fetch(self, venue: str) -> list[dict[str, object]]:
        transport = self._transports.get(venue)
        if transport is None:
            raise PublicRestError("NOT_CONFIGURED", "public instrument source is not configured")
        path = self._api_routes[venue]["instruments_path"]
        if venue == "binance":
            return parse_binance_instruments(transport.get_json(path, {}))
        if venue == "bybit":
            return self._fetch_bybit(transport, path)
        return parse_okx_instruments(transport.get_json(path, {"instType": "SPOT"}))

    def _fetch_bybit(self, transport: JsonRestTransport, path: str) -> list[dict[str, object]]:
        cursor: str | None = None
        items: list[dict[str, object]] = []
        for _ in range(self._max_pages):
            params = {"category": "spot", "limit": "1000"}
            if cursor:
                params["cursor"] = cursor
            payload = transport.get_json(path, params)
            if not isinstance(payload, Mapping) or int(payload.get("retCode", -1)) != 0:
                raise PublicRestError("UPSTREAM_REJECTED", "Bybit instrument response returned an error")
            result = payload.get("result")
            if not isinstance(result, Mapping):
                raise PublicRestError("INVALID_PAYLOAD", "Bybit instrument response has no result")
            page = parse_bybit_instruments({"result": result})
            items.extend(page)
            next_cursor = str(result.get("nextPageCursor") or "").strip()
            if not next_cursor or next_cursor == cursor or not page:
                break
            cursor = next_cursor
        return _unique_items(items)

    def _response(
        self,
        venue: str,
        cache: Mapping[str, object],
        quote: str,
        search: str,
        limit: int,
        *,
        status: str,
    ) -> dict[str, object]:
        all_items = [item for item in cache.get("items", []) if isinstance(item, dict)]
        filtered = [
            item
            for item in all_items
            if str(item.get("quote_asset", "")).upper() == quote
            and (not search or search in str(item.get("native_symbol", "")).upper() or search in str(item.get("base_asset", "")).upper())
        ]
        selected = deepcopy(filtered[:limit])
        return {
            "venue_id": venue,
            "venue_name": VENUE_LABELS[venue],
            "status": status,
            "source": f"{venue}-public-instruments",
            "quote_asset": quote,
            "fetched_at": cache.get("fetched_at"),
            "cache_ttl_seconds": self._cache_ttl_seconds,
            "total_count": len(filtered),
            "returned_count": len(selected),
            "truncated": len(filtered) > limit,
            "items": selected,
            "last_error": None,
        }

    def _blocked_response(self, venue: str, quote: str, search: str, limit: int, error: PublicRestError) -> dict[str, object]:
        return {
            "venue_id": venue,
            "venue_name": VENUE_LABELS[venue],
            "status": "BLOCKED",
            "source": f"{venue}-public-instruments",
            "quote_asset": quote,
            "fetched_at": None,
            "cache_ttl_seconds": self._cache_ttl_seconds,
            "total_count": 0,
            "returned_count": 0,
            "truncated": False,
            "items": [],
            "last_error": self._error_dict(error),
        }

    def _load(self) -> None:
        if self._state is None:
            return
        try:
            payload = self._state.load({"version": 1, "venues": {}})
        except JsonStateError as error:
            raise RuntimeError("instrument catalog state is unreadable") from error
        venues = payload.get("venues", {}) if isinstance(payload, dict) else {}
        if not isinstance(venues, dict):
            raise RuntimeError("instrument catalog state must contain an object")
        for venue, value in venues.items():
            if venue in VENUES and isinstance(value, dict) and isinstance(value.get("items"), list):
                self._cache[venue] = deepcopy(value)

    def _persist_locked(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save({"version": 1, "venues": self._cache})
        except JsonStateError as error:
            raise RuntimeError("instrument catalog state cannot be saved") from error

    def _is_fresh(self, fetched_at: object) -> bool:
        try:
            parsed = datetime.fromisoformat(str(fetched_at))
        except (TypeError, ValueError):
            return False
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return False
        return (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() < self._cache_ttl_seconds

    @staticmethod
    def _venue(value: str) -> str:
        venue = str(value).strip().lower()
        if venue not in VENUES:
            raise ValueError("venue_id must be one of binance, okx, bybit")
        return venue

    @staticmethod
    def _asset(value: str, field: str) -> str:
        asset = str(value).strip().upper()
        if not asset or not asset.isalnum() or len(asset) > 20:
            raise ValueError(f"{field} must be an asset code")
        return asset

    @staticmethod
    def _error_dict(error: PublicRestError) -> dict[str, object]:
        return {
            "kind": error.kind,
            "status_code": error.status_code,
            "retry_after_seconds": error.retry_after_seconds,
        }


def parse_binance_instruments(payload: Mapping[str, Any]) -> list[dict[str, object]]:
    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        raise ValueError("Binance instrument response has no symbols")
    result = []
    for raw in symbols:
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("status", "")).upper() != "TRADING" or raw.get("isSpotTradingAllowed") is False:
            continue
        base = str(raw.get("baseAsset", "")).strip().upper()
        quote = str(raw.get("quoteAsset", "")).strip().upper()
        native = str(raw.get("symbol", "")).strip().upper()
        if not base or not quote or not native or base == quote:
            continue
        raw_filters = raw.get("filters", [])
        if not isinstance(raw_filters, list):
            raw_filters = []
        filters = {
            str(item.get("filterType")): item
            for item in raw_filters
            if isinstance(item, Mapping)
        }
        result.append(_item(
            venue="binance",
            base=base,
            quote=quote,
            native=native,
            status="TRADING",
            price_tick=_value(filters.get("PRICE_FILTER"), "tickSize", "0.00000001"),
            quantity_step=_value(filters.get("LOT_SIZE"), "stepSize", "0.00000001"),
            min_quantity=_value(filters.get("LOT_SIZE"), "minQty", "0.00000001"),
            min_notional=_value(filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL"), "minNotional", "0"),
            extra={"base_asset_precision": raw.get("baseAssetPrecision"), "quote_asset_precision": raw.get("quoteAssetPrecision")},
        ))
    return _unique_items(result)


def parse_bybit_instruments(payload: Mapping[str, Any]) -> list[dict[str, object]]:
    result = payload.get("result")
    rows = result.get("list") if isinstance(result, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("Bybit instrument response has no list")
    parsed = []
    for raw in rows:
        if not isinstance(raw, Mapping) or str(raw.get("status", "")).lower() != "trading":
            continue
        base = str(raw.get("baseCoin", "")).strip().upper()
        quote = str(raw.get("quoteCoin", "")).strip().upper()
        native = str(raw.get("symbol", "")).strip().upper()
        lot = raw.get("lotSizeFilter") if isinstance(raw.get("lotSizeFilter"), Mapping) else {}
        price = raw.get("priceFilter") if isinstance(raw.get("priceFilter"), Mapping) else {}
        if not base or not quote or not native or base == quote:
            continue
        parsed.append(_item(
            venue="bybit",
            base=base,
            quote=quote,
            native=native,
            status="TRADING",
            price_tick=_value(price, "tickSize", "0.00000001"),
            quantity_step=_value(lot, "qtyStep", "0.00000001"),
            min_quantity=_value(lot, "minOrderQty", "0.00000001"),
            min_notional=_value(lot, "minOrderAmt", "0"),
            extra={"base_precision": lot.get("basePrecision"), "quote_precision": lot.get("quotePrecision")},
        ))
    return _unique_items(parsed)


def parse_okx_instruments(payload: Mapping[str, Any]) -> list[dict[str, object]]:
    if str(payload.get("code", "0")) != "0":
        raise ValueError("OKX instrument response returned an error")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("OKX instrument response has no data")
    parsed = []
    for raw in rows:
        if (
            not isinstance(raw, Mapping)
            or str(raw.get("instType", "")).upper() != "SPOT"
            or str(raw.get("state", "")).lower() != "live"
        ):
            continue
        base = str(raw.get("baseCcy", "")).strip().upper()
        quote = str(raw.get("quoteCcy", "")).strip().upper()
        native = str(raw.get("instId", "")).strip().upper()
        if not base or not quote or not native or base == quote:
            continue
        parsed.append(_item(
            venue="okx",
            base=base,
            quote=quote,
            native=native,
            status="TRADING",
            price_tick=_value(raw, "tickSz", "0.00000001"),
            quantity_step=_value(raw, "lotSz", "0.00000001"),
            min_quantity=_value(raw, "minSz", "0.00000001"),
            min_notional="0",
            extra={},
        ))
    return _unique_items(parsed)


def _item(*, venue: str, base: str, quote: str, native: str, status: str, price_tick: str, quantity_step: str, min_quantity: str, min_notional: str, extra: Mapping[str, object]) -> dict[str, object]:
    return {
        "instrument_key": f"{venue}:spot:{base}/{quote}",
        "venue_id": venue,
        "market_type": "spot",
        "base_asset": base,
        "quote_asset": quote,
        "native_symbol": native,
        "canonical_symbol": f"{base}/{quote}",
        "status": status,
        "price_tick": price_tick,
        "quantity_step": quantity_step,
        "min_quantity": min_quantity,
        "min_notional": min_notional,
        **dict(extra),
    }


def _value(source: Mapping[str, Any] | None, key: str, default: str) -> str:
    raw = source.get(key) if isinstance(source, Mapping) else None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return default
    return str(value) if value.is_finite() and value > 0 else default


def _unique_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    unique = {str(item["instrument_key"]): item for item in items if item.get("instrument_key")}
    return [deepcopy(unique[key]) for key in sorted(unique)]
