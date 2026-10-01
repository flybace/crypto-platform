from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from backend.app.settings import Settings


def make_settings() -> Settings:
    return Settings(
        app_name="Crypto Test App",
        version="test",
        admin_username="admin",
        admin_password="local-password",
        session_secret="test-session-secret-with-more-than-24-chars",
        token_ttl_seconds=3600,
        execution_mode="DISABLED",
        allowed_origins=("http://127.0.0.1:4191",),
        dev_mode=True,
    )


def test_backend_login_and_authenticated_market_overview() -> None:
    client = TestClient(create_app(make_settings()))

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "version": "test",
        "execution_mode": "DISABLED",
        "auth": "enabled",
    }

    unauthenticated = client.get("/api/v1/market/overview")
    assert unauthenticated.status_code == 401

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "local-password"},
    )
    assert login.status_code == 200
    body = login.json()
    assert body["token_type"] == "bearer"
    assert body["user"] == {"username": "admin", "display_name": "admin", "role": "admin"}

    headers = {"Authorization": f"Bearer {body['access_token']}"}
    me = client.get("/api/v1/auth/me", headers=headers)
    overview = client.get("/api/v1/market/overview", headers=headers)
    assert me.status_code == 200
    assert overview.status_code == 200
    assert overview.json()["execution_mode"] == "DISABLED"
    assert [item["venue_id"] for item in overview.json()["venues"]] == ["binance", "okx", "bybit"]


def test_backend_rejects_wrong_password_and_invalid_token() -> None:
    client = TestClient(create_app(make_settings()))

    wrong_password = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    invalid_token = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer forged-token"},
    )

    assert wrong_password.status_code == 401
    assert invalid_token.status_code == 401


def test_settings_fail_closed_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRYPTO_DEV_MODE", raising=False)
    monkeypatch.delenv("CRYPTO_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("CRYPTO_SESSION_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="CRYPTO_ADMIN_PASSWORD"):
        Settings.from_env()


def test_settings_reads_a_bounded_task_log_retention_period(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_DEV_MODE", "true")
    monkeypatch.setenv("CRYPTO_TASK_LOG_RETENTION_DAYS", "42")

    settings = Settings.from_env()

    assert settings.task_log_retention_days == 42
    monkeypatch.setenv("CRYPTO_TASK_LOG_RETENTION_DAYS", "3651")
    with pytest.raises(RuntimeError, match="CRYPTO_TASK_LOG_RETENTION_DAYS"):
        Settings.from_env()
