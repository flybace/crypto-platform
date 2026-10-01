"""Durable append-only archive boundary for normalized public market states."""

from datetime import datetime
from typing import Any, Protocol

from domain.market import OrderBookSnapshot


class MarketArchiveError(RuntimeError):
    """The market archive could not be read or written safely."""


class MarketArchive(Protocol):
    def append_snapshot(self, snapshot: OrderBookSnapshot) -> None:
        """Append one accepted normalized L2 state."""

    def recover(self) -> int:
        """Remove an incomplete trailing record after an interrupted write."""

    def read_snapshots(
        self,
        instrument_key: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
        tail: bool = False,
    ) -> tuple[OrderBookSnapshot, ...]:
        """Read archived states in receive-time order."""

    def prune(self, *, before: datetime) -> int:
        """Remove archive segments whose data is older than ``before``."""

    def status(self) -> dict[str, Any]:
        """Return bounded operational metadata without exposing archived payloads."""
