"""Connection and freshness state for public market data."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .market import _aware


class MarketConnectionState(StrEnum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    DEGRADED = "DEGRADED"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class MarketStatus:
    venue_id: str
    state: MarketConnectionState
    last_received_at: datetime | None = None
    last_sequence: int | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        venue_id = str(self.venue_id).strip().lower()
        state = self.state if isinstance(self.state, MarketConnectionState) else MarketConnectionState(self.state)
        if not venue_id:
            raise ValueError("venue_id must not be empty")
        if self.last_received_at is not None:
            _aware(self.last_received_at, "last_received_at")
        if self.last_sequence is not None and self.last_sequence < 0:
            raise ValueError("last_sequence must not be negative")
        object.__setattr__(self, "venue_id", venue_id)
        object.__setattr__(self, "state", state)
