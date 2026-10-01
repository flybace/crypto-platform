from fastapi.testclient import TestClient

from application.history_download import HistoryDownloadService
from application.history_storage import HistoryStorage
from backend.app.main import create_app
from backend.app.settings import Settings


def _settings(tmp_path) -> Settings:
    return Settings(
        app_name="Network Settings Test",
        version="test",
        admin_username="admin",
        admin_password="local-password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
        history_data_path=str(tmp_path / "history"),
    )


def test_network_settings_api_is_authenticated_and_redacts_proxy_credentials(tmp_path) -> None:
    storage = HistoryStorage(tmp_path / "history")
    app = create_app(
        _settings(tmp_path),
        history_service=HistoryDownloadService({}, storage),
    )
    client = TestClient(app)
    try:
        assert client.get("/api/v1/settings/network").status_code == 401
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "local-password"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        initial = client.get("/api/v1/settings/network", headers=headers)
        assert initial.status_code == 200
        assert initial.json()["source"] == "environment"

        saved = client.put(
            "/api/v1/settings/network",
            headers=headers,
            json={
                "venues": {
                    "okx": {
                        "public_rest_base_url": "https://api.okx.example.test/v5",
                        "public_ws_base_url": "wss://stream.okx.example.test/public",
                        "history_base_url": "https://history.okx.example.test",
                        "instruments_path": "/v6/public/instruments",
                        "order_book_path": "/v6/market/books",
                        "candles_path": "/v6/market/candles",
                        "time_path": "/v6/public/time",
                        "http_proxy": "http://user:secret@proxy.example:7897",
                        "ws_proxy": "http://proxy.example:7897",
                    }
                }
            },
        )
        assert saved.status_code == 200
        body = saved.json()
        okx = next(item for item in body["venues"] if item["venue_id"] == "okx")
        assert okx["http_proxy"]["display"] == "http://***:***@proxy.example:7897"
        assert okx["public_rest_base_url"] == "https://api.okx.example.test/v5"
        assert okx["public_ws_base_url"] == "wss://stream.okx.example.test/public"
        assert okx["history_base_url"] == "https://history.okx.example.test"
        assert okx["instruments_path"] == "/v6/public/instruments"
        assert okx["order_book_path"] == "/v6/market/books"
        assert okx["candles_path"] == "/v6/market/candles"
        assert okx["time_path"] == "/v6/public/time"
        assert "secret" not in saved.text
        assert body["applied"] is True
        assert app.state.settings.okx_public_http_proxy == "http://user:secret@proxy.example:7897"
        assert app.state.settings.okx_public_rest_base_url == "https://api.okx.example.test/v5"
        assert app.state.settings.okx_public_ws_base_url == "wss://stream.okx.example.test/public"
        assert app.state.settings.okx_history_base_url == "https://history.okx.example.test"
        assert app.state.instrument_catalog._api_routes["okx"]["instruments_path"] == "/v6/public/instruments"

        invalid = client.put(
            "/api/v1/settings/network",
            headers=headers,
            json={"venues": {"okx": {"http_proxy": "socks5://127.0.0.1:1080"}}},
        )
        assert invalid.status_code == 422

        cleared = client.put(
            "/api/v1/settings/network",
            headers=headers,
            json={"venues": {"okx": {"clear_http_proxy": True, "clear_ws_proxy": True}}},
        )
        assert cleared.status_code == 200
        okx = next(item for item in cleared.json()["venues"] if item["venue_id"] == "okx")
        assert okx["http_proxy"]["mode"] == "direct"
        assert okx["ws_proxy"]["mode"] == "direct"
    finally:
        app.state.history_jobs.close()
