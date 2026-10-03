"""Unit tests for FileSecretProvider."""

from adapters.standalone.file_secret_provider import FileSecretProvider


def test_save_and_get(tmp_path):
    p = FileSecretProvider(tmp_path / "exchange.json")
    assert not p.is_configured("binance")
    p.save("binance", "testkey12345678", "testsecret12345678")
    assert p.is_configured("binance")
    assert p.get_api_key("binance") == "testkey12345678"
    assert p.get_api_secret("binance") == "testsecret12345678"
    assert p.key_prefix("binance") == "test****"


def test_file_permissions(tmp_path):
    import os
    p = FileSecretProvider(tmp_path / "exchange.json")
    p.save("binance", "testkey12345678", "testsecret12345678")
    mode = os.stat(tmp_path / "exchange.json").st_mode & 0o777
    assert mode == 0o600


def test_delete(tmp_path):
    p = FileSecretProvider(tmp_path / "exchange.json")
    p.save("binance", "testkey12345678", "testsecret12345678")
    assert p.delete("binance") is True
    assert not p.is_configured("binance")
    assert p.delete("binance") is False


def test_rejects_short_credentials(tmp_path):
    import pytest
    p = FileSecretProvider(tmp_path / "exchange.json")
    with pytest.raises(ValueError):
        p.save("binance", "short", "testsecret12345678")
    with pytest.raises(ValueError):
        p.save("binance", "", "")


def test_env_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("CRYPTO_BINANCE_API_KEY", "envkey12345678")
    monkeypatch.setenv("CRYPTO_BINANCE_API_SECRET", "envsecret12345678")
    p = FileSecretProvider(tmp_path / "exchange.json")
    p.save("binance", "filekey12345678", "filesecret12345678")
    # env wins
    assert p.get_api_key("binance") == "envkey12345678"
