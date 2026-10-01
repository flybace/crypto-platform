import asyncio
import threading
from datetime import datetime, timedelta

from application.market_collector import PublicMarketCollector
from application.market_data import MarketDataService
from application.order_book import SequencePolicy
from domain.market import OrderBookSnapshot
from domain.market_events import OrderBookDelta, PriceLevelUpdate
from tests.helpers import NOW, make_instrument, make_snapshot


def _delta(instrument, first: int, last: int, previous: int) -> OrderBookDelta:
    return OrderBookDelta(
        instrument=instrument,
        bids=(PriceLevelUpdate("100.00", "0.8"),),
        asks=(),
        exchange_timestamp=NOW,
        received_timestamp=NOW,
        first_sequence=first,
        last_sequence=last,
        previous_sequence=previous,
    )


class FakeSnapshotGateway:
    def __init__(self, snapshots: tuple[OrderBookSnapshot, ...]) -> None:
        self.snapshots = snapshots
        self.calls = 0

    def fetch_snapshot(self, instrument):
        snapshot = self.snapshots[min(self.calls, len(self.snapshots) - 1)]
        self.calls += 1
        assert snapshot.instrument.key == instrument.key
        return snapshot


class FakeConnection:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.messages = list(messages)

    async def recv_json(self) -> dict[str, object]:
        if self.messages:
            return self.messages.pop(0)
        raise AssertionError("collector should stop after the final event")

    async def send_json(self, payload: dict[str, object]) -> None:
        return None

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

    def connect(self, url: str) -> FakeContext:
        assert url == "wss://example.test/book"
        return self.contexts.pop(0)


class BlockingSnapshotGateway:
    def __init__(self, snapshot: OrderBookSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def fetch_snapshot(self, instrument) -> OrderBookSnapshot:
        self.calls += 1
        assert self.snapshot.instrument.key == instrument.key
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test snapshot was not released")
        return self.snapshot


class BootstrapConnection:
    def __init__(
        self,
        before_snapshot: list[dict[str, object]],
        after_snapshot: dict[str, object],
        ready: asyncio.Event,
        release: threading.Event,
    ) -> None:
        self._before_snapshot = list(before_snapshot)
        self._after_snapshot = after_snapshot
        self._ready = ready
        self._release = release
        self._received_before_snapshot = 0
        self._after_snapshot_returned = False

    async def recv_json(self) -> dict[str, object]:
        if self._before_snapshot:
            payload = self._before_snapshot.pop(0)
            self._received_before_snapshot += 1
            if self._received_before_snapshot == 2:
                self._ready.set()
            return payload
        while not self._release.is_set():
            await asyncio.sleep(0.001)
        if self._after_snapshot_returned:
            await asyncio.Event().wait()
        self._after_snapshot_returned = True
        return self._after_snapshot

    async def send_json(self, payload: dict[str, object]) -> None:
        return None

    async def close(self) -> None:
        return None


def test_binance_bootstrap_buffers_before_rest_snapshot_and_continues_sequence() -> None:
    async def scenario() -> tuple[list[int], int, object]:
        instrument = make_instrument()
        snapshot = make_snapshot(instrument, age_seconds=0, sequence=100)
        gateway = BlockingSnapshotGateway(snapshot)
        ready = asyncio.Event()
        connection = BootstrapConnection(
            [
                {"first": 90, "last": 100, "previous": 89},
                {"first": 99, "last": 101, "previous": 100},
            ],
            {"first": 102, "last": 102, "previous": 101},
            ready,
            gateway.release,
        )
        connector = FakeConnector([FakeContext(connection)])
        service = MarketDataService(max_age_seconds=2)
        published: list[int] = []
        stop = asyncio.Event()

        def parse(payload, event_instrument, received: datetime):
            return _delta(
                event_instrument,
                int(payload["first"]),
                int(payload["last"]),
                int(payload["previous"]),
            )

        def on_snapshot(snapshot: OrderBookSnapshot) -> None:
            published.append(snapshot.sequence)
            if snapshot.sequence == 102:
                stop.set()

        collector = PublicMarketCollector(
            service,
            gateway,
            connector,
            instrument=instrument,
            stream_url="wss://example.test/book",
            parser=parse,
            sequence_policy=SequencePolicy.RANGE,
            clock=lambda: NOW,
            resync_delay_seconds=0,
            bootstrap_stream_before_snapshot=True,
            on_snapshot=on_snapshot,
        )

        task = asyncio.create_task(collector.run(stop))
        assert await asyncio.to_thread(gateway.started.wait, 5)
        await ready.wait()
        gateway.release.set()
        stats = await task
        return published, gateway.calls, stats

    published, gateway_calls, stats = asyncio.run(scenario())

    assert published == [100, 101, 102]
    assert gateway_calls == 1
    assert stats.snapshots_seeded == 1
    assert stats.deltas_applied == 2
    assert stats.rejected_events == 0
    assert stats.resyncs == 0


async def _run_freshness_probe(collector: PublicMarketCollector, stop: asyncio.Event) -> None:
    task = asyncio.create_task(collector._refresh_freshness(stop))
    await asyncio.sleep(0.55)
    assert collector._market_data.status("binance").state.value == "STALE"
    stop.set()
    await task


def test_collector_resyncs_after_a_sequence_gap_and_publishes_rebuilt_books() -> None:
    instrument = make_instrument()
    first = make_snapshot(instrument, age_seconds=0, sequence=100)
    second = make_snapshot(instrument, age_seconds=0, sequence=200)
    gateway = FakeSnapshotGateway((first, second))
    connector = FakeConnector(
        [
            FakeContext(FakeConnection([{"sequence": 102}])),
            FakeContext(FakeConnection([{"sequence": 202}])),
        ]
    )
    service = MarketDataService(max_age_seconds=2)
    published: list[int] = []
    statuses: list[str] = []
    stop = asyncio.Event()

    def parse(payload, event_instrument, received: datetime):
        sequence = int(payload["sequence"])
        if sequence == 102:
            return _delta(event_instrument, 102, 102, 101)
        return _delta(event_instrument, 201, sequence, 200)

    def on_snapshot(snapshot: OrderBookSnapshot) -> None:
        published.append(snapshot.sequence)
        if snapshot.sequence == 202:
            stop.set()

    collector = PublicMarketCollector(
        service,
        gateway,
        connector,
        instrument=instrument,
        stream_url="wss://example.test/book",
        parser=parse,
        sequence_policy=SequencePolicy.RANGE,
        clock=lambda: NOW,
        resync_delay_seconds=0,
        on_snapshot=on_snapshot,
        on_status=lambda status: statuses.append(status.state.value),
    )

    stats = asyncio.run(collector.run(stop))

    assert gateway.calls == 2
    assert published == [100, 200, 202]
    assert stats.snapshots_seeded == 2
    assert stats.deltas_applied == 1
    assert stats.rejected_events == 1
    assert stats.resyncs == 1
    assert stats.connections == 2
    assert service.latest(instrument).sequence == 202
    assert statuses[0] == "CONNECTED"
    assert "DEGRADED" in statuses
    assert statuses[-1] == "CONNECTED"


def test_collector_refreshes_staleness_when_the_stream_is_quiet() -> None:
    instrument = make_instrument()
    service = MarketDataService(max_age_seconds=1)
    service.ingest_stream_snapshot(make_snapshot(instrument, age_seconds=0), NOW)
    collector = PublicMarketCollector(
        service,
        FakeSnapshotGateway((make_snapshot(instrument, age_seconds=0),)),
        FakeConnector([]),
        instrument=instrument,
        stream_url="wss://example.test/book",
        parser=lambda payload, event_instrument, received: None,
        sequence_policy=SequencePolicy.RANGE,
        max_age_seconds=1,
        clock=lambda: NOW + timedelta(seconds=2),
    )

    asyncio.run(_run_freshness_probe(collector, asyncio.Event()))
