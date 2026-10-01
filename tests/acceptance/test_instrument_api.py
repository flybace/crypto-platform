from typing import Any

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.settings import Settings


class FakeCatalog:
    def list(self, venue_id: str, **kwargs: Any) -> dict[str, object]:
        return {
            "venue_id": venue_id,
            "venue_name": "Binance",
            "status": "LIVE",
            "source": "fake-public-instruments",
            "quote_asset": kwargs["quote_asset"],
            "fetched_at": "2026-09-15T00:00:00+00:00",
            "cache_ttl_seconds": 900,
            "total_count": 1,
            "returned_count": 1,
            "truncated": False,
            "items": [{"canonical_symbol": "BTC/USDT", "native_symbol": "BTCUSDT"}],
            "last_error": None,
        }


class FakeTickerService:
    def snapshot(self, **kwargs: Any) -> dict[str, object]:
        return {
            "kind": "public-market-tickers-v1",
            "quote_asset": kwargs["quote_asset"],
            "search": kwargs["search"],
            "as_of": "2026-09-18T00:00:00+00:00",
            "refresh_seconds": 5,
            "research_only": True,
            "total_count": 1,
            "returned_count": 1,
            "offset": kwargs["offset"],
            "limit": kwargs["limit"],
            "truncated": False,
            "sort_by": kwargs["sort_by"],
            "venues": [],
            "items": [{"symbol": "BTC/USDT", "markets": {}}],
        }

    def close(self) -> None:
        return None


def test_instrument_catalog_api_requires_auth_and_forwards_filters() -> None:
    settings = Settings(
        app_name="Instrument API Test",
        version="test",
        admin_username="admin",
        admin_password="local-password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
    )
    app = create_app(settings, instrument_catalog=FakeCatalog(), ticker_service=FakeTickerService())
    client = TestClient(app)
    try:
        assert client.get("/api/v1/market/instruments").status_code == 401
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "local-password"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.get(
            "/api/v1/market/instruments",
            params={"venue_id": "binance", "quote_asset": "USDT", "search": "BTC", "limit": 20, "refresh": "true"},
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["items"][0]["canonical_symbol"] == "BTC/USDT"
        assert response.json()["quote_asset"] == "USDT"
    finally:
        app.state.history_jobs.close()


def test_public_ticker_api_requires_auth_and_forwards_market_filters() -> None:
    settings = Settings(
        app_name="Ticker API Test",
        version="test",
        admin_username="admin",
        admin_password="local-password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
    )
    app = create_app(settings, ticker_service=FakeTickerService())
    client = TestClient(app)
    try:
        assert client.get("/api/v1/market/tickers").status_code == 401
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "local-password"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.get(
            "/api/v1/market/tickers",
            params={
                "quote_asset": "USDT",
                "search": "BTC",
                "sort_by": "spread",
                "limit": 20,
                "offset": 0,
                "refresh": "true",
            },
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["items"][0]["symbol"] == "BTC/USDT"
    finally:
        app.state.history_jobs.close()
