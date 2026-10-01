"""Market snapshot ingestion with freshness and sequence gates."""

from dataclasses import dataclass
from datetime import datetime

from domain.market import Instrument, OrderBookSnapshot
from domain.market_status import MarketConnectionState, MarketStatus


@dataclass(frozen=True, slots=True)
class MarketIngestResult:
    accepted: bool
    reason: str | None
    snapshot: OrderBookSnapshot | None


class MarketDataService:
    """Keep only validated snapshots and fail closed on data-quality violations."""

    def __init__(self, max_age_seconds: int = 2) -> None:
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self._max_age_seconds = max_age_seconds
        self._snapshots: dict[str, OrderBookSnapshot] = {}
        self._sequences: dict[str, int] = {}
        self._resync_required: set[str] = set()
        self._statuses: dict[str, MarketStatus] = {}

    def ingest(self, snapshot: OrderBookSnapshot, now: datetime) -> MarketIngestResult:
        """Ingest snapshots whose sequence must advance one by one."""
        return self._ingest(snapshot, now, require_contiguous=True, reset_resync=False)

    def ingest_stream_snapshot(
        self,
        snapshot: OrderBookSnapshot,
        now: datetime,
        *,
        is_resync: bool = False,
    ) -> MarketIngestResult:
        """Ingest a snapshot already validated by a native order-book stream.

        A reconstructed book can advance from sequence 100 to 126 because one
        WebSocket event may contain a range of native updates.  The
        ``OrderBookReconstructor`` owns that native sequence contract, so this
        entry point checks freshness and monotonicity without requiring a
        synthetic ``+1`` sequence.
        """
        return self._ingest(snapshot, now, require_contiguous=False, reset_resync=is_resync)

    def _ingest(
        self,
        snapshot: OrderBookSnapshot,
        now: datetime,
        *,
        require_contiguous: bool,
        reset_resync: bool,
    ) -> MarketIngestResult:
        key = snapshot.instrument.key
        if reset_resync:
            self._resync_required.discard(key)
            self._sequences.pop(key, None)
        if key in self._resync_required:
            self._set_status(snapshot, MarketConnectionState.DEGRADED, "RESYNC_REQUIRED")
            return MarketIngestResult(False, "RESYNC_REQUIRED", None)
        previous_sequence = self._sequences.get(key)
        if previous_sequence is not None and snapshot.sequence <= previous_sequence:
            self._set_status(snapshot, MarketConnectionState.DEGRADED, "SEQUENCE_NOT_ADVANCING")
            return MarketIngestResult(False, "SEQUENCE_NOT_ADVANCING", None)
        if require_contiguous and previous_sequence is not None and snapshot.sequence != previous_sequence + 1:
            self._resync_required.add(key)
            self._set_status(snapshot, MarketConnectionState.DEGRADED, "SEQUENCE_GAP")
            return MarketIngestResult(False, "SEQUENCE_GAP", None)

        age_seconds = snapshot.age_seconds(now)
        if age_seconds < 0:
            self._set_status(snapshot, MarketConnectionState.STALE, "MARKET_TIME_INVALID")
            return MarketIngestResult(False, "MARKET_TIME_INVALID", None)
        if age_seconds > self._max_age_seconds:
            self._set_status(snapshot, MarketConnectionState.STALE, "MARKET_DATA_STALE")
            return MarketIngestResult(False, "MARKET_DATA_STALE", None)

        self._snapshots[key] = snapshot
        self._sequences[key] = snapshot.sequence
        self._statuses[snapshot.instrument.venue_id] = MarketStatus(
            venue_id=snapshot.instrument.venue_id,
            state=MarketConnectionState.CONNECTED,
            last_received_at=snapshot.received_timestamp,
            last_sequence=snapshot.sequence,
        )
        return MarketIngestResult(True, None, snapshot)

    def resync(self, snapshot: OrderBookSnapshot, now: datetime) -> MarketIngestResult:
        """Accept a fresh snapshot after a sequence gap has stopped the stream."""
        key = snapshot.instrument.key
        self._resync_required.discard(key)
        self._sequences.pop(key, None)
        result = self.ingest(snapshot, now)
        if not result.accepted:
            self._resync_required.add(key)
        return result

    def mark_disconnected(self, venue_id: str, reason: str = "CONNECTOR_DISCONNECTED") -> MarketStatus:
        key = str(venue_id).strip().lower()
        current = self._statuses.get(key)
        status = MarketStatus(
            venue_id=key,
            state=MarketConnectionState.DISCONNECTED,
            last_received_at=None if current is None else current.last_received_at,
            last_sequence=None if current is None else current.last_sequence,
            reason=reason,
        )
        self._statuses[key] = status
        return status

    def mark_degraded(self, venue_id: str, reason: str = "CONNECTOR_DEGRADED") -> MarketStatus:
        key = str(venue_id).strip().lower()
        current = self._statuses.get(key)
        status = MarketStatus(
            venue_id=key,
            state=MarketConnectionState.DEGRADED,
            last_received_at=None if current is None else current.last_received_at,
            last_sequence=None if current is None else current.last_sequence,
            reason=reason,
        )
        self._statuses[key] = status
        return status

    def refresh_staleness(self, now: datetime) -> tuple[MarketStatus, ...]:
        changed: list[MarketStatus] = []
        for snapshot in self._snapshots.values():
            if snapshot.age_seconds(now) > self._max_age_seconds:
                status = MarketStatus(
                    venue_id=snapshot.instrument.venue_id,
                    state=MarketConnectionState.STALE,
                    last_received_at=snapshot.received_timestamp,
                    last_sequence=snapshot.sequence,
                    reason="MARKET_DATA_STALE",
                )
                self._statuses[status.venue_id] = status
                changed.append(status)
        return tuple(changed)

    def latest(self, instrument: Instrument) -> OrderBookSnapshot:
        try:
            return self._snapshots[instrument.key]
        except KeyError as error:
            raise KeyError(f"no accepted snapshot: {instrument.key}") from error

    def status(self, venue_id: str) -> MarketStatus:
        key = str(venue_id).strip().lower()
        return self._statuses.get(
            key,
            MarketStatus(venue_id=key, state=MarketConnectionState.DISCONNECTED, reason="NO_DATA"),
        )

    def _set_status(self, snapshot: OrderBookSnapshot, state: MarketConnectionState, reason: str) -> None:
        previous = self._statuses.get(snapshot.instrument.venue_id)
        self._statuses[snapshot.instrument.venue_id] = MarketStatus(
            venue_id=snapshot.instrument.venue_id,
            state=state,
            last_received_at=snapshot.received_timestamp if previous is None else previous.last_received_at,
            last_sequence=self._sequences.get(snapshot.instrument.key),
            reason=reason,
        )
