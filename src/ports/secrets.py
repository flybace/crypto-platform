"""Secret provider boundary for exchange API credentials.

Secrets must never enter logs, API responses, frontend code, or error messages.
"""

from typing import Protocol


class SecretProvider(Protocol):
    def get_api_key(self, venue_id: str) -> str | None:
        """Return the API key for a venue, or None when not configured."""

    def get_api_secret(self, venue_id: str) -> str | None:
        """Return the API secret for a venue, or None when not configured."""

    def is_configured(self, venue_id: str) -> bool:
        """True only when both key and secret are present for the venue."""
