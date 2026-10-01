"""Transport-neutral market status view used by a future HTTP adapter."""

from application.market_data import MarketDataService
from domain.market_status import MarketConnectionState, MarketStatus
from ports.market_state import MarketStateStore, MarketStateStoreError


class MarketStatusView:
    def __init__(self, market_data: MarketDataService, state_store: MarketStateStore | None = None) -> None:
        self._market_data = market_data
        self._state_store = state_store

    def get(self, venue_id: str) -> dict[str, object]:
        status = self._market_data.status(venue_id)
        if self._state_store is not None:
            try:
                persisted = self._state_store.read_status(venue_id)
            except MarketStateStoreError:
                persisted = None
                status = MarketStatus(
                    venue_id=status.venue_id,
                    state=MarketConnectionState.DEGRADED,
                    last_received_at=status.last_received_at,
                    last_sequence=status.last_sequence,
                    reason="STATE_STORE_INVALID",
                )
            if persisted is not None:
                status = persisted
        return {
            "venue_id": status.venue_id,
            "state": status.state.value,
            "last_received_at": None if status.last_received_at is None else status.last_received_at.isoformat(),
            "last_sequence": status.last_sequence,
            "reason": status.reason,
        }
