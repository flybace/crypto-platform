"""Tests for Binance signed REST client and read-only account gateway."""

import pytest

from adapters.standalone.env_secret_provider import EnvSecretProvider
from adapters.venues.binance_account import BinanceReadOnlyAccountGateway
from adapters.venues.binance_signed import BinanceSignedRestClient, sign_query_string


class TestSignQueryString:
    def test_binance_doc_vector(self):
        # Example from Binance API documentation.
        query = (
            "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1"
            "&price=0.1&recvWindow=5000&timestamp=1499827319559"
        )
        secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
        assert sign_query_string(query, secret) == (
            "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"
        )

    def test_empty_secret_rejected(self):
        with pytest.raises(ValueError):
            sign_query_string("timestamp=1", "")

    def test_signature_is_hex(self):
        sig = sign_query_string("a=1", "secret")
        assert len(sig) == 64
        int(sig, 16)


class TestBinanceSignedRestClient:
    def test_rejects_empty_key(self):
        with pytest.raises(ValueError):
            BinanceSignedRestClient("https://api.binance.com", "", "secret")

    def test_rejects_empty_secret(self):
        with pytest.raises(ValueError):
            BinanceSignedRestClient("https://api.binance.com", "key", "")

    def test_sets_api_key_header(self):
        client = BinanceSignedRestClient(
            "https://api.binance.com", "mykey", "mysecret", trust_env=False
        )
        try:
            assert client._client.headers["X-MBX-APIKEY"] == "mykey"
        finally:
            client.close()


class FakeSignedClient:
    """Stand-in for BinanceSignedRestClient returning canned payloads."""

    def __init__(self, account_payload, orders_payload):
        self._account = account_payload
        self._orders = orders_payload

    def get_account(self):
        return self._account

    def get_open_orders(self, symbol=None):
        return self._orders

    def close(self):
        pass


class TestBinanceReadOnlyAccountGateway:
    def _gateway(self, balances, orders=()):
        gateway = BinanceReadOnlyAccountGateway.__new__(BinanceReadOnlyAccountGateway)
        gateway._client = FakeSignedClient({"balances": balances}, list(orders))
        return gateway

    def test_fetch_account_maps_balances(self):
        gateway = self._gateway(
            [
                {"asset": "BTC", "free": "0.5", "locked": "0.1"},
                {"asset": "USDT", "free": "1000", "locked": "0"},
                {"asset": "ETH", "free": "0", "locked": "0"},
            ],
            [{"orderId": 12345}, {"orderId": 67890}],
        )
        snapshot = gateway.fetch_account("default")
        assert snapshot.account_id == "default"
        assert snapshot.venue_id == "binance"
        by_asset = {b.asset: b for b in snapshot.balances}
        # Zero-total assets are skipped
        assert set(by_asset) == {"BTC", "USDT"}
        assert str(by_asset["BTC"].available) == "0.5"
        assert str(by_asset["BTC"].total) == "0.6"
        assert snapshot.open_order_ids == ("12345", "67890")

    def test_fetch_account_rejects_empty_id(self):
        gateway = self._gateway([])
        with pytest.raises(ValueError):
            gateway.fetch_account("  ")

    def test_fetch_account_skips_bad_rows(self):
        gateway = self._gateway(
            [
                {"asset": "", "free": "1", "locked": "0"},
                {"asset": "XRP", "free": "not-a-number", "locked": "0"},
                "not-a-dict",
                {"asset": "SOL", "free": "2", "locked": "3"},
            ]
        )
        snapshot = gateway.fetch_account("default")
        assert [b.asset for b in snapshot.balances] == ["SOL"]


class TestEnvSecretProvider:
    def test_unset_returns_none(self, monkeypatch):
        monkeypatch.delenv("CRYPTO_BINANCE_API_KEY", raising=False)
        monkeypatch.delenv("CRYPTO_BINANCE_API_SECRET", raising=False)
        provider = EnvSecretProvider()
        assert provider.get_api_key("binance") is None
        assert provider.get_api_secret("binance") is None
        assert provider.is_configured("binance") is False

    def test_configured_when_both_set(self, monkeypatch):
        monkeypatch.setenv("CRYPTO_BINANCE_API_KEY", "key123")
        monkeypatch.setenv("CRYPTO_BINANCE_API_SECRET", "secret123")
        provider = EnvSecretProvider()
        assert provider.get_api_key("binance") == "key123"
        assert provider.get_api_secret("binance") == "secret123"
        assert provider.is_configured("binance") is True

    def test_partial_config_is_not_configured(self, monkeypatch):
        monkeypatch.setenv("CRYPTO_BINANCE_API_KEY", "key123")
        monkeypatch.delenv("CRYPTO_BINANCE_API_SECRET", raising=False)
        provider = EnvSecretProvider()
        assert provider.is_configured("binance") is False
