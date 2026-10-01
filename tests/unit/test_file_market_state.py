import json
from datetime import timedelta
from pathlib import Path

import pytest

from adapters.standalone.file_market_state import FileMarketStateStore
from domain.market_status import MarketConnectionState, MarketStatus
from ports.market_state import MarketStateStoreError
from tests.helpers import NOW, make_snapshot


def test_file_market_state_round_trips_snapshot_and_status(tmp_path: Path) -> None:
    store = FileMarketStateStore(tmp_path)
    snapshot = make_snapshot(sequence=42)

    store.write_snapshot(snapshot)
    store.write_status(
        MarketStatus(
            venue_id="BINANCE",
            state=MarketConnectionState.CONNECTED,
            last_received_at=NOW,
            last_sequence=42,
        )
    )

    status = store.read_status("binance")
    assert status is not None
    assert status.state is MarketConnectionState.CONNECTED
    assert status.last_sequence == 42
    snapshot_files = list(tmp_path.glob("snapshot-*.json"))
    assert len(snapshot_files) == 1
    payload = json.loads(snapshot_files[0].read_text(encoding="utf-8"))
    assert payload["instrument"]["native_symbol"] == "BTCUSDT"
    assert payload["sequence"] == 42
    assert payload["bids"][0]["price"] == "100.00"
    assert store.read_snapshot(snapshot.instrument.key) == snapshot


def test_file_market_state_rejects_invalid_status_without_exposing_file_content(tmp_path: Path) -> None:
    store = FileMarketStateStore(tmp_path)
    (tmp_path / "status-binance.json").write_text("{bad", encoding="utf-8")

    with pytest.raises(MarketStateStoreError, match="invalid"):
        store.read_status("binance")


def test_file_market_state_uses_aware_timestamps_and_safe_venue_names(tmp_path: Path) -> None:
    store = FileMarketStateStore(tmp_path)
    store.write_status(MarketStatus("okx", MarketConnectionState.DISCONNECTED, reason="NO_DATA"))

    assert store.read_status("OKX").last_received_at is None
    with pytest.raises(MarketStateStoreError, match="invalid"):
        store.read_status("../secrets")


def test_file_market_state_does_not_mask_newer_instrument_status(tmp_path: Path) -> None:
    store = FileMarketStateStore(tmp_path)
    store.write_status(
        MarketStatus(
            "bybit",
            MarketConnectionState.CONNECTED,
            last_received_at=NOW,
            last_sequence=100,
        )
    )

    store.write_status(
        MarketStatus(
            "bybit",
            MarketConnectionState.STALE,
            last_received_at=NOW - timedelta(seconds=1),
            last_sequence=90,
            reason="MARKET_DATA_STALE",
        )
    )

    status = store.read_status("bybit")
    assert status is not None
    assert status.state is MarketConnectionState.CONNECTED
    assert status.last_sequence == 100


def test_file_market_state_reader_observes_updates_from_another_process(tmp_path: Path) -> None:
    writer = FileMarketStateStore(tmp_path)
    reader = FileMarketStateStore(tmp_path, create=False)
    first = make_snapshot(sequence=1, bid_price="100.00")
    writer.write_snapshot(first)

    assert reader.read_snapshot(first.instrument.key) == first

    second = make_snapshot(sequence=2, bid_price="101.00", ask_price="101.10")
    writer.write_snapshot(second)
    refreshed = reader.read_snapshot(second.instrument.key)

    assert refreshed is not None
    assert refreshed.sequence == 2
    assert refreshed.bids[0].price == second.bids[0].price
