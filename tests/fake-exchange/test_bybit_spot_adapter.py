from dataclasses import replace
from decimal import Decimal

from adapters.venues.bybit_public_rest import BybitSpotPublicRestGateway
from adapters.venues.bybit_spot import BybitSpotPublicAdapter
from domain.market import OrderBookSnapshot
from domain.market_events import OrderBookDelta
from tests.helpers import NOW, make_instrument


def _metadata() -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "baseCoin": "BTC",
        "quoteCoin": "USDT",
        "priceFilter": {"tickSize": "0.1"},
        "lotSizeFilter": {
            "basePrecision": "0.000001",
            "qtyStep": "0.000001",
            "minOrderQty": "0.00001",
            "minOrderAmt": "5",
        },
    }


def _book_payload() -> dict[str, object]:
    return {
        "retCode": 0,
        "time": 1767268800000,
        "result": {
            "s": "BTCUSDT",
            "u": 100,
            "seq": 900,
            "b": [["100.0", "1"], ["99.0", "2"]],
            "a": [["100.1", "3"], ["101.0", "4"]],
        },
    }


def test_bybit_payloads_share_the_canonical_snapshot_and_delta_boundary() -> None:
    adapter = BybitSpotPublicAdapter()
    assert adapter.VENUE.public_ws_base_url == "wss://stream.bybit.kz/v5/public/spot"
    instrument = adapter.instrument_from_metadata(_metadata())
    snapshot = adapter.normalize_order_book(_book_payload(), instrument, NOW)
    delta = adapter.normalize_order_book_event(
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "delta",
            "ts": 1767268800100,
            "data": {
                "s": "BTCUSDT",
                "u": 101,
                "pu": 100,
                "b": [["100.0", "0"]],
                "a": [["100.2", "1"]],
            },
        },
        instrument,
        NOW,
    )

    assert instrument.key == "bybit:spot:BTC/USDT"
    assert instrument.min_notional == Decimal("5")
    assert isinstance(snapshot, OrderBookSnapshot)
    assert snapshot.best_bid is not None
    assert snapshot.best_bid.price == Decimal("100.0")
    assert isinstance(delta, OrderBookDelta)
    assert delta.first_sequence == 101
    assert delta.previous_sequence == 100
    assert adapter.orderbook_subscription(instrument) == {
        "op": "subscribe",
        "args": ["orderbook.50.BTCUSDT"],
    }
    assert adapter.normalize_order_book_event(
        {"op": "subscribe", "success": True},
        instrument,
        NOW,
    ) is None


def test_bybit_delta_accepts_the_live_spot_shape_without_previous_update_id() -> None:
    adapter = BybitSpotPublicAdapter()
    instrument = adapter.instrument_from_metadata(_metadata())

    delta = adapter.normalize_order_book_event(
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "delta",
            "ts": 1767268800100,
            "data": {
                "s": "BTCUSDT",
                "u": 101,
                "seq": 9001,
                "b": [["100.0", "0"]],
                "a": [],
            },
        },
        instrument,
        NOW,
    )

    assert isinstance(delta, OrderBookDelta)
    assert delta.last_sequence == 101
    assert delta.previous_sequence is None


class FakeJsonTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get_json(self, path: str, params: dict[str, str]) -> dict[str, object]:
        self.calls.append((path, params))
        if path.endswith("instruments-info"):
            return {"retCode": 0, "result": {"list": [_metadata()]}}
        return _book_payload()


def test_bybit_rest_gateway_fetches_metadata_and_orderbook() -> None:
    transport = FakeJsonTransport()
    gateway = BybitSpotPublicRestGateway(transport, depth_limit=50, clock=lambda: NOW)

    instrument = gateway.fetch_instrument("btcusdt")
    snapshot = gateway.fetch_snapshot(replace(instrument, native_symbol="BTCUSDT"))

    assert snapshot.sequence == 100
    assert transport.calls == [
        (
            "/v5/market/instruments-info",
            {"category": "spot", "symbol": "BTCUSDT"},
        ),
        (
            "/v5/market/orderbook",
            {"category": "spot", "symbol": "BTCUSDT", "limit": "50"},
        ),
    ]
