"""websockets-based implementation of the public JSON stream port."""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from inspect import signature
from typing import Any
from urllib.parse import urlsplit

from websockets import connect

from .proxy import normalize_public_proxy
from ports.websocket import PublicWebSocketConnection


_CONNECT_SUPPORTS_PROXY = "proxy" in signature(connect).parameters


class WebSocketTransportError(RuntimeError):
    """A connection or payload error without response-body leakage."""


class _WebsocketsConnection(PublicWebSocketConnection):
    def __init__(self, socket: Any) -> None:
        self._socket = socket

    async def recv_json(self) -> Mapping[str, Any]:
        try:
            raw = await self._socket.recv()
        except Exception as error:
            raise WebSocketTransportError("public WebSocket receive failed") from error
        if raw is None:
            raise WebSocketTransportError("public WebSocket closed")
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise WebSocketTransportError("public WebSocket returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise WebSocketTransportError("public WebSocket payload must be an object")
        return payload

    async def send_json(self, payload: Mapping[str, Any]) -> None:
        try:
            await self._socket.send(json.dumps(dict(payload), separators=(",", ":")))
        except Exception as error:
            raise WebSocketTransportError("public WebSocket send failed") from error

    async def close(self) -> None:
        await self._socket.close()


class WebsocketsJsonConnector:
    def __init__(
        self,
        *,
        ping_interval_seconds: float = 20,
        ping_timeout_seconds: float = 20,
        open_timeout_seconds: float = 10,
        close_timeout_seconds: float = 2,
        proxy: str | None = None,
    ) -> None:
        if (
            ping_interval_seconds <= 0
            or ping_timeout_seconds <= 0
            or open_timeout_seconds <= 0
            or close_timeout_seconds <= 0
        ):
            raise ValueError("WebSocket timeouts must be positive")
        self._proxy = normalize_public_proxy(proxy) or None
        if self._proxy is not None and not _CONNECT_SUPPORTS_PROXY:
            raise ValueError("explicit WebSocket proxy requires websockets 15 or newer")
        self._options = {
            "ping_interval": ping_interval_seconds,
            "ping_timeout": ping_timeout_seconds,
            "open_timeout": open_timeout_seconds,
            "close_timeout": close_timeout_seconds,
        }

    @asynccontextmanager
    async def connect(self, url: str) -> AsyncIterator[PublicWebSocketConnection]:
        try:
            if self._proxy is not None:
                await _check_proxy_endpoint(self._proxy, self._options["open_timeout"])
            options = dict(self._options)
            if _CONNECT_SUPPORTS_PROXY:
                # Passing None explicitly disables environment proxy discovery.
                options["proxy"] = self._proxy
            async with connect(url, **options) as socket:
                yield _WebsocketsConnection(socket)
        except WebSocketTransportError:
            raise
        except Exception as error:
            raise WebSocketTransportError("public WebSocket connection failed") from error


async def _check_proxy_endpoint(proxy: str, timeout_seconds: float) -> None:
    """Fail before websockets creates a half-open proxy connection.

    Some websockets releases log an internal callback traceback when an HTTP
    proxy disappears before the client handshake initializes its receive
    queue. A short TCP preflight turns that case into the transport error the
    caller already knows how to classify, without logging proxy credentials.
    """
    parsed = urlsplit(proxy)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    writer = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout_seconds,
        )
    except Exception as error:
        raise WebSocketTransportError("public WebSocket proxy endpoint is unreachable") from error
    finally:
        if writer is not None:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()
