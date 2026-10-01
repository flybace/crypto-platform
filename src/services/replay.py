"""Deterministic event replay utility for market-data tests and research."""

from collections.abc import Callable, Iterable

from domain.market import OrderBookSnapshot


class SnapshotReplay:
    def __init__(self, snapshots: Iterable[OrderBookSnapshot]) -> None:
        self._snapshots = tuple(snapshots)

    def run(self, consumer: Callable[[OrderBookSnapshot], object]) -> int:
        for snapshot in self._snapshots:
            consumer(snapshot)
        return len(self._snapshots)
