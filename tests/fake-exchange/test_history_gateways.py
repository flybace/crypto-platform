from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from adapters.venues.binance_history import BinanceSpotHistoryGateway
from adapters.venues.bybit_history import BybitSpotHistoryGateway
from adapters.venues.httpx_transport import HttpxJsonTransport
from adapters.venues.okx_history import OkxSpotHistoryGateway
from domain.candle import CandleInterval, HistoryQuery
from domain.market import MarketType
from ports.rest import PublicRestError


DAY = 86_400_000
START_MS = 1_767_240_000_000
START = datetime.fromtimestamp(START_MS / 1000, tz=UTC)


def query(venue_id: str, native_symbol: str) -> HistoryQuery:
    return HistoryQuery(
        venue_id=venue_id,
        market_type=MarketType.SPOT,
        instrument_key=f"{venue_id}:spot:BTC/USDT",
        native_symbol=native_symbol,
        interval=CandleInterval.DAY,
        start_at=START,
        end_at=START + timedelta(days=3),
    )


def binance_row(open_ms: int, close: str) -> list[Any]:
    return [open_ms, "100", "110", "90", close, "12", open_ms + DAY - 1, "1200", 10, "6", "600", "0"]


class ValueTransport:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get_json_value(self, path: str, params: dict[str, str]) -> Any:
        self.calls.append((path, dict(params)))
        return self.responses.pop(0)


def test_binance_history_paginates_forward_and_normalizes_rows() -> None:
    transport = ValueTransport(
        [
            [binance_row(START_MS, "101"), binance_row(START_MS + DAY, "102")],
            [binance_row(START_MS + 2 * DAY, "103")],
        ]
    )

    candles = BinanceSpotHistoryGateway(transport, page_limit=2).fetch_candles(query("binance", "BTCUSDT"))

    assert [str(candle.close) for candle in candles] == ["101", "102", "103"]
    assert transport.calls[0][0] == "/api/v3/klines"
    assert transport.calls[0][1]["startTime"] == str(START_MS)
    assert transport.calls[1][1]["startTime"] == str(START_MS + 2 * DAY)


def test_history_gateway_captures_successful_pages_for_raw_archiving() -> None:
    transport = ValueTransport(
        [[binance_row(START_MS, "101"), binance_row(START_MS + DAY, "102")], [binance_row(START_MS + 2 * DAY, "103")]]
    )
    gateway = BinanceSpotHistoryGateway(transport, page_limit=2)

    gateway.fetch_candles(query("binance", "BTCUSDT"))
    responses = gateway.drain_raw_responses()

    assert len(responses) == 2
    assert responses[0].path == "/api/v3/klines"
    assert responses[0].params["symbol"] == "BTCUSDT"


def test_history_gateway_uses_configured_candles_path() -> None:
    transport = ValueTransport([[binance_row(START_MS, "101")]])
    gateway = BinanceSpotHistoryGateway(
        transport,
        api_routes={"candles_path": "/v6/market/candles"},
    )

    gateway.fetch_candles(query("binance", "BTCUSDT"))

    assert transport.calls[0][0] == "/v6/market/candles"


def test_okx_history_paginates_backwards_with_after_cursor() -> None:
    rows = lambda open_ms, close: [str(open_ms), "100", "110", "90", close, "12", "0", "1200", "1"]
    transport = ValueTransport(
        [
            {"code": "0", "data": [rows(START_MS + 2 * DAY, "103"), rows(START_MS + DAY, "102")]},
            {"code": "0", "data": [rows(START_MS, "101")]},
        ]
    )

    candles = OkxSpotHistoryGateway(transport, page_limit=2).fetch_candles(query("okx", "BTC-USDT"))

    assert [str(candle.close) for candle in candles] == ["101", "102", "103"]
    assert transport.calls[0][1]["after"] == str(START_MS + 3 * DAY)
    assert transport.calls[1][1]["after"] == str(START_MS + DAY - 1)


def test_bybit_history_paginates_backwards_with_end_cursor() -> None:
    rows = lambda open_ms, close: [str(open_ms), "100", "110", "90", close, "12", "1200"]
    transport = ValueTransport(
        [
            {"retCode": 0, "result": {"list": [rows(START_MS + 2 * DAY, "103"), rows(START_MS + DAY, "102")]}},
            {"retCode": 0, "result": {"list": [rows(START_MS, "101")]}},
        ]
    )

    candles = BybitSpotHistoryGateway(transport, page_limit=2).fetch_candles(query("bybit", "BTCUSDT"))

    assert [str(candle.close) for candle in candles] == ["101", "102", "103"]
    assert transport.calls[0][1]["end"] == str(START_MS + 3 * DAY - 1)
    assert transport.calls[1][1]["end"] == str(START_MS + DAY - 1)
    assert transport.calls[0][1]["start"] == str(START_MS)


def test_httpx_history_transport_accepts_public_array_payload() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[[START_MS, "100", "110", "90", "105"]])

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.test")
    transport = HttpxJsonTransport("https://example.test", client=client)
    try:
        assert transport.get_json_value("/klines", {})[0][0] == START_MS
        try:
            transport.get_json("/klines", {})
        except PublicRestError as error:
            assert error.kind == "INVALID_JSON"
        else:
            raise AssertionError("array payload must not pass the object-only transport method")
    finally:
        client.close()
