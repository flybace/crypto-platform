import pytest

from adapters.standalone.file_market_archive import FileMarketArchive
from adapters.standalone.file_market_state import FileMarketStateStore
from services.market_worker import _persist_snapshot, parse_instrument_allowlist
from tests.helpers import make_snapshot


def test_worker_allowlist_normalizes_venue_and_symbol() -> None:
    parsed = parse_instrument_allowlist(" BINANCE:btcusdt,okx:btc-usdt,bybit:btcusdt ")

    assert [(item.venue_id, item.native_symbol) for item in parsed] == [
        ("binance", "BTCUSDT"),
        ("okx", "BTC-USDT"),
        ("bybit", "BTCUSDT"),
    ]


def test_worker_allowlist_rejects_unsupported_or_duplicate_entries() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        parse_instrument_allowlist("kraken:BTCUSDT")
    with pytest.raises(ValueError, match="duplicates"):
        parse_instrument_allowlist("binance:BTCUSDT,binance:btcusdt")


def test_worker_allowlist_ignores_empty_entries_but_rejects_malformed_values() -> None:
    assert [(item.venue_id, item.native_symbol) for item in parse_instrument_allowlist(",binance:BTCUSDT,")] == [
        ("binance", "BTCUSDT")
    ]
    with pytest.raises(ValueError, match="venue:native_symbol"):
        parse_instrument_allowlist("binance")


def test_worker_persists_latest_state_and_optional_archive(tmp_path) -> None:
    latest = FileMarketStateStore(tmp_path / "latest")
    archive = FileMarketArchive(tmp_path / "archive")
    snapshot = make_snapshot(sequence=7)

    _persist_snapshot(latest, snapshot, archive)

    assert latest.read_snapshot(snapshot.instrument.key) == snapshot
    assert archive.read_snapshots(snapshot.instrument.key) == (snapshot,)
