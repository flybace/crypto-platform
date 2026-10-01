"""Public 24-hour ticker aggregation for the read-only market workspace."""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from threading import RLock
from typing import Any

from adapters.venues.api_routes import normalize_api_routes
from adapters.venues.httpx_transport import HttpxJsonTransport
from ports.rest import PublicRestError


VENUE_LABELS = {"binance": "Binance", "okx": "OKX", "bybit": "Bybit"}
VENUE_IDS = tuple(VENUE_LABELS)
DEFAULT_TICKER_CACHE_TTL_SECONDS = 5


class PublicTickerService:
    """Fetch and normalize public 24-hour spot tickers without private auth."""

    def __init__(
        self,
        *,
        base_urls: Mapping[str, str],
        proxies: Mapping[str, str | None],
        api_routes: Mapping[str, Mapping[str, str | None]] | None = None,
        timeout_seconds: float = 10.0,
        trust_env: bool = False,
        cache_ttl_seconds: int = DEFAULT_TICKER_CACHE_TTL_SECONDS,
        transports: Mapping[str, Any] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if cache_ttl_seconds <= 0:
            raise ValueError("cache_ttl_seconds must be positive")
        self._timeout_seconds = float(timeout_seconds)
        self._trust_env = bool(trust_env)
        self._cache_ttl_seconds = int(cache_ttl_seconds)
        self._lock = RLock()
        self._api_routes = normalize_api_routes(api_routes or {})
        self._transports: dict[str, Any] = {
            str(key).strip().lower(): value for key, value in (transports or {}).items()
        }
        for venue_id, base_url in base_urls.items():
            venue = str(venue_id).strip().lower()
            if venue in VENUE_IDS and venue not in self._transports:
                self._transports[venue] = HttpxJsonTransport(
                    str(base_url),
                    timeout_seconds=self._timeout_seconds,
                    trust_env=self._trust_env,
                    proxy=proxies.get(venue),
                )
        self._cache: dict[str, dict[str, object]] = {}

    def snapshot(
        self,
        *,
        quote_asset: str = "USDT",
        search: str = "",
        limit: int = 80,
        offset: int = 0,
        sort_by: str = "volume",
        refresh: bool = False,
    ) -> dict[str, object]:
        quote = _normalize_asset(quote_asset, "quote_asset")
        needle = str(search).strip().upper()
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0 or offset > 10_000:
            raise ValueError("offset must be between 0 and 10000")
        if sort_by not in {"volume", "change", "spread", "symbol"}:
            raise ValueError("sort_by must be volume, change, spread, or symbol")

        # Keep each public venue independent: one slow or blocked exchange must
        # not delay the prices that are available from the other venues.
        with ThreadPoolExecutor(max_workers=len(VENUE_IDS), thread_name_prefix="public-ticker") as pool:
            futures = {
                venue: pool.submit(self.list_venue, venue, quote_asset=quote, refresh=refresh)
                for venue in VENUE_IDS
            }
            venue_results = [futures[venue].result() for venue in VENUE_IDS]
        groups: dict[str, dict[str, object]] = {}
        for result in venue_results:
            venue = str(result["venue_id"])
            for item in result["items"]:
                if not isinstance(item, Mapping):
                    continue
                symbol = str(item["symbol"])
                if needle and needle not in symbol and needle not in str(item["base_asset"]):
                    continue
                group = groups.setdefault(
                    symbol,
                    {
                        "symbol": symbol,
                        "base_asset": item["base_asset"],
                        "quote_asset": item["quote_asset"],
                        "markets": {venue_id: None for venue_id in VENUE_IDS},
                    },
                )
                markets = group["markets"]
                if isinstance(markets, dict):
                    markets[venue] = item

        items = [_merge_ticker_group(group) for group in groups.values()]
        items.sort(key=lambda item: _sort_key(item, sort_by))
        page = items[offset : offset + limit]
        timestamps = [
            str(item["timestamp"])
            for item in items
            if isinstance(item.get("timestamp"), str) and item["timestamp"]
        ]
        as_of = max(timestamps, default=None)
        return {
            "kind": "public-market-tickers-v1",
            "quote_asset": quote,
            "search": needle,
            "as_of": as_of,
            "refresh_seconds": self._cache_ttl_seconds,
            "research_only": True,
            "total_count": len(items),
            "returned_count": len(page),
            "offset": offset,
            "limit": limit,
            "truncated": offset + len(page) < len(items),
            "sort_by": sort_by,
            "venues": [
                {
                    "venue_id": result["venue_id"],
                    "venue_name": result["venue_name"],
                    "status": result["status"],
                    "fetched_at": result["fetched_at"],
                    "total_count": result["total_count"],
                    "last_error": result["last_error"],
                }
                for result in venue_results
            ],
            "items": page,
        }

    def list_venue(
        self,
        venue_id: str,
        *,
        quote_asset: str = "USDT",
        refresh: bool = False,
    ) -> dict[str, object]:
        venue = _normalize_venue(venue_id)
        quote = _normalize_asset(quote_asset, "quote_asset")
        with self._lock:
            cached = self._cache.get(venue)
            if cached is not None and not refresh and self._is_fresh(cached.get("fetched_at")):
                return _venue_response(venue, cached, quote, status="CACHED")

        try:
            items = self._fetch(venue)
        except PublicRestError as error:
            with self._lock:
                cached = self._cache.get(venue)
                if cached is not None:
                    result = _venue_response(venue, cached, quote, status="CACHED")
                    result["last_error"] = _error_dict(error)
                    return result
            return {
                "venue_id": venue,
                "venue_name": VENUE_LABELS[venue],
                "status": "BLOCKED",
                "fetched_at": None,
                "total_count": 0,
                "items": [],
                "last_error": _error_dict(error),
            }

        fetched_at = datetime.now(UTC).isoformat()
        entry = {"fetched_at": fetched_at, "items": items}
        with self._lock:
            self._cache[venue] = entry
            return _venue_response(venue, entry, quote, status="LIVE")

    def update_network(
        self,
        base_urls: Mapping[str, str],
        proxies: Mapping[str, str | None],
        api_routes: Mapping[str, Mapping[str, str | None]],
    ) -> None:
        with self._lock:
            for venue, transport in self._transports.items():
                if venue in base_urls:
                    update_endpoint = getattr(transport, "set_base_url", None)
                    if callable(update_endpoint):
                        update_endpoint(base_urls[venue])
                update_proxy = getattr(transport, "set_proxy", None)
                if callable(update_proxy):
                    update_proxy(proxies.get(venue))
            self._api_routes = normalize_api_routes(api_routes)
            self._cache.clear()

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

    def _fetch(self, venue: str) -> list[dict[str, object]]:
        transport = self._transports.get(venue)
        if transport is None:
            raise PublicRestError("NOT_CONFIGURED", "public ticker source is not configured")
        path = self._api_routes[venue]["tickers_path"]
        get_value = getattr(transport, "get_json_value", None)
        if callable(get_value):
            payload = get_value(path, _ticker_params(venue))
        else:
            payload = transport.get_json(path, _ticker_params(venue))
        return parse_public_tickers(venue, payload)

    def _is_fresh(self, fetched_at: object) -> bool:
        try:
            parsed = datetime.fromisoformat(str(fetched_at))
        except (TypeError, ValueError):
            return False
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return False
        return (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() < self._cache_ttl_seconds


def parse_public_tickers(venue_id: str, payload: object) -> list[dict[str, object]]:
    venue = _normalize_venue(venue_id)
    if venue == "binance":
        if not isinstance(payload, list):
            raise PublicRestError("INVALID_PAYLOAD", "Binance ticker response was not a list")
        return _parse_binance(payload)
    if not isinstance(payload, Mapping):
        raise PublicRestError("INVALID_PAYLOAD", f"{venue} ticker response was not an object")
    if venue == "okx":
        if str(payload.get("code", "0")) != "0" or not isinstance(payload.get("data"), list):
            raise PublicRestError("UPSTREAM_REJECTED", "OKX ticker response returned an error")
        return _parse_okx(payload["data"])
    if int(payload.get("retCode", -1)) != 0:
        raise PublicRestError("UPSTREAM_REJECTED", "Bybit ticker response returned an error")
    result = payload.get("result")
    if not isinstance(result, Mapping) or not isinstance(result.get("list"), list):
        raise PublicRestError("INVALID_PAYLOAD", "Bybit ticker response has no list")
    return _parse_bybit(result["list"])


def _parse_binance(rows: list[object]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        native = str(raw.get("symbol", "")).strip().upper()
        for quote in ("USDT", "USDC", "BTC", "ETH"):
            if native.endswith(quote) and len(native) > len(quote):
                result.append(
                    _ticker_item(
                        venue="binance",
                        native=native,
                        base=native[: -len(quote)],
                        quote=quote,
                        last=raw.get("lastPrice"),
                        opening=raw.get("openPrice"),
                        high=raw.get("highPrice"),
                        low=raw.get("lowPrice"),
                        change=raw.get("priceChangePercent"),
                        volume=raw.get("volume"),
                        quote_volume=raw.get("quoteVolume"),
                        timestamp=raw.get("closeTime"),
                    )
                )
                break
    return _valid_tickers(result)


def _parse_okx(rows: list[object]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        native = str(raw.get("instId", "")).strip().upper()
        if "-" not in native:
            continue
        base, quote = native.split("-", 1)
        result.append(
            _ticker_item(
                venue="okx",
                native=native,
                base=base,
                quote=quote,
                last=raw.get("last"),
                opening=raw.get("open24h"),
                high=raw.get("high24h"),
                low=raw.get("low24h"),
                change=None,
                volume=raw.get("vol24h"),
                quote_volume=raw.get("volCcy24h"),
                timestamp=raw.get("ts"),
            )
        )
    return _valid_tickers(result)


def _parse_bybit(rows: list[object]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        native = str(raw.get("symbol", "")).strip().upper()
        quote = ""
        base = ""
        for candidate in ("USDT", "USDC", "BTC", "ETH"):
            if native.endswith(candidate) and len(native) > len(candidate):
                base, quote = native[: -len(candidate)], candidate
                break
        if not base:
            continue
        fraction = raw.get("price24hPcnt")
        change = None if fraction in (None, "") else _multiply_text(fraction, "100")
        result.append(
            _ticker_item(
                venue="bybit",
                native=native,
                base=base,
                quote=quote,
                last=raw.get("lastPrice"),
                opening=raw.get("prevPrice24h"),
                high=raw.get("highPrice24h"),
                low=raw.get("lowPrice24h"),
                change=change,
                volume=raw.get("volume24h"),
                quote_volume=raw.get("turnover24h"),
                timestamp=raw.get("time"),
            )
        )
    return _valid_tickers(result)


def _ticker_item(
    *,
    venue: str,
    native: str,
    base: str,
    quote: str,
    last: object,
    opening: object,
    high: object,
    low: object,
    change: object,
    volume: object,
    quote_volume: object,
    timestamp: object,
) -> dict[str, object]:
    last_value = _decimal(last)
    opening_value = _decimal(opening)
    high_value = _decimal_or_zero(high)
    low_value = _decimal_or_zero(low)
    change_value = _decimal(change) if change not in (None, "") else None
    if change_value is None and opening_value is not None and opening_value > 0 and last_value is not None:
        change_value = (last_value / opening_value - Decimal("1")) * Decimal("100")
    range_position = Decimal("0")
    if high_value > low_value and last_value is not None:
        range_position = max(Decimal("0"), min(Decimal("100"), (last_value - low_value) / (high_value - low_value) * Decimal("100")))
    change_value = change_value or Decimal("0")
    return {
        "venue_id": venue,
        "venue_name": VENUE_LABELS[venue],
        "symbol": f"{base}/{quote}",
        "native_symbol": native,
        "base_asset": base,
        "quote_asset": quote,
        "last_price": _decimal_text(last_value),
        "open_24h": _decimal_text(opening_value),
        "high_24h": _decimal_text(high_value),
        "low_24h": _decimal_text(low_value),
        "change_pct": _decimal_text(change_value),
        "volume_24h": _decimal_text(_decimal_or_zero(volume)),
        "quote_volume_24h": _decimal_text(_decimal_or_zero(quote_volume)),
        "range_position_pct": _decimal_text(range_position),
        "trend": "up" if change_value > 0 else "down" if change_value < 0 else "flat",
        "timestamp": _timestamp(timestamp),
    }


def _valid_tickers(items: list[dict[str, object]]) -> list[dict[str, object]]:
    return [item for item in items if item["last_price"] not in {None, "0"}]


def _merge_ticker_group(group: Mapping[str, object]) -> dict[str, object]:
    markets = group["markets"]
    assert isinstance(markets, Mapping)
    available = [item for item in markets.values() if isinstance(item, Mapping)]
    prices = [(str(item["venue_id"]), Decimal(str(item["last_price"]))) for item in available]
    changes = [Decimal(str(item["change_pct"])) for item in available]
    volumes = [Decimal(str(item["quote_volume_24h"])) for item in available]
    best = min(prices, key=lambda item: item[1]) if prices else (None, None)
    worst = max(prices, key=lambda item: item[1]) if prices else (None, None)
    spread = (worst[1] / best[1] - Decimal("1")) * Decimal("100") if best[1] and best[1] > 0 else Decimal("0")
    average_change = sum(changes, Decimal("0")) / Decimal(len(changes)) if changes else Decimal("0")
    spread = spread.quantize(Decimal("0.00000001"))
    average_change = average_change.quantize(Decimal("0.00000001"))
    timestamps = [
        str(item["timestamp"])
        for item in available
        if isinstance(item.get("timestamp"), str) and item["timestamp"]
    ]
    return {
        "symbol": group["symbol"],
        "base_asset": group["base_asset"],
        "quote_asset": group["quote_asset"],
        "markets": deepcopy(dict(markets)),
        "available_venues": [str(item["venue_id"]) for item in available],
        "best_venue_id": best[0],
        "best_price": _decimal_text(best[1]),
        "highest_venue_id": worst[0],
        "highest_price": _decimal_text(worst[1]),
        "spread_pct": _decimal_text(spread),
        "change_pct": _decimal_text(average_change),
        "quote_volume_24h": _decimal_text(max(volumes, default=Decimal("0"))),
        "trend": "up" if average_change > 0 else "down" if average_change < 0 else "flat",
        "timestamp": max(timestamps, default=None),
    }


def _sort_key(item: Mapping[str, object], sort_by: str) -> tuple[object, ...]:
    if sort_by == "symbol":
        return (str(item["symbol"]),)
    if sort_by == "change":
        return (-Decimal(str(item["change_pct"])), str(item["symbol"]))
    if sort_by == "spread":
        return (-Decimal(str(item["spread_pct"])), str(item["symbol"]))
    return (-Decimal(str(item["quote_volume_24h"])), str(item["symbol"]))


def _venue_response(venue: str, entry: Mapping[str, object], quote: str, *, status: str) -> dict[str, object]:
    items = [
        item
        for item in entry.get("items", [])
        if isinstance(item, Mapping) and str(item.get("quote_asset", "")).upper() == quote
    ]
    return {
        "venue_id": venue,
        "venue_name": VENUE_LABELS[venue],
        "status": status,
        "fetched_at": entry.get("fetched_at"),
        "total_count": len(items),
        "items": deepcopy(items),
        "last_error": None,
    }


def _ticker_params(venue: str) -> dict[str, str]:
    if venue == "okx":
        return {"instType": "SPOT"}
    if venue == "bybit":
        return {"category": "spot"}
    return {}


def _normalize_venue(value: str) -> str:
    venue = str(value).strip().lower()
    if venue not in VENUE_IDS:
        raise ValueError("venue_id must be one of binance, okx, bybit")
    return venue


def _normalize_asset(value: str, field: str) -> str:
    asset = str(value).strip().upper()
    if not asset or not asset.isalnum() or len(asset) > 20:
        raise ValueError(f"{field} must be an asset code")
    return asset


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _decimal_or_zero(value: object) -> Decimal:
    parsed = _decimal(value)
    return parsed if parsed is not None and parsed >= 0 else Decimal("0")


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _multiply_text(value: object, multiplier: str) -> str | None:
    parsed = _decimal(value)
    return _decimal_text(parsed * Decimal(multiplier)) if parsed is not None else None


def _timestamp(value: object) -> str | None:
    parsed = _decimal(value)
    if parsed is None or parsed <= 0:
        return None
    try:
        return datetime.fromtimestamp(float(parsed) / 1000, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _error_dict(error: PublicRestError) -> dict[str, object]:
    return {
        "kind": error.kind,
        "status_code": error.status_code,
        "retry_after_seconds": error.retry_after_seconds,
    }
