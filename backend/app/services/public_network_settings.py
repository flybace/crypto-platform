"""Durable, redacted proxy settings shared by the public data processes."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import json
from pathlib import Path
from threading import RLock
from typing import Any, Mapping
from urllib.parse import urlsplit

from adapters.standalone.state_store import JsonStateError, JsonStateStore
from adapters.venues.api_routes import (
    API_ROUTE_FIELDS,
    default_api_route_config,
    normalize_api_route,
    normalize_api_routes,
)
from adapters.venues.httpx_transport import HttpxJsonTransport
from adapters.venues.proxy import normalize_public_proxy, redact_public_proxy
from adapters.venues.websockets_transport import WebSocketTransportError, WebsocketsJsonConnector
from backend.app.settings import Settings
from ports.rest import PublicRestError


CONTRACT_VERSION = "public-network-settings-v2"
LEGACY_CONTRACT_VERSIONS = frozenset({"public-network-settings-v1", CONTRACT_VERSION})
VENUE_IDS = ("binance", "okx", "bybit")
PROXY_FIELDS = ("http_proxy", "ws_proxy")
ENDPOINT_FIELDS = ("public_rest_base_url", "public_ws_base_url", "history_base_url")
DEFAULT_ENDPOINTS = {
    "binance": {
        "public_rest_base_url": "https://data-api.binance.vision",
        "public_ws_base_url": "wss://data-stream.binance.vision",
        "history_base_url": "https://data-api.binance.vision",
    },
    "okx": {
        "public_rest_base_url": "https://www.okx.com",
        "public_ws_base_url": "wss://ws.okx.com:8443/ws/v5/public",
        "history_base_url": "https://www.okx.com",
    },
    "bybit": {
        "public_rest_base_url": "https://api.bybit-tr.com",
        "public_ws_base_url": "wss://stream.bybit.kz/v5/public/spot",
        "history_base_url": "https://api.bybit-tr.com",
    },
}
PROBE_TARGETS = {
    "binance": "/api/v3/time",
    "okx": "/api/v5/public/time",
    "bybit": "/v5/market/time",
}
WS_PROBE_TARGETS = {
    "binance": None,
    "okx": {"op": "subscribe", "args": [{"channel": "books5", "instId": "BTC-USDT"}]},
    "bybit": {"op": "subscribe", "args": ["orderbook.50.BTCUSDT"]},
}


class PublicNetworkSettingsError(RuntimeError):
    """Raised when the persisted public-network settings cannot be trusted."""


def _default_endpoint_config() -> dict[str, dict[str, str]]:
    return deepcopy(DEFAULT_ENDPOINTS)


@dataclass(frozen=True, slots=True)
class PublicNetworkSnapshot:
    """Internal effective settings; callers must use ``public_view`` for APIs."""

    proxies: dict[str, dict[str, str]]
    source: str
    updated_at: str | None
    file_error: str | None = None
    endpoints: dict[str, dict[str, str]] = field(default_factory=_default_endpoint_config)
    api_routes: dict[str, dict[str, str]] = field(default_factory=default_api_route_config)

    @property
    def http_proxies(self) -> dict[str, str]:
        return {venue: values["http_proxy"] for venue, values in self.proxies.items()}

    @property
    def ws_proxies(self) -> dict[str, str]:
        return {venue: values["ws_proxy"] for venue, values in self.proxies.items()}

    @property
    def public_rest_base_urls(self) -> dict[str, str]:
        return {venue: values["public_rest_base_url"] for venue, values in self.endpoints.items()}

    @property
    def public_ws_base_urls(self) -> dict[str, str]:
        return {venue: values["public_ws_base_url"] for venue, values in self.endpoints.items()}

    @property
    def history_base_urls(self) -> dict[str, str]:
        return {venue: values["history_base_url"] for venue, values in self.endpoints.items()}

    def fingerprint(self) -> str:
        return json.dumps(
            {"endpoints": self.endpoints, "api_routes": self.api_routes, "proxies": self.proxies},
            sort_keys=True,
            separators=(",", ":"),
        )


class PublicNetworkSettingsStore:
    """Atomically persist public endpoints and proxy choices."""

    def __init__(
        self,
        path: str | Path,
        *,
        defaults: Mapping[str, Mapping[str, str | None]] | None = None,
        endpoint_defaults: Mapping[str, Mapping[str, str | None]] | None = None,
        api_route_defaults: Mapping[str, Mapping[str, str | None]] | None = None,
    ) -> None:
        self.path = Path(path)
        self._defaults = _normalize_config(defaults or {})
        self._endpoint_defaults = _normalize_endpoints(endpoint_defaults or DEFAULT_ENDPOINTS)
        self._api_route_defaults = normalize_api_routes(api_route_defaults or {})
        self._state = JsonStateStore(self.path)
        self._lock = RLock()

    def current(self) -> PublicNetworkSnapshot:
        with self._lock:
            defaults = deepcopy(self._defaults)
            endpoint_defaults = deepcopy(self._endpoint_defaults)
            api_route_defaults = deepcopy(self._api_route_defaults)
            if not self.path.exists():
                return PublicNetworkSnapshot(
                    defaults,
                    "environment",
                    None,
                    endpoints=endpoint_defaults,
                    api_routes=api_route_defaults,
                )
            try:
                payload = self._state.load({})
                if not isinstance(payload, Mapping):
                    raise PublicNetworkSettingsError("network settings file must contain an object")
                if payload.get("schema_version") not in LEGACY_CONTRACT_VERSIONS:
                    raise PublicNetworkSettingsError("network settings file has an unsupported schema")
                venues = payload.get("venues")
                if not isinstance(venues, Mapping):
                    raise PublicNetworkSettingsError("network settings file has no venue object")
                effective, effective_endpoints, effective_api_routes = _merge_config(
                    defaults,
                    endpoint_defaults,
                    api_route_defaults,
                    venues,
                )
                updated_at = str(payload.get("updated_at") or "").strip() or None
                return PublicNetworkSnapshot(
                    effective,
                    "saved",
                    updated_at,
                    endpoints=effective_endpoints,
                    api_routes=effective_api_routes,
                )
            except (JsonStateError, PublicNetworkSettingsError, TypeError, ValueError) as error:
                # A malformed persisted configuration must fail closed to defaults.
                return PublicNetworkSnapshot(
                    defaults,
                    "invalid_file",
                    None,
                    file_error="saved network settings are invalid; using direct access",
                    endpoints=endpoint_defaults,
                    api_routes=api_route_defaults,
                )

    def save(self, updates: Mapping[str, Mapping[str, object]]) -> PublicNetworkSnapshot:
        with self._lock:
            current = self.current()
            effective_proxies = deepcopy(current.proxies)
            effective_endpoints = deepcopy(current.endpoints)
            effective_api_routes = deepcopy(current.api_routes)
            for venue, values in updates.items():
                normalized_venue = str(venue).strip().lower()
                if normalized_venue not in VENUE_IDS:
                    raise PublicNetworkSettingsError(f"unsupported public network venue: {normalized_venue}")
                if not isinstance(values, Mapping):
                    raise PublicNetworkSettingsError(f"network settings for {normalized_venue} must be an object")
                for field in PROXY_FIELDS:
                    if bool(values.get(f"clear_{field}")):
                        effective_proxies[normalized_venue][field] = ""
                    elif field in values and values[field] is not None:
                        effective_proxies[normalized_venue][field] = normalize_public_proxy(str(values[field]))
                for field in ENDPOINT_FIELDS:
                    if bool(values.get(f"clear_{field}")):
                        effective_endpoints[normalized_venue][field] = self._endpoint_defaults[normalized_venue][field]
                    elif field in values and values[field] is not None:
                        normalized = normalize_public_endpoint(str(values[field]), field=field)
                        effective_endpoints[normalized_venue][field] = (
                            normalized or self._endpoint_defaults[normalized_venue][field]
                        )
                for field in API_ROUTE_FIELDS:
                    if bool(values.get(f"clear_{field}")):
                        effective_api_routes[normalized_venue][field] = self._api_route_defaults[normalized_venue][field]
                    elif field in values and values[field] is not None:
                        normalized = normalize_api_route(str(values[field]), field=field)
                        effective_api_routes[normalized_venue][field] = (
                            normalized or self._api_route_defaults[normalized_venue][field]
                        )

            now = datetime.now(UTC).isoformat()
            payload = {
                "schema_version": CONTRACT_VERSION,
                "updated_at": now,
                "venues": {
                    venue: {
                        **effective_endpoints[venue],
                        **effective_proxies[venue],
                        **effective_api_routes[venue],
                    }
                    for venue in VENUE_IDS
                },
            }
            try:
                self._state.save(payload)
                # Proxy credentials may be embedded in the URL. The atomic
                # temporary file starts private; keep that mode explicit
                # after replacement as well.
                self.path.chmod(0o600)
            except JsonStateError as error:
                raise PublicNetworkSettingsError("network settings cannot be saved") from error
            except OSError as error:
                raise PublicNetworkSettingsError("network settings permissions cannot be restricted") from error
            return PublicNetworkSnapshot(
                effective_proxies,
                "saved",
                now,
                endpoints=effective_endpoints,
                api_routes=effective_api_routes,
            )

    def public_view(self, snapshot: PublicNetworkSnapshot | None = None) -> dict[str, object]:
        current = snapshot or self.current()
        venues = []
        for venue in VENUE_IDS:
            values = current.proxies[venue]
            venues.append(
                {
                    "venue_id": venue,
                    **current.endpoints[venue],
                    **current.api_routes[venue],
                    "http_proxy": _proxy_view(values["http_proxy"]),
                    "ws_proxy": _proxy_view(values["ws_proxy"]),
                }
            )
        return {
            "schema_version": CONTRACT_VERSION,
            "source": current.source,
            "updated_at": current.updated_at,
            "file_error": current.file_error,
            "venues": venues,
            "apply_policy": {
                "backend": "immediate",
                "history_worker": "next_task",
                "scheduler": "next_cycle",
                "market_worker": "automatic_reconnect",
            },
            "supported_proxy_schemes": ["http", "https"],
            "supported_endpoint_schemes": {
                "public_rest_base_url": ["http", "https"],
                "public_ws_base_url": ["ws", "wss"],
                "history_base_url": ["http", "https"],
            },
            "supported_api_route_fields": list(API_ROUTE_FIELDS),
            "api_route_semantics": "relative REST paths; request parameters and response normalization remain venue adapter contracts",
        }


def resolve_public_network_settings_path(settings: Settings, project_root: str | Path) -> Path:
    configured = str(getattr(settings, "public_network_settings_path", "") or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else Path(project_root) / path
    history_path = Path(settings.history_data_path)
    history_root = history_path if history_path.is_absolute() else Path(project_root) / history_path
    return history_root / ".runtime" / "public-network-settings.json"


def proxy_defaults_from_settings(settings: Settings) -> dict[str, dict[str, str]]:
    return {
        "binance": {
            "http_proxy": settings.binance_public_http_proxy,
            "ws_proxy": settings.binance_public_ws_proxy,
        },
        "okx": {
            "http_proxy": settings.okx_public_http_proxy,
            "ws_proxy": settings.okx_public_ws_proxy,
        },
        "bybit": {
            "http_proxy": settings.bybit_public_http_proxy,
            "ws_proxy": settings.bybit_public_ws_proxy,
        },
    }


def endpoint_defaults_from_settings(settings: Settings) -> dict[str, dict[str, str]]:
    return {
        "binance": {
            "public_rest_base_url": settings.binance_public_rest_base_url,
            "public_ws_base_url": settings.binance_public_ws_base_url,
            "history_base_url": settings.binance_history_base_url,
        },
        "okx": {
            "public_rest_base_url": settings.okx_public_rest_base_url,
            "public_ws_base_url": settings.okx_public_ws_base_url,
            "history_base_url": settings.okx_history_base_url,
        },
        "bybit": {
            "public_rest_base_url": settings.bybit_public_rest_base_url,
            "public_ws_base_url": settings.bybit_public_ws_base_url,
            "history_base_url": settings.bybit_history_base_url,
        },
    }


def settings_with_public_network(settings: Settings, snapshot: PublicNetworkSnapshot) -> Settings:
    return replace(
        settings,
        binance_public_rest_base_url=snapshot.endpoints["binance"]["public_rest_base_url"],
        okx_public_rest_base_url=snapshot.endpoints["okx"]["public_rest_base_url"],
        bybit_public_rest_base_url=snapshot.endpoints["bybit"]["public_rest_base_url"],
        binance_public_ws_base_url=snapshot.endpoints["binance"]["public_ws_base_url"],
        okx_public_ws_base_url=snapshot.endpoints["okx"]["public_ws_base_url"],
        bybit_public_ws_base_url=snapshot.endpoints["bybit"]["public_ws_base_url"],
        binance_history_base_url=snapshot.endpoints["binance"]["history_base_url"],
        okx_history_base_url=snapshot.endpoints["okx"]["history_base_url"],
        bybit_history_base_url=snapshot.endpoints["bybit"]["history_base_url"],
        binance_public_http_proxy=snapshot.proxies["binance"]["http_proxy"],
        binance_public_ws_proxy=snapshot.proxies["binance"]["ws_proxy"],
        okx_public_http_proxy=snapshot.proxies["okx"]["http_proxy"],
        okx_public_ws_proxy=snapshot.proxies["okx"]["ws_proxy"],
        bybit_public_http_proxy=snapshot.proxies["bybit"]["http_proxy"],
        bybit_public_ws_proxy=snapshot.proxies["bybit"]["ws_proxy"],
    )


def probe_public_http(
    venue_id: str,
    settings: Settings,
    *,
    network_snapshot: PublicNetworkSnapshot | None = None,
) -> dict[str, object]:
    venue = str(venue_id).strip().lower()
    if venue not in PROBE_TARGETS:
        raise ValueError("venue_id must be one of binance, okx, bybit")
    effective_snapshot = network_snapshot or PublicNetworkSnapshot(
        proxy_defaults_from_settings(settings),
        "environment",
        None,
        endpoints=endpoint_defaults_from_settings(settings),
    )
    path = effective_snapshot.api_routes[venue]["time_path"] or PROBE_TARGETS[venue]
    endpoints = effective_snapshot.endpoints
    base_url = endpoints[venue]["public_rest_base_url"]
    # The persisted snapshot is authoritative over environment defaults. This
    # keeps the UI probe on the same route the workers will use.
    proxy = effective_snapshot.proxies[venue]["http_proxy"] or None
    started = datetime.now(UTC)
    transport = HttpxJsonTransport(
        base_url,
        timeout_seconds=min(max(float(settings.history_timeout_seconds), 1.0), 15.0),
        trust_env=False,
        proxy=proxy,
    )
    try:
        response = transport.get_json_value_with_metadata(path, {})
        elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        return {
            "venue_id": venue,
            "channel": "HTTP",
            "state": "REACHABLE",
            "http_status": response.status_code,
            "latency_ms": max(0, elapsed_ms),
            "used_proxy": bool(proxy),
            "endpoint": base_url + path,
            "error": None,
        }
    except PublicRestError as error:
        elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        return {
            "venue_id": venue,
            "channel": "HTTP",
            "state": "BLOCKED" if error.kind in {"NETWORK_ERROR", "UPSTREAM_UNAVAILABLE"} else "REJECTED",
            "http_status": error.status_code,
            "latency_ms": max(0, elapsed_ms),
            "used_proxy": bool(proxy),
            "endpoint": base_url + path,
            "error": {"kind": error.kind, "message": str(error)},
        }
    finally:
        transport.close()


def probe_public_websocket(
    venue_id: str,
    settings: Settings,
    *,
    network_snapshot: PublicNetworkSnapshot | None = None,
) -> dict[str, object]:
    """Open one public market stream and require a market payload.

    A successful TCP/TLS handshake alone is insufficient: proxies can permit
    CONNECT while blocking the WebSocket upgrade or upstream subscription.
    The probe therefore sends the same public subscription shape used by the
    market worker for venues that require one.
    """
    venue = str(venue_id).strip().lower()
    if venue not in WS_PROBE_TARGETS:
        raise ValueError("venue_id must be one of binance, okx, bybit")
    subscription = WS_PROBE_TARGETS[venue]
    effective_snapshot = network_snapshot or PublicNetworkSnapshot(
        proxy_defaults_from_settings(settings),
        "environment",
        None,
        endpoints=endpoint_defaults_from_settings(settings),
    )
    endpoints = effective_snapshot.endpoints
    endpoint = endpoints[venue]["public_ws_base_url"]
    if venue == "binance":
        endpoint = f"{endpoint.rstrip('/')}/ws/btcusdt@depth@100ms"
    proxy = effective_snapshot.proxies[venue]["ws_proxy"] or None
    timeout_seconds = min(max(float(settings.history_timeout_seconds), 1.0), 15.0)
    started = datetime.now(UTC)
    try:
        asyncio.run(_receive_public_websocket_probe(endpoint, subscription, proxy, timeout_seconds))
    except ValueError as error:
        return _websocket_probe_result(
            venue,
            endpoint,
            proxy,
            started,
            state="REJECTED",
            error_kind="UPSTREAM_REJECTED",
            error_message=str(error),
        )
    except (TimeoutError, asyncio.TimeoutError, WebSocketTransportError, OSError):
        return _websocket_probe_result(
            venue,
            endpoint,
            proxy,
            started,
            state="BLOCKED",
            error_kind="NETWORK_ERROR",
            error_message="public WebSocket probe failed",
        )
    except Exception:
        return _websocket_probe_result(
            venue,
            endpoint,
            proxy,
            started,
            state="BLOCKED",
            error_kind="NETWORK_ERROR",
            error_message="public WebSocket probe failed",
        )
    return _websocket_probe_result(venue, endpoint, proxy, started, state="REACHABLE")


async def _receive_public_websocket_probe(
    endpoint: str,
    subscription: Mapping[str, object] | None,
    proxy: str | None,
    timeout_seconds: float,
) -> None:
    connector = WebsocketsJsonConnector(
        open_timeout_seconds=timeout_seconds,
        ping_interval_seconds=timeout_seconds,
        ping_timeout_seconds=timeout_seconds,
        close_timeout_seconds=min(max(timeout_seconds / 4, 0.5), 2.0),
        proxy=proxy,
    )
    async with connector.connect(endpoint) as connection:
        # Keep the probe deadline around opening and receiving data. Closing a
        # successful WebSocket has its own bounded timeout in the connector.
        async with asyncio.timeout(timeout_seconds):
            if subscription is not None:
                await connection.send_json(subscription)
            for _ in range(4):
                payload = await connection.recv_json()
                if payload.get("event") == "error" or payload.get("success") is False:
                    raise ValueError("public WebSocket subscription was rejected")
                if payload.get("code") not in (None, 0, "0"):
                    raise ValueError("public WebSocket returned an upstream error")
                if _is_market_websocket_payload(payload):
                    return
    raise ValueError("public WebSocket returned no market payload")


def _is_market_websocket_payload(payload: Mapping[str, object]) -> bool:
    return any(key in payload for key in ("data", "stream", "topic", "e"))


def _websocket_probe_result(
    venue: str,
    endpoint: str,
    proxy: str | None,
    started: datetime,
    *,
    state: str,
    error_kind: str | None = None,
    error_message: str | None = None,
) -> dict[str, object]:
    elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
    error = None if error_kind is None else {"kind": error_kind, "message": error_message or error_kind}
    return {
        "venue_id": venue,
        "channel": "WebSocket",
        "state": state,
        "http_status": None,
        "latency_ms": max(0, elapsed_ms),
        "used_proxy": bool(proxy),
        "endpoint": endpoint,
        "error": error,
    }


def proxy_defaults_from_env(getenv) -> dict[str, dict[str, str]]:
    """Read worker-safe defaults without requiring backend authentication settings."""
    return {
        venue: {
            "http_proxy": str(getenv(f"CRYPTO_{venue.upper()}_PUBLIC_HTTP_PROXY", "") or "").strip(),
            "ws_proxy": str(getenv(f"CRYPTO_{venue.upper()}_PUBLIC_WS_PROXY", "") or "").strip(),
        }
        for venue in VENUE_IDS
    }


def endpoint_defaults_from_env(getenv) -> dict[str, dict[str, str]]:
    def value(name: str, default: str, *aliases: str) -> str:
        raw = str(getenv(name, "") or "").strip()
        if raw:
            return raw
        for alias in aliases:
            raw = str(getenv(alias, "") or "").strip()
            if raw:
                return raw
        return default

    return {
        "binance": {
            "public_rest_base_url": value(
                "CRYPTO_BINANCE_PUBLIC_REST_BASE_URL",
                DEFAULT_ENDPOINTS["binance"]["public_rest_base_url"],
            ),
            "public_ws_base_url": value(
                "CRYPTO_BINANCE_PUBLIC_WS_BASE_URL",
                DEFAULT_ENDPOINTS["binance"]["public_ws_base_url"],
            ),
            "history_base_url": value(
                "CRYPTO_BINANCE_HISTORY_BASE_URL",
                DEFAULT_ENDPOINTS["binance"]["history_base_url"],
            ),
        },
        "okx": {
            "public_rest_base_url": value(
                "CRYPTO_OKX_PUBLIC_REST_BASE_URL",
                DEFAULT_ENDPOINTS["okx"]["public_rest_base_url"],
            ),
            "public_ws_base_url": value(
                "CRYPTO_OKX_PUBLIC_WS_BASE_URL",
                DEFAULT_ENDPOINTS["okx"]["public_ws_base_url"],
            ),
            "history_base_url": value(
                "CRYPTO_OKX_HISTORY_BASE_URL",
                DEFAULT_ENDPOINTS["okx"]["history_base_url"],
            ),
        },
        "bybit": {
            "public_rest_base_url": value(
                "CRYPTO_BYBIT_PUBLIC_REST_BASE_URL",
                DEFAULT_ENDPOINTS["bybit"]["public_rest_base_url"],
                "CRYPTO_BYBIT_PUBLIC_BASE_URL",
            ),
            "public_ws_base_url": value(
                "CRYPTO_BYBIT_PUBLIC_WS_BASE_URL",
                DEFAULT_ENDPOINTS["bybit"]["public_ws_base_url"],
                "CRYPTO_BYBIT_MARKET_WS_URL",
            ),
            "history_base_url": value(
                "CRYPTO_BYBIT_HISTORY_BASE_URL",
                DEFAULT_ENDPOINTS["bybit"]["history_base_url"],
            ),
        },
    }


# Keep the descriptive name available to the market worker and older imports.
public_proxy_defaults_from_env = proxy_defaults_from_env


def _normalize_config(values: Mapping[str, Mapping[str, str | None]]) -> dict[str, dict[str, str]]:
    result = {venue: {field: "" for field in PROXY_FIELDS} for venue in VENUE_IDS}
    for venue, raw_values in values.items():
        normalized_venue = str(venue).strip().lower()
        if normalized_venue not in VENUE_IDS or not isinstance(raw_values, Mapping):
            continue
        for field in PROXY_FIELDS:
            result[normalized_venue][field] = normalize_public_proxy(raw_values.get(field))
    return result


def _normalize_endpoints(values: Mapping[str, Mapping[str, str | None]]) -> dict[str, dict[str, str]]:
    result = _default_endpoint_config()
    for venue, raw_values in values.items():
        normalized_venue = str(venue).strip().lower()
        if normalized_venue not in VENUE_IDS or not isinstance(raw_values, Mapping):
            continue
        for field in ENDPOINT_FIELDS:
            if field in raw_values and raw_values[field] is not None:
                normalized = normalize_public_endpoint(str(raw_values[field]), field=field)
                if normalized:
                    result[normalized_venue][field] = normalized
    return result


def _merge_config(
    defaults: Mapping[str, Mapping[str, str]],
    endpoint_defaults: Mapping[str, Mapping[str, str]],
    api_route_defaults: Mapping[str, Mapping[str, str]],
    saved: Mapping[str, Any],
) -> tuple[
    dict[str, dict[str, str]],
    dict[str, dict[str, str]],
    dict[str, dict[str, str]],
]:
    result = deepcopy(defaults)
    endpoint_result = deepcopy(endpoint_defaults)
    api_route_result = deepcopy(api_route_defaults)
    for venue in VENUE_IDS:
        raw_values = saved.get(venue)
        if raw_values is None:
            continue
        if not isinstance(raw_values, Mapping):
            raise PublicNetworkSettingsError(f"network settings for {venue} must be an object")
        for field in PROXY_FIELDS:
            if field in raw_values:
                result[venue][field] = normalize_public_proxy(raw_values[field])
        for field in ENDPOINT_FIELDS:
            if field in raw_values:
                normalized = normalize_public_endpoint(str(raw_values[field]), field=field)
                if normalized:
                    endpoint_result[venue][field] = normalized
        for field in API_ROUTE_FIELDS:
            if field in raw_values:
                normalized = normalize_api_route(str(raw_values[field]), field=field)
                if normalized:
                    api_route_result[venue][field] = normalized
    return result, endpoint_result, api_route_result


def normalize_public_endpoint(value: str | None, *, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    schemes = {
        "public_rest_base_url": frozenset({"http", "https"}),
        "public_ws_base_url": frozenset({"ws", "wss"}),
        "history_base_url": frozenset({"http", "https"}),
    }.get(field)
    if schemes is None:
        raise ValueError(f"unsupported public endpoint field: {field}")
    if any(character.isspace() for character in raw):
        raise ValueError(f"{field} must be a URL without whitespace")
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as error:
        raise ValueError(f"{field} must be a valid URL") from error
    if parsed.scheme.lower() not in schemes or not parsed.netloc or not hostname:
        allowed = "/".join(sorted(schemes))
        raise ValueError(f"{field} must use a {allowed} URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{field} must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{field} must not contain a query or fragment")
    return raw.rstrip("/")


def _proxy_view(value: str) -> dict[str, object]:
    return {
        "configured": bool(value),
        "display": redact_public_proxy(value),
        "mode": "proxy" if value else "direct",
    }
