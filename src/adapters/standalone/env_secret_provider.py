"""Environment-variable secret provider for development.

Reads per-venue API credentials from environment variables:
  CRYPTO_<VENUE>_API_KEY / CRYPTO_<VENUE>_API_SECRET
(e.g. CRYPTO_BINANCE_API_KEY).

This is a development convenience, not a production secret store.
Production deployments must use a real secret manager; this provider
never logs values and returns None (not empty strings) when unset.
"""

from __future__ import annotations

import os


class EnvSecretProvider:
    def __init__(self, prefix: str = "CRYPTO") -> None:
        self._prefix = str(prefix or "CRYPTO").strip().upper() or "CRYPTO"

    def _var(self, venue_id: str, kind: str) -> str:
        venue = str(venue_id or "").strip().upper()
        return f"{self._prefix}_{venue}_{kind}"

    def get_api_key(self, venue_id: str) -> str | None:
        value = os.getenv(self._var(venue_id, "API_KEY"), "").strip()
        return value or None

    def get_api_secret(self, venue_id: str) -> str | None:
        value = os.getenv(self._var(venue_id, "API_SECRET"), "").strip()
        return value or None

    def is_configured(self, venue_id: str) -> bool:
        return self.get_api_key(venue_id) is not None and self.get_api_secret(venue_id) is not None
