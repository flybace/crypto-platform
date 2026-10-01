"""Fail-closed reconstruction of a book from a REST snapshot and WS deltas."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from domain.market import Instrument, OrderBookSnapshot, PriceLevel, _aware
from domain.market_events import OrderBookDelta, PriceLevelUpdate


class SequencePolicy(StrEnum):
    """Native sequence semantics used by a public venue stream."""

    RANGE = "RANGE"
    PREVIOUS_ID = "PREVIOUS_ID"


@dataclass(frozen=True, slots=True)
class OrderBookApplyResult:
    accepted: bool
    reason: str | None
    snapshot: OrderBookSnapshot | None


class OrderBookReconstructor:
    """Apply venue-normalized updates only after a valid REST/WS snapshot."""

    def __init__(self, instrument: Instrument, *, sequence_policy: SequencePolicy, max_age_seconds: int = 2) -> None:
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self._instrument = instrument
        self._sequence_policy = sequence_policy if isinstance(sequence_policy, SequencePolicy) else SequencePolicy(sequence_policy)
        self._max_age_seconds = max_age_seconds
        self._snapshot: OrderBookSnapshot | None = None
        self._delta_started = False

    @property
    def snapshot(self) -> OrderBookSnapshot | None:
        return self._snapshot

    def seed(self, snapshot: OrderBookSnapshot, now: datetime) -> OrderBookApplyResult:
        if snapshot.instrument.key != self._instrument.key:
            return OrderBookApplyResult(False, "INSTRUMENT_MISMATCH", None)
        reason = self._freshness_reason(snapshot, now)
        if reason is not None:
            return OrderBookApplyResult(False, reason, None)
        self._snapshot = snapshot
        self._delta_started = False
        return OrderBookApplyResult(True, None, snapshot)

    def apply(self, delta: OrderBookDelta, now: datetime) -> OrderBookApplyResult:
        current = self._snapshot
        if delta.instrument.key != self._instrument.key:
            return OrderBookApplyResult(False, "INSTRUMENT_MISMATCH", None)
        if current is None:
            return OrderBookApplyResult(False, "SNAPSHOT_REQUIRED", None)
        reason = self._freshness_reason(delta, now)
        if reason is not None:
            return OrderBookApplyResult(False, reason, None)
        sequence_reason = self._sequence_reason(current, delta)
        if sequence_reason is not None:
            return OrderBookApplyResult(False, sequence_reason, None)

        bids = {level.price: level.quantity for level in current.bids}
        asks = {level.price: level.quantity for level in current.asks}
        self._apply_levels(bids, delta.bids)
        self._apply_levels(asks, delta.asks)
        try:
            snapshot = OrderBookSnapshot(
                instrument=self._instrument,
                bids=tuple(PriceLevel(price, quantity) for price, quantity in sorted(bids.items(), reverse=True)),
                asks=tuple(PriceLevel(price, quantity) for price, quantity in sorted(asks.items())),
                exchange_timestamp=delta.exchange_timestamp,
                received_timestamp=delta.received_timestamp,
                sequence=delta.last_sequence,
            )
        except ValueError:
            return OrderBookApplyResult(False, "INVALID_BOOK", None)
        self._snapshot = snapshot
        self._delta_started = True
        return OrderBookApplyResult(True, None, snapshot)

    def _sequence_reason(self, current: OrderBookSnapshot, delta: OrderBookDelta) -> str | None:
        if delta.last_sequence <= current.sequence:
            return "DUPLICATE_OR_STALE"
        if self._sequence_policy is SequencePolicy.PREVIOUS_ID:
            if delta.previous_sequence != current.sequence:
                return "SEQUENCE_GAP"
            return None
        expected = current.sequence + 1
        if delta.first_sequence > expected:
            return "SEQUENCE_GAP"
        if self._delta_started and delta.previous_sequence is not None and delta.previous_sequence != current.sequence:
            return "SEQUENCE_GAP"
        return None

    @staticmethod
    def _apply_levels(target: dict[Decimal, Decimal], updates: tuple[PriceLevelUpdate, ...]) -> None:
        for level in updates:
            if level.quantity == 0:
                target.pop(level.price, None)
            else:
                target[level.price] = level.quantity

    def _freshness_reason(self, event: OrderBookSnapshot | OrderBookDelta, now: datetime) -> str | None:
        _aware(now, "now")
        received_at = event.received_timestamp
        age_seconds = (now - received_at).total_seconds()
        if age_seconds < 0:
            return "MARKET_TIME_INVALID"
        if age_seconds > self._max_age_seconds:
            return "MARKET_DATA_STALE"
        return None
