"""Bounded public stream runner with explicit reconnect behavior."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any

from ports.websocket import PublicWebSocketConnection, PublicWebSocketConnector


StreamBootstrap = Callable[[PublicWebSocketConnection], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class StreamRunStats:
    messages: int
    connections: int
    reconnects: int
    failures: int


class StreamResetRequired(RuntimeError):
    """Stop this stream attempt so its caller can rebuild its market state."""

    def __init__(self, reason: str, stats: StreamRunStats | None = None) -> None:
        self.reason = str(reason)
        self.stats = stats
        super().__init__(self.reason)


class PublicMarketStream:
    """Reconnect a public stream while keeping parsing and application injectable."""

    def __init__(
        self,
        connector: PublicWebSocketConnector,
        *,
        url: str,
        parser: Callable[[Mapping[str, Any]], object],
        consumer: Callable[[object], object | Awaitable[object]],
        subscription: Mapping[str, Any] | None = None,
        reconnect_initial_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
        on_failure: Callable[[Exception], object] | None = None,
        bootstrap: StreamBootstrap | None = None,
    ) -> None:
        if reconnect_initial_seconds < 0 or reconnect_max_seconds < reconnect_initial_seconds:
            raise ValueError("reconnect delays are invalid")
        self._connector = connector
        self._url = url
        self._parser = parser
        self._consumer = consumer
        self._subscription = subscription
        self._initial_delay = reconnect_initial_seconds
        self._max_delay = reconnect_max_seconds
        self._on_failure = on_failure
        self._bootstrap = bootstrap

    async def run(self, stop_event: asyncio.Event) -> StreamRunStats:
        messages = 0
        connections = 0
        reconnects = 0
        failures = 0
        delay = self._initial_delay

        while not stop_event.is_set():
            try:
                async with self._connector.connect(self._url) as connection:
                    connections += 1
                    delay = self._initial_delay
                    if self._subscription is not None:
                        await connection.send_json(self._subscription)
                    if self._bootstrap is not None:
                        await self._bootstrap(connection)
                    while not stop_event.is_set():
                        payload = await connection.recv_json()
                        event = self._parser(payload)
                        if event is None:
                            continue
                        result = self._consumer(event)
                        if isawaitable(result):
                            await result
                        messages += 1
                if stop_event.is_set():
                    break
                raise ConnectionError("public WebSocket ended without stop request")
            except asyncio.CancelledError:
                raise
            except StreamResetRequired as error:
                error.stats = StreamRunStats(messages, connections, reconnects, failures)
                raise
            except Exception as error:
                failures += 1
                if self._on_failure is not None:
                    self._on_failure(error)
                if stop_event.is_set():
                    break
                reconnects += 1
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(self._max_delay, max(self._initial_delay, delay * 2 or 0.001))

        return StreamRunStats(messages, connections, reconnects, failures)
