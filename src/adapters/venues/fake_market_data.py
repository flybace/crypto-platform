"""In-memory public market-data gateway for deterministic replay."""

from domain.market import Instrument, OrderBookSnapshot
from domain.market_status import MarketConnectionState, MarketStatus


class FakeMarketDataGateway:
    def __init__(self, snapshots: tuple[OrderBookSnapshot, ...] = ()) -> None:
        self._snapshots: dict[str, OrderBookSnapshot] = {}
        self._statuses: dict[str, MarketStatus] = {}
        for snapshot in snapshots:
            self.publish(snapshot)

    def publish(self, snapshot: OrderBookSnapshot) -> None:
        self._snapshots[snapshot.instrument.key] = snapshot
        self._statuses[snapshot.instrument.venue_id] = MarketStatus(
            venue_id=snapshot.instrument.venue_id,
            state=MarketConnectionState.CONNECTED,
            last_received_at=snapshot.received_timestamp,
            last_sequence=snapshot.sequence,
        )

    def fetch_snapshot(self, instrument: Instrument) -> OrderBookSnapshot:
        try:
            return self._snapshots[instrument.key]
        except KeyError as error:
            raise KeyError(f"no fake snapshot: {instrument.key}") from error

    def status(self, venue_id: str) -> MarketStatus:
        key = str(venue_id).strip().lower()
        return self._statuses.get(
            key,
            MarketStatus(venue_id=key, state=MarketConnectionState.DISCONNECTED, reason="NO_DATA"),
        )
