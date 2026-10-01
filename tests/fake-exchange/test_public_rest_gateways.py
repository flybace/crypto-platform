from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from adapters.venues.binance_public_rest import BinanceSpotPublicRestGateway
from adapters.venues.httpx_transport import HttpxJsonTransport
from adapters.venues.okx_public_rest import OkxSpotPublicRestGateway
from domain.market_status import MarketConnectionState
from ports.rest import PublicRestError

from tests.helpers import NOW, make_instrument


class FakeJsonTransport:
    def __init__(self, responses: Mapping[str, Mapping[str, object]]) -> None:
        self.responses = dict(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get_json(self, path: str, params: Mapping[str, str]) -> Mapping[str, object]:
        self.calls.append((path, dict(params)))
        return self.responses[path]


def test_binance_rest_gateway_fetches_instrument_and_depth_without_private_auth() -> None:
    transport = FakeJsonTransport(
        {
            "/api/v3/exchangeInfo": {
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "baseAsset": "BTC",
                        "quoteAsset": "USDT",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                            {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                            {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
                        ],
                    }
                ]
            },
            "/api/v3/depth": {
                "lastUpdateId": 123,
                "bids": [["100.00", "1.000"]],
                "asks": [["100.10", "2.000"]],
            },
        }
    )
    gateway = BinanceSpotPublicRestGateway(transport, clock=lambda: NOW)

    instrument = gateway.fetch_instrument("btcusdt")
    snapshot = gateway.fetch_snapshot(instrument)

    assert instrument.key == "binance:spot:BTC/USDT"
    assert snapshot.exchange_timestamp == NOW
    assert snapshot.sequence == 123
    assert gateway.status("BINANCE").state is MarketConnectionState.CONNECTED
    assert transport.calls == [
        ("/api/v3/exchangeInfo", {"symbol": "BTCUSDT"}),
        ("/api/v3/depth", {"symbol": "BTCUSDT", "limit": "100"}),
    ]


def test_okx_rest_gateway_normalizes_public_books_and_checks_api_code() -> None:
    transport = FakeJsonTransport(
        {
            "/api/v5/market/books": {
                "code": "0",
                "data": [
                    {
                        "ts": "1767268800000",
                        "seqId": "22",
                        "bids": [["100.0", "1", "0", "1"]],
                        "asks": [["100.1", "2", "0", "1"]],
                    }
                ],
            }
        }
    )
    gateway = OkxSpotPublicRestGateway(transport, depth_limit=20, clock=lambda: NOW)

    instrument = replace(make_instrument("okx"), native_symbol="BTC-USDT")
    snapshot = gateway.fetch_snapshot(instrument)

    assert snapshot.instrument.native_symbol == "BTC-USDT"
    assert snapshot.best_bid is not None
    assert snapshot.best_bid.price == Decimal("100.0")
    assert snapshot.sequence == 22
    assert transport.calls == [("/api/v5/market/books", {"instId": "BTC-USDT", "sz": "20"})]


def test_okx_rest_gateway_rejects_nonzero_api_code() -> None:
    transport = FakeJsonTransport({"/api/v5/market/books": {"code": "50001", "data": []}})
    gateway = OkxSpotPublicRestGateway(transport, clock=lambda: NOW)

    with pytest.raises(ValueError, match="error code"):
        gateway.fetch_snapshot(replace(make_instrument("okx"), native_symbol="BTC-USDT"))

    assert gateway.status("okx").state is MarketConnectionState.DEGRADED
    assert gateway.status("okx").reason == "INVALID_PAYLOAD"


def test_public_rest_gateway_uses_configured_api_paths() -> None:
    transport = FakeJsonTransport(
        {
            "/v6/instruments": {
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "baseAsset": "BTC",
                        "quoteAsset": "USDT",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                            {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                            {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
                        ],
                    }
                ]
            },
            "/v6/books": {
                "lastUpdateId": 123,
                "bids": [["100.00", "1.000"]],
                "asks": [["100.10", "2.000"]],
            },
        }
    )
    gateway = BinanceSpotPublicRestGateway(
        transport,
        api_routes={
            "instruments_path": "/v6/instruments",
            "order_book_path": "/v6/books",
        },
        clock=lambda: NOW,
    )

    instrument = gateway.fetch_instrument("BTCUSDT")
    gateway.fetch_snapshot(instrument)

    assert [path for path, _ in transport.calls] == ["/v6/instruments", "/v6/books"]


def test_public_rest_gateway_records_rate_limit_as_degraded() -> None:
    class RateLimitedTransport:
        def get_json(self, path: str, params: Mapping[str, str]) -> Mapping[str, object]:
            raise PublicRestError("RATE_LIMITED", "limited", status_code=429, retry_after_seconds=2)

    gateway = BinanceSpotPublicRestGateway(RateLimitedTransport(), clock=lambda: NOW)

    with pytest.raises(PublicRestError) as error:
        gateway.fetch_snapshot(make_instrument("binance"))

    assert error.value.kind == "RATE_LIMITED"
    assert error.value.retry_after_seconds == 2
    assert gateway.status("binance").state is MarketConnectionState.DEGRADED
    assert gateway.status("binance").reason == "RATE_LIMITED"


def test_httpx_transport_maps_http_status_without_returning_error_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/depth"
        return httpx.Response(429, headers={"Retry-After": "1.5"}, json={"secret": "not-propagated"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.test")
    transport = HttpxJsonTransport("https://example.test", client=client)

    try:
        with pytest.raises(PublicRestError) as error:
            transport.get_json("/depth", {"symbol": "BTCUSDT"})
    finally:
        client.close()

    assert error.value.kind == "RATE_LIMITED"
    assert error.value.status_code == 429
    assert error.value.retry_after_seconds == 1.5
    assert "not-propagated" not in str(error.value)


def test_httpx_transport_exposes_only_safe_success_response_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/json",
                "ETag": "abc",
                "Authorization": "must-not-propagate",
            },
            json={"ok": True},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.test")
    transport = HttpxJsonTransport("https://example.test", client=client)
    try:
        result = transport.get_json_value_with_metadata("/depth", {"symbol": "BTCUSDT"})
    finally:
        client.close()

    assert result.payload == {"ok": True}
    assert result.path == "/depth"
    assert result.headers == {"content-type": "application/json", "etag": "abc"}


def test_httpx_transport_passes_explicit_proxy_and_trust_env_to_client(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def close(self) -> None:
            return None

    monkeypatch.setattr(httpx, "Client", FakeClient)

    transport = HttpxJsonTransport(
        "https://example.test",
        trust_env=False,
        proxy="http://192.168.68.183:7897",
    )
    transport.close()

    assert captured["trust_env"] is False
    assert captured["proxy"] == "http://192.168.68.183:7897"


def test_httpx_transport_rejects_non_http_proxy_urls() -> None:
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        HttpxJsonTransport("https://example.test", proxy="socks5://127.0.0.1:1080")
