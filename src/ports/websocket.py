"""Async JSON WebSocket boundary for public market streams."""

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol


class PublicWebSocketConnection(Protocol):
    async def recv_json(self) -> Mapping[str, Any]:
        """Receive one JSON object from the stream."""

    async def send_json(self, payload: Mapping[str, Any]) -> None:
        """Send a JSON object, for protocols such as OKX subscriptions."""

    async def close(self) -> None:
        """Close the connection."""


class PublicWebSocketConnector(Protocol):
    def connect(self, url: str) -> AbstractAsyncContextManager[PublicWebSocketConnection]:
        """Open a public WebSocket connection."""
