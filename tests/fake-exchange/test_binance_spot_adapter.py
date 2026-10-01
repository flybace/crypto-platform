from decimal import Decimal

import pytest

from adapters.venues.binance_spot import BinanceSpotPublicAdapter
from domain.market import MarketType

from tests.helpers import NOW, make_instrument


def _symbol_payload() -> dict:
    return {
        "symbol": "BTCUSDT",
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
        ],
    }


def test_binance_symbol_and_order_book_payload_are_normalized() -> None:
    adapter = BinanceSpotPublicAdapter()
    instrument = adapter.instrument_from_exchange_info(_symbol_payload())
    snapshot = adapter.normalize_order_book(
        {
            "lastUpdateId": 123,
            "E": 1767268800000,
            "bids": [["100.00", "1.000"]],
            "asks": [["100.10", "2.000"]],
        },
        instrument,
        NOW,
    )

    assert instrument.key == "binance:spot:BTC/USDT"
    assert instrument.market_type is MarketType.SPOT
    assert snapshot.sequence == 123
    assert snapshot.best_bid is not None
    assert snapshot.best_bid.price == Decimal("100.00")


def test_binance_adapter_rejects_missing_notional_filter() -> None:
    payload = _symbol_payload()
    payload["filters"] = payload["filters"][:2]

    with pytest.raises(ValueError, match="notional filter"):
        BinanceSpotPublicAdapter().instrument_from_exchange_info(payload)


def test_binance_depth_event_supports_combined_stream_and_sequence_metadata() -> None:
    adapter = BinanceSpotPublicAdapter()
    instrument = make_instrument()

    delta = adapter.normalize_depth_event(
        {
            "stream": "btcusdt@depth@100ms",
            "data": {
                "e": "depthUpdate",
                "E": 1767268800000,
                "s": "BTCUSDT",
                "U": 124,
                "u": 126,
                "pu": 123,
                "b": [["100.00", "0.8"]],
                "a": [["100.10", "0"]],
            },
        },
        instrument,
        NOW,
    )

    assert delta.first_sequence == 124
    assert delta.last_sequence == 126
    assert delta.previous_sequence == 123
    assert delta.bids[0].quantity == Decimal("0.8")
    assert adapter.depth_stream_url(instrument, 100).endswith("/ws/btcusdt@depth@100ms")
    assert adapter.depth_stream_url(
        instrument,
        100,
        base_url="wss://stream.example.test/custom/",
    ) == "wss://stream.example.test/custom/ws/btcusdt@depth@100ms"


def test_binance_public_endpoints_use_the_dedicated_market_data_hosts() -> None:
    adapter = BinanceSpotPublicAdapter()

    assert adapter.VENUE.public_rest_base_url == "https://data-api.binance.vision"
    assert adapter.VENUE.public_ws_base_url == "wss://data-stream.binance.vision"


def test_binance_depth_event_rejects_a_different_symbol() -> None:
    with pytest.raises(ValueError, match="symbol"):
        BinanceSpotPublicAdapter().normalize_depth_event(
            {
                "e": "depthUpdate",
                "E": 1767268800000,
                "s": "ETHUSDT",
                "U": 1,
                "u": 1,
                "b": [],
                "a": [],
            },
            make_instrument(),
            NOW,
        )
