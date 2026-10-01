import asyncio
import json
from pathlib import Path

import pytest

from adapters.venues.proxy import normalize_public_proxy, redact_public_proxy
from backend.app.services.public_network_settings import (
    PublicNetworkSettingsStore,
    normalize_api_route,
    normalize_public_endpoint,
    probe_public_http,
    probe_public_websocket,
    proxy_defaults_from_env,
)
from backend.app.settings import Settings
from adapters.venues.websockets_transport import WebSocketTransportError, _check_proxy_endpoint


def test_public_proxy_validation_accepts_http_https_and_redacts_credentials() -> None:
    assert normalize_public_proxy("") == ""
    assert normalize_public_proxy(" http://proxy.example:7897 ") == "http://proxy.example:7897"
    assert normalize_public_proxy("https://user:secret@proxy.example:443") == "https://user:secret@proxy.example:443"
    assert redact_public_proxy("https://user:secret@proxy.example:443") == "https://***:***@proxy.example:443"


def test_public_proxy_validation_rejects_socks_and_target_paths() -> None:
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        normalize_public_proxy("socks5://127.0.0.1:1080")
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        normalize_public_proxy("http://proxy.example:7897/path")
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        normalize_public_proxy("http://proxy.example:7897?token=secret")


def test_public_endpoint_validation_keeps_api_urls_editable_without_credentials() -> None:
    assert normalize_public_endpoint(" https://api.example.test/v5/ ", field="public_rest_base_url") == "https://api.example.test/v5"
    assert normalize_public_endpoint("wss://stream.example.test/ws", field="public_ws_base_url") == "wss://stream.example.test/ws"

    with pytest.raises(ValueError, match="public_ws_base_url must use"):
        normalize_public_endpoint("https://stream.example.test", field="public_ws_base_url")
    with pytest.raises(ValueError, match="must not contain credentials"):
        normalize_public_endpoint("https://user:secret@example.test", field="history_base_url")
    with pytest.raises(ValueError, match="must not contain a query"):
        normalize_public_endpoint("https://api.example.test?token=secret", field="history_base_url")


def test_public_api_route_validation_rejects_urls_and_placeholders() -> None:
    assert normalize_api_route(" /api/v5/market/books/ ", field="order_book_path") == "/api/v5/market/books"
    with pytest.raises(ValueError, match="absolute path"):
        normalize_api_route("https://api.example.test/books", field="order_book_path")
    with pytest.raises(ValueError, match="absolute path"):
        normalize_api_route("api/v5/books", field="order_book_path")
    with pytest.raises(ValueError, match="placeholders"):
        normalize_api_route("/api/v5/books/{symbol}", field="order_book_path")


def test_websocket_proxy_preflight_hides_proxy_details(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_open_connection(host: str | None, port: int) -> tuple[object, object]:
        raise OSError("connection refused")

    monkeypatch.setattr("adapters.venues.websockets_transport.asyncio.open_connection", fail_open_connection)

    with pytest.raises(WebSocketTransportError, match="proxy endpoint is unreachable") as error:
        asyncio.run(_check_proxy_endpoint("http://user:secret@proxy.example:7897", 0.1))

    assert "secret" not in str(error.value)


def test_public_network_store_overrides_environment_and_can_clear_values(tmp_path: Path) -> None:
    path = tmp_path / ".runtime" / "public-network-settings.json"
    store = PublicNetworkSettingsStore(
        path,
        defaults=proxy_defaults_from_env(lambda name, default="": default if name != "CRYPTO_OKX_PUBLIC_HTTP_PROXY" else "http://env.example:7897"),
    )

    initial = store.current()
    assert initial.source == "environment"
    assert initial.proxies["okx"]["http_proxy"] == "http://env.example:7897"

    saved = store.save(
        {
            "okx": {
                "http_proxy": "http://user:secret@proxy.example:7897",
                "ws_proxy": "http://proxy.example:7897",
            }
        }
    )
    assert saved.source == "saved"
    assert store.current().proxies["okx"]["http_proxy"] == "http://user:secret@proxy.example:7897"
    view = store.public_view(saved)
    okx = next(item for item in view["venues"] if item["venue_id"] == "okx")
    assert okx["http_proxy"] == {
        "configured": True,
        "display": "http://***:***@proxy.example:7897",
        "mode": "proxy",
    }
    assert "secret" not in str(view)

    cleared = store.save({"okx": {"clear_http_proxy": True, "clear_ws_proxy": True}})
    assert cleared.proxies["okx"] == {"http_proxy": "", "ws_proxy": ""}
    assert path.is_file()


def test_public_network_store_persists_endpoints_and_migrates_proxy_only_v1_files(tmp_path: Path) -> None:
    path = tmp_path / ".runtime" / "public-network-settings.json"
    store = PublicNetworkSettingsStore(path)
    saved = store.save(
        {
            "okx": {
                "public_rest_base_url": "https://api.okx.example.test/v5",
                "public_ws_base_url": "wss://stream.okx.example.test/public",
                "history_base_url": "https://history.okx.example.test",
                "instruments_path": "/v6/public/instruments",
                "order_book_path": "/v6/market/books",
                "candles_path": "/v6/market/candles",
                "time_path": "/v6/public/time",
            }
        }
    )

    assert saved.endpoints["okx"] == {
        "public_rest_base_url": "https://api.okx.example.test/v5",
        "public_ws_base_url": "wss://stream.okx.example.test/public",
        "history_base_url": "https://history.okx.example.test",
    }
    view = store.public_view(saved)
    okx = next(item for item in view["venues"] if item["venue_id"] == "okx")
    assert okx["public_rest_base_url"] == "https://api.okx.example.test/v5"
    assert okx["public_ws_base_url"] == "wss://stream.okx.example.test/public"
    assert okx["history_base_url"] == "https://history.okx.example.test"
    assert okx["instruments_path"] == "/v6/public/instruments"
    assert okx["order_book_path"] == "/v6/market/books"
    assert okx["candles_path"] == "/v6/market/candles"
    assert okx["time_path"] == "/v6/public/time"

    path.write_text(
        json.dumps(
            {
                "schema_version": "public-network-settings-v1",
                "updated_at": "2026-09-17T00:00:00+00:00",
                "venues": {"okx": {"http_proxy": "http://proxy.example.test:7897"}},
            }
        ),
        encoding="utf-8",
    )
    migrated = PublicNetworkSettingsStore(path).current()
    assert migrated.source == "saved"
    assert migrated.proxies["okx"]["http_proxy"] == "http://proxy.example.test:7897"
    assert migrated.endpoints["okx"]["public_rest_base_url"] == "https://www.okx.com"
    assert migrated.api_routes["okx"]["order_book_path"] == "/api/v5/market/books"


def test_public_websocket_probe_uses_configured_ws_proxy_and_requires_market_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeConnection:
        def __init__(self) -> None:
            self.sent: list[dict[str, object]] = []
            self.received = 0

        async def send_json(self, payload: dict[str, object]) -> None:
            self.sent.append(payload)

        async def recv_json(self) -> dict[str, object]:
            self.received += 1
            return {"event": "subscribe"} if self.received == 1 else {"data": [{"bids": [], "asks": []}]}

        async def close(self) -> None:
            return None

    class FakeContext:
        def __init__(self, connection: FakeConnection) -> None:
            self.connection = connection

        async def __aenter__(self) -> FakeConnection:
            return self.connection

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            await self.connection.close()

    class FakeConnector:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def connect(self, url: str) -> FakeContext:
            captured["url"] = url
            return FakeContext(FakeConnection())

    monkeypatch.setattr(
        "backend.app.services.public_network_settings.WebsocketsJsonConnector",
        FakeConnector,
    )
    settings = Settings(
        app_name="probe",
        version="test",
        admin_username="admin",
        admin_password="password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
        history_data_path="data/history",
        okx_public_ws_proxy="http://192.168.68.186:7897",
    )

    result = probe_public_websocket("okx", settings)

    assert result["state"] == "REACHABLE"
    assert result["channel"] == "WebSocket"
    assert result["used_proxy"] is True
    assert captured["proxy"] == "http://192.168.68.186:7897"
    assert captured["close_timeout_seconds"] == 2.0
    assert captured["url"] == "wss://ws.okx.com:8443/ws/v5/public"


def test_probes_use_saved_snapshot_proxies_over_environment_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class FakeTransport:
        def __init__(self, base_url: str, **kwargs: object) -> None:
            captured["http_base_url"] = base_url
            captured["http_proxy"] = kwargs["proxy"]

        def get_json_value_with_metadata(self, path: str, params: dict[str, str]):
            captured["http_path"] = path
            return type("Response", (), {"status_code": 200})()

        def close(self) -> None:
            return None

    class FakeConnection:
        async def send_json(self, payload: dict[str, object]) -> None:
            return None

        async def recv_json(self) -> dict[str, object]:
            return {"data": [{"bids": [], "asks": []}]}

        async def close(self) -> None:
            return None

    class FakeContext:
        async def __aenter__(self) -> FakeConnection:
            return FakeConnection()

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    class FakeConnector:
        def __init__(self, **kwargs: object) -> None:
            captured["ws_proxy"] = kwargs["proxy"]

        def connect(self, url: str) -> FakeContext:
            captured["ws_url"] = url
            return FakeContext()

    monkeypatch.setattr("backend.app.services.public_network_settings.HttpxJsonTransport", FakeTransport)
    monkeypatch.setattr("backend.app.services.public_network_settings.WebsocketsJsonConnector", FakeConnector)
    settings = Settings(
        app_name="probe",
        version="test",
        admin_username="admin",
        admin_password="password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
        history_data_path="data/history",
        okx_public_http_proxy="http://environment.example:7897",
        okx_public_ws_proxy="http://environment.example:7897",
    )
    store = PublicNetworkSettingsStore(
        tmp_path / "network-settings.json",
        defaults=proxy_defaults_from_env(lambda name, default="": default),
    )
    saved = store.save(
        {
            "okx": {
                "http_proxy": "http://saved.example:7897",
                "ws_proxy": "http://saved.example:7897",
            }
        }
    )

    http_result = probe_public_http("okx", settings, network_snapshot=saved)
    ws_result = probe_public_websocket("okx", settings, network_snapshot=saved)

    assert http_result["state"] == "REACHABLE"
    assert ws_result["state"] == "REACHABLE"
    assert captured["http_proxy"] == "http://saved.example:7897"
    assert captured["ws_proxy"] == "http://saved.example:7897"


def test_public_websocket_probe_classifies_subscription_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeConnection:
        async def recv_json(self) -> dict[str, object]:
            return {"event": "error"}

        async def send_json(self, payload: dict[str, object]) -> None:
            return None

        async def close(self) -> None:
            return None

    class FakeContext:
        async def __aenter__(self) -> FakeConnection:
            return FakeConnection()

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    class FakeConnector:
        def __init__(self, **kwargs: object) -> None:
            return None

        def connect(self, url: str) -> FakeContext:
            return FakeContext()

    monkeypatch.setattr(
        "backend.app.services.public_network_settings.WebsocketsJsonConnector",
        FakeConnector,
    )
    settings = Settings(
        app_name="probe",
        version="test",
        admin_username="admin",
        admin_password="password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
        history_data_path="data/history",
    )

    result = probe_public_websocket("binance", settings)

    assert result["state"] == "REJECTED"
    assert result["error"] == {
        "kind": "UPSTREAM_REJECTED",
        "message": "public WebSocket subscription was rejected",
    }
