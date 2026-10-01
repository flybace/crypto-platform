import asyncio

from application.market_stream import PublicMarketStream


class FakeConnection:
    def __init__(self, messages: list[dict[str, object]], *, fail_after: bool) -> None:
        self.messages = list(messages)
        self.fail_after = fail_after
        self.sent: list[dict[str, object]] = []

    async def recv_json(self) -> dict[str, object]:
        if self.messages:
            return self.messages.pop(0)
        if self.fail_after:
            raise ConnectionError("fake disconnect")
        raise AssertionError("stream should have stopped before reading again")

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        return None


class FakeContext:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.connection.close()


class FakeConnector:
    def __init__(self, contexts: list[FakeContext]) -> None:
        self.contexts = list(contexts)
        self.urls: list[str] = []

    def connect(self, url: str) -> FakeContext:
        self.urls.append(url)
        return self.contexts.pop(0)


def test_public_market_stream_reconnects_and_sends_subscription() -> None:
    stop = asyncio.Event()
    first = FakeConnection([{"id": 1}], fail_after=True)
    second = FakeConnection([{"id": 2}], fail_after=False)
    connector = FakeConnector([FakeContext(first), FakeContext(second)])
    events: list[object] = []
    failures: list[Exception] = []

    def consume(event: object) -> None:
        events.append(event)
        if event == {"id": 2}:
            stop.set()

    stream = PublicMarketStream(
        connector,
        url="wss://example.test/stream",
        parser=lambda payload: payload,
        consumer=consume,
        subscription={"op": "subscribe"},
        reconnect_initial_seconds=0,
        reconnect_max_seconds=0,
        on_failure=failures.append,
    )

    stats = asyncio.run(stream.run(stop))

    assert events == [{"id": 1}, {"id": 2}]
    assert len(failures) == 1
    assert stats.messages == 2
    assert stats.connections == 2
    assert stats.reconnects == 1
    assert stats.failures == 1
    assert first.sent == [{"op": "subscribe"}]
    assert second.sent == [{"op": "subscribe"}]
