from application.market_data import MarketDataService
from services.replay import SnapshotReplay

from tests.helpers import NOW, make_snapshot


def test_snapshot_replay_delivers_events_to_market_service() -> None:
    service = MarketDataService(max_age_seconds=2)
    snapshots = (make_snapshot(sequence=1), make_snapshot(sequence=2, bid_price="100.01"))
    replay = SnapshotReplay(snapshots)

    delivered = replay.run(lambda snapshot: service.ingest(snapshot, NOW))

    assert delivered == 2
    assert service.latest(snapshots[0].instrument) == snapshots[1]
