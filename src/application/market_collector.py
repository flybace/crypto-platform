"""Compose public REST snapshots and WebSocket order-book streams."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from inspect import isawaitable
from typing import Any

from domain.market import Instrument, OrderBookSnapshot
from domain.market_events import OrderBookDelta
from domain.market_status import MarketStatus
from ports.market_data import MarketDataGateway
from ports.websocket import PublicWebSocketConnection, PublicWebSocketConnector

from .market_data import MarketDataService
from .market_stream import PublicMarketStream, StreamResetRequired
from .order_book import OrderBookReconstructor, SequencePolicy


MarketEvent = OrderBookSnapshot | OrderBookDelta
MarketParser = Callable[[Mapping[str, Any], Instrument, datetime], MarketEvent | None]
SnapshotConsumer = Callable[[OrderBookSnapshot], object | Awaitable[object]]
StatusConsumer = Callable[[MarketStatus], object]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class MarketCollectorRunStats:
    """Counters collected across reconnect and resync attempts."""

    snapshots_seeded: int
    deltas_applied: int
    rejected_events: int
    resyncs: int
    failures: int
    connections: int
    reconnects: int


class PublicMarketCollector:
    """Maintain one venue/instrument book with fail-closed resynchronization."""

    def __init__(
        self,
        market_data: MarketDataService,
        snapshot_gateway: MarketDataGateway,
        websocket_connector: PublicWebSocketConnector,
        *,
        instrument: Instrument,
        stream_url: str,
        parser: MarketParser,
        subscription: Mapping[str, Any] | None = None,
        sequence_policy: SequencePolicy,
        max_age_seconds: int = 2,
        clock: Clock | None = None,
        resync_delay_seconds: float = 0.25,
        failure_retry_initial_seconds: float = 1.0,
        failure_retry_max_seconds: float = 30.0,
        bootstrap_stream_before_snapshot: bool = False,
        bootstrap_buffer_limit: int = 10_000,
        on_snapshot: SnapshotConsumer | None = None,
        on_status: StatusConsumer | None = None,
    ) -> None:
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        if resync_delay_seconds < 0:
            raise ValueError("resync_delay_seconds must not be negative")
        if failure_retry_initial_seconds <= 0 or failure_retry_max_seconds < failure_retry_initial_seconds:
            raise ValueError("failure retry delays are invalid")
        if bootstrap_buffer_limit <= 0:
            raise ValueError("bootstrap_buffer_limit must be positive")
        self._market_data = market_data
        self._snapshot_gateway = snapshot_gateway
        self._websocket_connector = websocket_connector
        self._instrument = instrument
        self._max_age_seconds = max_age_seconds
        self._stream_url = str(stream_url)
        self._parser = parser
        self._subscription = subscription
        self._reconstructor = OrderBookReconstructor(
            instrument,
            sequence_policy=sequence_policy,
            max_age_seconds=max_age_seconds,
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._resync_delay_seconds = resync_delay_seconds
        self._failure_retry_initial_seconds = failure_retry_initial_seconds
        self._failure_retry_max_seconds = failure_retry_max_seconds
        self._bootstrap_stream_before_snapshot = bootstrap_stream_before_snapshot
        self._bootstrap_buffer_limit = bootstrap_buffer_limit
        self._on_snapshot = on_snapshot
        self._on_status = on_status
        self._deltas_applied = 0
        self._rejected_events = 0

    async def run(self, stop_event: asyncio.Event) -> MarketCollectorRunStats:
        self._deltas_applied = 0
        self._rejected_events = 0
        snapshots_seeded = 0
        resyncs = 0
        failures = 0
        connections = 0
        reconnects = 0
        failure_delay = self._failure_retry_initial_seconds
        self._bootstrap_snapshots_seeded = 0

        freshness_task = asyncio.create_task(self._refresh_freshness(stop_event))
        try:
            while not stop_event.is_set():
                try:
                    self._bootstrap_snapshots_seeded = 0
                    self._bootstrap_is_resync = resyncs > 0
                    if not self._bootstrap_stream_before_snapshot:
                        await self._seed_snapshot(is_resync=resyncs > 0)
                        snapshots_seeded += 1
                        failure_delay = self._failure_retry_initial_seconds

                    stream = PublicMarketStream(
                        self._websocket_connector,
                        url=self._stream_url,
                        parser=self._parse,
                        consumer=self._consume,
                        subscription=self._subscription,
                        on_failure=self._on_stream_failure,
                        bootstrap=self._bootstrap_snapshot if self._bootstrap_stream_before_snapshot else None,
                    )
                    stream_stats = await stream.run(stop_event)
                    snapshots_seeded += self._bootstrap_snapshots_seeded
                    connections += stream_stats.connections
                    reconnects += stream_stats.reconnects
                    failures += stream_stats.failures
                    if stop_event.is_set():
                        break
                except StreamResetRequired as error:
                    if error.stats is not None:
                        connections += error.stats.connections
                        reconnects += error.stats.reconnects
                        failures += error.stats.failures
                    resyncs += 1
                    self._publish_status(self._market_data.mark_degraded(self._instrument.venue_id, error.reason))
                    await self._wait_before_retry(stop_event)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failures += 1
                    self._publish_status(self._market_data.mark_disconnected(self._instrument.venue_id, "COLLECTOR_FAILURE"))
                    await self._wait_for_retry(stop_event, failure_delay)
                    failure_delay = min(
                        self._failure_retry_max_seconds,
                        max(self._failure_retry_initial_seconds, failure_delay * 2),
                    )

            return MarketCollectorRunStats(
                snapshots_seeded=snapshots_seeded,
                deltas_applied=self._deltas_applied,
                rejected_events=self._rejected_events,
                resyncs=resyncs,
                failures=failures,
                connections=connections,
                reconnects=reconnects,
            )
        finally:
            freshness_task.cancel()
            with suppress(asyncio.CancelledError):
                await freshness_task

    def _parse(self, payload: Mapping[str, Any]) -> MarketEvent | None:
        return self._parser(payload, self._instrument, self._now())

    async def _consume(self, event: object) -> None:
        now = self._now()
        if isinstance(event, OrderBookSnapshot):
            result = self._reconstructor.seed(event, now)
            if not result.accepted or result.snapshot is None:
                self._publish_status(self._market_data.mark_degraded(self._instrument.venue_id, result.reason or "SNAPSHOT_REJECTED"))
                raise StreamResetRequired(result.reason or "SNAPSHOT_REJECTED")
            ingested = self._market_data.ingest_stream_snapshot(result.snapshot, now)
            if not ingested.accepted or ingested.snapshot is None:
                self._publish_status(self._market_data.status(self._instrument.venue_id))
                raise StreamResetRequired(ingested.reason or "SNAPSHOT_REJECTED")
            await self._publish(ingested.snapshot)
            self._publish_status(self._market_data.status(self._instrument.venue_id))
            return
        if not isinstance(event, OrderBookDelta):
            raise ValueError("market parser returned an unsupported event")
        result = self._reconstructor.apply(event, now)
        if not result.accepted or result.snapshot is None:
            self._rejected_events += 1
            self._market_data.mark_degraded(self._instrument.venue_id, result.reason or "DELTA_REJECTED")
            self._publish_status(self._market_data.status(self._instrument.venue_id))
            raise StreamResetRequired(result.reason or "DELTA_REJECTED")
        ingested = self._market_data.ingest_stream_snapshot(result.snapshot, now)
        if not ingested.accepted or ingested.snapshot is None:
            self._rejected_events += 1
            self._market_data.mark_degraded(self._instrument.venue_id, ingested.reason or "SNAPSHOT_REJECTED")
            self._publish_status(self._market_data.status(self._instrument.venue_id))
            raise StreamResetRequired(ingested.reason or "SNAPSHOT_REJECTED")
        await self._publish(ingested.snapshot)
        self._publish_status(self._market_data.status(self._instrument.venue_id))
        self._deltas_applied += 1

    async def _publish(self, snapshot: OrderBookSnapshot) -> None:
        if self._on_snapshot is None:
            return
        result = self._on_snapshot(snapshot)
        if isawaitable(result):
            await result

    def _on_stream_failure(self, _error: Exception) -> None:
        self._market_data.mark_disconnected(self._instrument.venue_id, "STREAM_FAILURE")
        self._publish_status(self._market_data.status(self._instrument.venue_id))

    def _publish_status(self, status: MarketStatus) -> None:
        if self._on_status is not None:
            self._on_status(status)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return an aware datetime")
        return value

    async def _wait_before_retry(self, stop_event: asyncio.Event) -> None:
        await self._wait_for_retry(stop_event, self._resync_delay_seconds)

    async def _wait_for_retry(self, stop_event: asyncio.Event, delay_seconds: float) -> None:
        if delay_seconds == 0:
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay_seconds)
        except asyncio.TimeoutError:
            pass

    async def _seed_snapshot(self, *, is_resync: bool) -> None:
        snapshot = await asyncio.to_thread(self._snapshot_gateway.fetch_snapshot, self._instrument)
        seeded = self._reconstructor.seed(snapshot, self._now())
        if not seeded.accepted or seeded.snapshot is None:
            self._rejected_events += 1
            self._publish_status(
                self._market_data.mark_degraded(
                    self._instrument.venue_id,
                    seeded.reason or "SNAPSHOT_REJECTED",
                )
            )
            raise StreamResetRequired(seeded.reason or "SNAPSHOT_REJECTED")
        ingested = self._market_data.ingest_stream_snapshot(
            seeded.snapshot,
            self._now(),
            is_resync=is_resync,
        )
        if not ingested.accepted or ingested.snapshot is None:
            self._rejected_events += 1
            self._publish_status(
                self._market_data.mark_degraded(
                    self._instrument.venue_id,
                    ingested.reason or "SNAPSHOT_REJECTED",
                )
            )
            raise StreamResetRequired(ingested.reason or "SNAPSHOT_REJECTED")
        await self._publish(ingested.snapshot)
        self._publish_status(self._market_data.status(self._instrument.venue_id))

    async def _bootstrap_snapshot(self, connection: PublicWebSocketConnection) -> None:
        """Buffer Binance diff-depth events until its REST snapshot is ready."""
        snapshot_task = asyncio.create_task(
            asyncio.to_thread(self._snapshot_gateway.fetch_snapshot, self._instrument)
        )
        receive_task: asyncio.Task[Mapping[str, Any]] | None = None
        buffered: list[Mapping[str, Any]] = []
        try:
            while not snapshot_task.done():
                receive_task = asyncio.create_task(connection.recv_json())
                done, _ = await asyncio.wait(
                    {snapshot_task, receive_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if receive_task in done:
                    buffered.append(receive_task.result())
                    if len(buffered) > self._bootstrap_buffer_limit:
                        raise StreamResetRequired("BOOTSTRAP_BUFFER_LIMIT")
                    continue
                receive_task.cancel()
                with suppress(asyncio.CancelledError):
                    await receive_task

            snapshot = await snapshot_task
            seeded = self._reconstructor.seed(snapshot, self._now())
            if not seeded.accepted or seeded.snapshot is None:
                self._rejected_events += 1
                self._publish_status(
                    self._market_data.mark_degraded(
                        self._instrument.venue_id,
                        seeded.reason or "SNAPSHOT_REJECTED",
                    )
                )
                raise StreamResetRequired(seeded.reason or "SNAPSHOT_REJECTED")
            ingested = self._market_data.ingest_stream_snapshot(
                seeded.snapshot,
                self._now(),
                is_resync=self._bootstrap_is_resync,
            )
            if not ingested.accepted or ingested.snapshot is None:
                self._rejected_events += 1
                self._publish_status(
                    self._market_data.mark_degraded(
                        self._instrument.venue_id,
                        ingested.reason or "SNAPSHOT_REJECTED",
                    )
                )
                raise StreamResetRequired(ingested.reason or "SNAPSHOT_REJECTED")
            await self._publish(ingested.snapshot)
            self._publish_status(self._market_data.status(self._instrument.venue_id))
            self._bootstrap_snapshots_seeded += 1

            for payload in buffered:
                event = self._parse(payload)
                if isinstance(event, OrderBookDelta) and event.last_sequence <= snapshot.sequence:
                    continue
                if event is not None:
                    await self._consume(event)
        finally:
            if not snapshot_task.done():
                snapshot_task.cancel()
                with suppress(asyncio.CancelledError):
                    await snapshot_task
            if receive_task is not None and not receive_task.done():
                receive_task.cancel()
                with suppress(asyncio.CancelledError):
                    await receive_task

    async def _refresh_freshness(self, stop_event: asyncio.Event) -> None:
        interval = max(0.1, min(self._max_age_seconds / 2, 5.0))
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                for status in self._market_data.refresh_staleness(self._now()):
                    self._publish_status(status)
