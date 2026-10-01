from fastapi.testclient import TestClient

from adapters.standalone.file_market_archive import FileMarketArchive
from backend.app.main import create_app
from backend.app.settings import Settings
from tests.helpers import make_snapshot


def _settings() -> Settings:
    return Settings(
        app_name="Market Archive Test",
        version="test",
        admin_username="admin",
        admin_password="local-password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
    )


def _headers(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "local-password"},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_market_archive_api_is_authenticated_and_returns_latest_bounded_depth(tmp_path) -> None:
    archive = FileMarketArchive(tmp_path / "archive")
    snapshots = tuple(
        make_snapshot(sequence=sequence, bid_price=f"100.{sequence:02d}", age_seconds=3 - sequence)
        for sequence in range(1, 4)
    )
    for snapshot in snapshots:
        archive.append_snapshot(snapshot)
    app = create_app(_settings(), market_archive=archive)

    with TestClient(app) as client:
        path = "/api/v1/market/archive/order-books"
        params = {"instrument_key": snapshots[0].instrument.key, "limit": 2, "depth": 1}
        assert client.get(path, params=params).status_code == 401

        response = client.get(path, params=params, headers=_headers(client))

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert payload["depth"] == 1
    assert [item["sequence"] for item in payload["items"]] == [2, 3]
    assert all(len(item["bids"]) == 1 and len(item["asks"]) == 1 for item in payload["items"])
    assert payload["as_of"] == snapshots[-1].received_timestamp.isoformat()


def test_market_archive_api_reports_disabled_and_rejects_invalid_time_range(tmp_path) -> None:
    app = create_app(_settings())

    with TestClient(app) as client:
        headers = _headers(client)
        path = "/api/v1/market/archive/order-books"
        disabled = client.get(path, params={"instrument_key": "binance:spot:BTC/USDT"}, headers=headers)
    archive = FileMarketArchive(tmp_path / "archive", create=False)
    enabled_app = create_app(_settings(), market_archive=archive)
    with TestClient(enabled_app) as client:
        headers = _headers(client)
        invalid_range = client.get(
            path,
            params={
                "instrument_key": "binance:spot:BTC/USDT",
                "start_at": "2026-02-02T00:00:00Z",
                "end_at": "2026-02-01T00:00:00Z",
            },
            headers=headers,
        )

    assert disabled.status_code == 503
    assert "disabled" in disabled.json()["detail"]
    assert invalid_range.status_code == 422

def test_market_archive_status_api_exposes_governance_metadata(tmp_path) -> None:
    disabled_app = create_app(_settings())
    with TestClient(disabled_app) as client:
        headers = _headers(client)
        disabled = client.get("/api/v1/market/archive/status", headers=headers)

    archive = FileMarketArchive(tmp_path / "archive", retention_seconds=3600)
    archive.append_snapshot(make_snapshot(sequence=9))
    enabled_app = create_app(_settings(), market_archive=archive)
    with TestClient(enabled_app) as client:
        headers = _headers(client)
        enabled = client.get("/api/v1/market/archive/status", headers=headers)

    assert disabled.status_code == 200
    assert disabled.json() == {"enabled": False, "status": "DISABLED"}
    assert enabled.status_code == 200
    payload = enabled.json()
    assert payload["enabled"] is True
    assert payload["status"] == "READY"
    assert payload["schema"] == "market-archive-status-v1"
    assert payload["segment_count"] == 1
    assert payload["total_bytes"] > 0
    assert payload["retention_seconds"] == 3600
