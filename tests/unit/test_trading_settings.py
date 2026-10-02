"""Tests for the explicit auto-trading toggle store."""

import json

from backend.app.services.trading_settings import TradingSettingsStore


def test_defaults_to_disabled(tmp_path):
    store = TradingSettingsStore(tmp_path / "trading-settings.json")
    assert store.is_enabled() is False


def test_set_enabled_persists(tmp_path):
    path = tmp_path / "trading-settings.json"
    store = TradingSettingsStore(path)
    assert store.set_enabled(True) is True
    assert store.is_enabled() is True
    # New instance reads the persisted file
    assert TradingSettingsStore(path).is_enabled() is True
    assert store.set_enabled(False) is False
    assert TradingSettingsStore(path).is_enabled() is False


def test_corrupt_file_falls_back_to_disabled(tmp_path):
    path = tmp_path / "trading-settings.json"
    path.write_text("not-json", encoding="utf-8")
    assert TradingSettingsStore(path).is_enabled() is False


def test_env_fallback(tmp_path, monkeypatch):
    path = tmp_path / "trading-settings.json"
    monkeypatch.setenv("CRYPTO_AUTO_TRADING_ENABLED", "true")
    assert TradingSettingsStore(path).is_enabled() is True
    monkeypatch.setenv("CRYPTO_AUTO_TRADING_ENABLED", "0")
    assert TradingSettingsStore(path).is_enabled() is False
    # Explicit file value wins over env
    TradingSettingsStore(path).set_enabled(True)
    monkeypatch.setenv("CRYPTO_AUTO_TRADING_ENABLED", "0")
    assert TradingSettingsStore(path).is_enabled() is True


def test_stored_json_shape(tmp_path):
    path = tmp_path / "trading-settings.json"
    TradingSettingsStore(path).set_enabled(True)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"auto_trading_enabled": True}
