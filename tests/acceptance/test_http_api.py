from pathlib import Path

from fastapi.testclient import TestClient

from adapters.standalone.file_market_state import FileMarketStateStore
from domain.market_status import MarketConnectionState, MarketStatus
from tests.helpers import NOW, make_snapshot
from web.http_api import create_app


def test_read_only_http_api_exposes_health_and_market_status() -> None:
    client = TestClient(create_app())

    health = client.get("/health")
    status = client.get("/v1/market/status/BINANCE")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "execution_mode": "DISABLED"}
    assert status.status_code == 200
    assert status.json()["venue_id"] == "binance"
    assert status.json()["state"] == "DISCONNECTED"


def test_http_api_reads_shared_market_state_without_exposing_raw_payload(tmp_path: Path, monkeypatch) -> None:
    store = FileMarketStateStore(tmp_path)
    snapshot = make_snapshot(sequence=12)
    store.write_snapshot(snapshot)
    store.write_status(
        MarketStatus(
            "binance",
            MarketConnectionState.CONNECTED,
            last_received_at=NOW,
            last_sequence=12,
        )
    )
    monkeypatch.setenv("CRYPTO_MARKET_STATE_PATH", str(tmp_path))

    client = TestClient(create_app())

    status = client.get("/v1/market/status/BINANCE")
    response = client.get(
        "/v1/market/snapshot",
        params={"instrument_key": snapshot.instrument.key},
    )

    assert status.json()["state"] == "CONNECTED"
    assert status.json()["last_sequence"] == 12
    assert response.status_code == 200
    assert response.json()["sequence"] == 12
    assert response.json()["bids"] == [{"price": "100.00", "quantity": "1"}]
