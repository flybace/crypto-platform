"""Binance signed private REST client (read-only endpoints only).

Security boundaries enforced by this module:
- Only read-only endpoints are implemented (account balances, open orders).
- NO order placement, cancellation, transfer, or withdrawal endpoints exist here.
- The API secret is used only for HMAC-SHA256 signing and is never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

import httpx

from ports.rest import PublicRestError


def sign_query_string(query_string: str, api_secret: str) -> str:
    """Return the hex HMAC-SHA256 signature for a Binance query string."""
    secret = str(api_secret or "")
    if not secret:
        raise ValueError("api_secret must not be empty")
    return hmac.new(secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()


class BinanceSignedRestClient:
    """Minimal signed REST client for Binance private read-only endpoints."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        *,
        timeout_seconds: float = 10.0,
        trust_env: bool = True,
        recv_window_ms: int = 5000,
    ) -> None:
        key = str(api_key or "").strip()
        if not key:
            raise ValueError("api_key must not be empty")
        if not str(api_secret or ""):
            raise ValueError("api_secret must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._base_url = str(base_url or "").rstrip("/")
        if not self._base_url:
            raise ValueError("base_url must not be empty")
        self._api_key = key
        self._api_secret = str(api_secret)
        self._recv_window_ms = int(recv_window_ms)
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=float(timeout_seconds),
            trust_env=bool(trust_env),
            headers={"X-MBX-APIKEY": key},
        )

    def _signed_get(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        query: dict[str, str] = {str(k): str(v) for k, v in (params or {}).items()}
        query["timestamp"] = str(int(time.time() * 1000))
        query["recvWindow"] = str(self._recv_window_ms)
        query_string = urlencode(query)
        query["signature"] = sign_query_string(query_string, self._api_secret)
        try:
            response = self._client.get(path, params=query)
        except httpx.HTTPError as error:
            raise PublicRestError("NETWORK_ERROR", "binance signed request failed") from error
        if response.status_code == 401:
            raise PublicRestError("UNAUTHORIZED", "binance API key rejected", status_code=401)
        if response.status_code == 403:
            raise PublicRestError("FORBIDDEN", "binance API key lacks permission", status_code=403)
        if response.status_code == 429:
            raise PublicRestError("RATE_LIMITED", "binance rate limit reached", status_code=429)
        if 400 <= response.status_code < 500:
            raise PublicRestError(
                "UPSTREAM_REJECTED", "binance rejected the request", status_code=response.status_code
            )
        if response.status_code >= 500:
            raise PublicRestError(
                "UPSTREAM_UNAVAILABLE", "binance unavailable", status_code=response.status_code
            )
        try:
            return response.json()
        except ValueError as error:
            raise PublicRestError("INVALID_JSON", "binance response was not JSON") from error

    def get_account(self) -> Mapping[str, Any]:
        """GET /api/v3/account — balances. Requires signed request."""
        payload = self._signed_get("/api/v3/account")
        if not isinstance(payload, Mapping):
            raise PublicRestError("INVALID_JSON", "binance account response must be an object")
        return payload

    def get_open_orders(self, symbol: str | None = None) -> list[Mapping[str, Any]]:
        """GET /api/v3/openOrders — open orders. Requires signed request."""
        params: dict[str, str] = {}
        if symbol:
            params["symbol"] = str(symbol).strip().upper()
        payload = self._signed_get("/api/v3/openOrders", params)
        if not isinstance(payload, list):
            raise PublicRestError("INVALID_JSON", "binance openOrders response must be a list")
        return [item for item in payload if isinstance(item, Mapping)]

    def close(self) -> None:
        self._client.close()
