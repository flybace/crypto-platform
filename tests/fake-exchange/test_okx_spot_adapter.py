from dataclasses import replace
from decimal import Decimal

from adapters.venues.okx_spot import OkxSpotPublicAdapter
from domain.market import OrderBookSnapshot
from domain.market_events import OrderBookDelta
from tests.helpers import NOW
from tests.helpers import make_instrument


def test_okx_spot_payload_maps_to_the_same_canonical_model() -> None:
    adapter = OkxSpotPublicAdapter()
    instrument = adapter.instrument_from_metadata(
        {
            "instId": "BTC-USDT",
            "tickSz": "0.1",
            "lotSz": "0.001",
            "minSz": "0.001",
            "minNotional": "10",
        }
    )
    snapshot = adapter.normalize_order_book(
        {
            "data": [
                {
                    "ts": "1767268800000",
                    "seqId": "22",
                    "bids": [["100.0", "1", "0", "1"]],
                    "asks": [["100.1", "2", "0", "1"]],
                }
            ]
        },
        instrument,
        NOW,
    )

    assert instrument.key == "okx:spot:BTC/USDT"
    assert snapshot.sequence == 22
    assert snapshot.best_bid is not None
    assert snapshot.best_bid.price == Decimal("100.0")


def test_okx_public_rest_base_url_is_the_host_root_for_full_api_paths() -> None:
    assert OkxSpotPublicAdapter.VENUE.public_rest_base_url == "https://www.okx.com"


def test_okx_books_snapshot_and_update_share_a_normalized_event_boundary() -> None:
    adapter = OkxSpotPublicAdapter()
    instrument = replace(make_instrument("okx"), native_symbol="BTC-USDT")

    snapshot = adapter.normalize_books_event(
        {
            "arg": {"channel": "books", "instId": "BTC-USDT"},
            "action": "snapshot",
            "data": [
                {
                    "ts": "1767268800000",
                    "seqId": "100",
                    "bids": [["100.0", "1", "0", "1"]],
                    "asks": [["100.1", "2", "0", "1"]],
                }
            ],
        },
        instrument,
        NOW,
    )
    delta = adapter.normalize_books_event(
        {
            "arg": {"channel": "books", "instId": "BTC-USDT"},
            "action": "update",
            "data": [
                {
                    "ts": "1767268800100",
                    "prevSeqId": "100",
                    "seqId": "101",
                    "bids": [["100.0", "0", "0", "0"]],
                    "asks": [["100.2", "1", "0", "1"]],
                }
            ],
        },
        instrument,
        NOW,
    )

    assert isinstance(snapshot, OrderBookSnapshot)
    assert snapshot.sequence == 100
    assert isinstance(delta, OrderBookDelta)
    assert delta.previous_sequence == 100
    assert delta.first_sequence == 101
    assert adapter.books_subscription(instrument) == {
        "op": "subscribe",
        "args": [{"channel": "books", "instId": "BTC-USDT"}],
    }

    assert (
        adapter.normalize_books_event(
            {"event": "subscribe", "arg": {"channel": "books", "instId": "BTC-USDT"}},
            instrument,
            NOW,
        )
        is None
    )
