from datetime import timedelta

from application.market_data import MarketDataService
from domain.market_status import MarketConnectionState

from tests.helpers import NOW, make_context, make_snapshot


def test_market_service_accepts_contiguous_snapshots_and_replays_latest() -> None:
    service = MarketDataService(max_age_seconds=2)
    first = make_snapshot(sequence=1)
    second = make_snapshot(sequence=2, bid_price="100.01")

    assert service.ingest(first, NOW).accepted is True
    assert service.ingest(second, NOW).accepted is True
    assert service.latest(first.instrument) == second
    assert service.status("binance").state is MarketConnectionState.CONNECTED


def test_sequence_gap_requires_explicit_resync() -> None:
    service = MarketDataService(max_age_seconds=2)
    service.ingest(make_snapshot(sequence=1), NOW)

    gap = service.ingest(make_snapshot(sequence=3), NOW)
    blocked = service.ingest(make_snapshot(sequence=4), NOW)
    recovered = service.resync(make_snapshot(sequence=100), NOW)

    assert gap.reason == "SEQUENCE_GAP"
    assert blocked.reason == "RESYNC_REQUIRED"
    assert recovered.accepted is True
    assert service.status("binance").last_sequence == 100


def test_reconstructed_stream_snapshots_allow_native_sequence_ranges() -> None:
    service = MarketDataService(max_age_seconds=2)
    service.ingest_stream_snapshot(make_snapshot(sequence=100), NOW)

    accepted = service.ingest_stream_snapshot(make_snapshot(sequence=126), NOW)

    assert accepted.accepted is True
    assert service.latest(make_snapshot().instrument).sequence == 126


def test_staleness_refresh_and_disconnect_are_visible() -> None:
    service = MarketDataService(max_age_seconds=2)
    service.ingest(make_snapshot(sequence=1), NOW)

    changed = service.refresh_staleness(NOW + timedelta(seconds=3))
    disconnected = service.mark_disconnected("BINANCE", "network_down")

    assert changed[0].state is MarketConnectionState.STALE
    assert disconnected.state is MarketConnectionState.DISCONNECTED
    assert disconnected.reason == "network_down"
