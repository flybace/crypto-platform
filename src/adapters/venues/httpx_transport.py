"""httpx implementation of the public JSON transport port."""

from collections.abc import Mapping
from datetime import UTC, datetime
from threading import RLock
from typing import Any

import httpx

from .proxy import normalize_public_proxy
from ports.rest import PublicJsonResponse, PublicRestError


class HttpxJsonTransport:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 5.0,
        client: httpx.Client | None = None,
        trust_env: bool = True,
        proxy: str | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        normalized_proxy = normalize_public_proxy(proxy)
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._trust_env = trust_env
        self._client = client or httpx.Client(
            base_url=self._base_url,
            timeout=timeout_seconds,
            trust_env=trust_env,
            proxy=normalized_proxy or None,
        )
        self._owns_client = client is None
        self._proxy = normalized_proxy
        self._lock = RLock()

    def get_json(self, path: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        payload = self.get_json_value(path, params)
        if not isinstance(payload, Mapping):
            raise PublicRestError("INVALID_JSON", "public REST response must be a JSON object")
        return payload

    def get_json_value(self, path: str, params: Mapping[str, str]) -> Any:
        return self.get_json_value_with_metadata(path, params).payload

    def get_json_value_with_metadata(
        self,
        path: str,
        params: Mapping[str, str],
    ) -> PublicJsonResponse:
        try:
            with self._lock:
                response = self._client.get(path, params=dict(params))
        except httpx.HTTPError as error:
            raise PublicRestError("NETWORK_ERROR", "public REST request failed") from error

        retry_after = self._retry_after(response.headers.get("Retry-After"))
        if response.status_code == 429:
            raise PublicRestError(
                "RATE_LIMITED",
                "public REST rate limit reached",
                status_code=429,
                retry_after_seconds=retry_after,
            )
        if 500 <= response.status_code <= 599:
            raise PublicRestError("UPSTREAM_UNAVAILABLE", "public REST upstream unavailable", status_code=response.status_code)
        if 400 <= response.status_code <= 499:
            raise PublicRestError("UPSTREAM_REJECTED", "public REST request rejected", status_code=response.status_code)
        if response.status_code < 200 or response.status_code >= 300:
            raise PublicRestError("HTTP_ERROR", "unexpected public REST status", status_code=response.status_code)

        try:
            payload = response.json()
        except ValueError as error:
            raise PublicRestError("INVALID_JSON", "public REST response was not JSON", status_code=response.status_code) from error
        return PublicJsonResponse(
            path=path,
            params=params,
            payload=payload,
            status_code=response.status_code,
            headers=self._safe_headers(response.headers),
            received_at=datetime.now(UTC),
        )

    def close(self) -> None:
        with self._lock:
            if self._owns_client:
                self._client.close()

    def set_proxy(self, proxy: str | None) -> None:
        """Rebuild the owned client so a saved proxy applies without restart."""
        normalized_proxy = normalize_public_proxy(proxy)
        with self._lock:
            if normalized_proxy == self._proxy:
                return
            if not self._owns_client:
                raise ValueError("an injected HTTP client cannot be reconfigured")
            next_client = httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                trust_env=self._trust_env,
                proxy=normalized_proxy or None,
            )
            previous = self._client
            self._client = next_client
            self._proxy = normalized_proxy
        previous.close()

    def set_base_url(self, base_url: str) -> None:
        """Rebuild the owned client so a saved public endpoint applies live."""
        normalized_base_url = str(base_url).strip().rstrip("/")
        if not normalized_base_url:
            raise ValueError("base_url must not be empty")
        with self._lock:
            if normalized_base_url == self._base_url:
                return
            if not self._owns_client:
                raise ValueError("an injected HTTP client cannot be reconfigured")
            next_client = httpx.Client(
                base_url=normalized_base_url,
                timeout=self._timeout_seconds,
                trust_env=self._trust_env,
                proxy=self._proxy or None,
            )
            previous = self._client
            self._client = next_client
            self._base_url = normalized_base_url
        previous.close()

    @staticmethod
    def _retry_after(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
        """Keep cache and rate-limit hints without persisting credential headers."""
        allowed = {
            "content-type",
            "date",
            "etag",
            "last-modified",
            "retry-after",
            "x-mbx-used-weight-1m",
        }
        return {key.lower(): str(value) for key, value in headers.items() if key.lower() in allowed}
