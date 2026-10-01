from datetime import timedelta
from decimal import Decimal

from application.order_book import OrderBookReconstructor, SequencePolicy
from domain.market_events import OrderBookDelta, PriceLevelUpdate

from tests.helpers import NOW, make_instrument, make_snapshot


def _delta(
    instrument,
    *,
    first: int,
    last: int,
    previous: int | None = None,
    bid_updates: tuple[tuple[str, str], ...] = (),
    ask_updates: tuple[tuple[str, str], ...] = (),
    received_at=NOW,
) -> OrderBookDelta:
    return OrderBookDelta(
        instrument=instrument,
        bids=tuple(PriceLevelUpdate(price, quantity) for price, quantity in bid_updates),
        asks=tuple(PriceLevelUpdate(price, quantity) for price, quantity in ask_updates),
        exchange_timestamp=received_at,
        received_timestamp=received_at,
        first_sequence=first,
        last_sequence=last,
        previous_sequence=previous,
    )


def test_binance_range_reconstructor_applies_updates_and_removes_zero_levels() -> None:
    instrument = make_instrument()
    reconstructor = OrderBookReconstructor(instrument, sequence_policy=SequencePolicy.RANGE)
    assert reconstructor.seed(make_snapshot(instrument, sequence=123), NOW).accepted is True

    result = reconstructor.apply(
        _delta(
            instrument,
            first=124,
            last=126,
            previous=120,
            bid_updates=(("100.00", "0.5"), ("99.90", "2")),
            ask_updates=(("100.10", "0"), ("100.20", "1")),
        ),
        NOW,
    )

    assert result.accepted is True
    assert result.snapshot is not None
    assert result.snapshot.sequence == 126
    assert result.snapshot.best_bid is not None
    assert result.snapshot.best_bid.quantity == Decimal("0.5")
    assert result.snapshot.best_ask is not None
    assert result.snapshot.best_ask.price == Decimal("100.20")

    next_result = reconstructor.apply(
        _delta(instrument, first=127, last=127, previous=126, bid_updates=(("99.90", "0"),)),
        NOW,
    )
    assert next_result.accepted is True


def test_reconstructor_rejects_gap_duplicate_stale_and_invalid_books_without_mutating_state() -> None:
    instrument = make_instrument()
    reconstructor = OrderBookReconstructor(instrument, sequence_policy=SequencePolicy.RANGE)
    seed = make_snapshot(instrument, sequence=10)
    reconstructor.seed(seed, NOW)

    gap = reconstructor.apply(_delta(instrument, first=12, last=12), NOW)
    duplicate = reconstructor.apply(_delta(instrument, first=10, last=10), NOW)
    crossed = reconstructor.apply(
        _delta(instrument, first=11, last=11, bid_updates=(("101", "1"),)),
        NOW,
    )

    assert gap.reason == "SEQUENCE_GAP"
    assert duplicate.reason == "DUPLICATE_OR_STALE"
    assert crossed.reason == "INVALID_BOOK"
    assert reconstructor.snapshot == seed


def test_previous_id_policy_requires_exact_okx_predecessor() -> None:
    instrument = make_instrument("okx")
    reconstructor = OrderBookReconstructor(instrument, sequence_policy=SequencePolicy.PREVIOUS_ID)
    seed = make_snapshot(instrument, sequence=20)
    reconstructor.seed(seed, NOW)

    mismatch = reconstructor.apply(_delta(instrument, first=22, last=22, previous=21), NOW)
    accepted = reconstructor.apply(
        _delta(instrument, first=21, last=21, previous=20, ask_updates=(("100.10", "0.8"),)),
        NOW,
    )

    assert mismatch.reason == "SEQUENCE_GAP"
    assert accepted.accepted is True
    assert accepted.snapshot is not None
    assert accepted.snapshot.sequence == 21


def test_reconstructor_rejects_stale_and_requires_a_snapshot() -> None:
    instrument = make_instrument()
    reconstructor = OrderBookReconstructor(instrument, sequence_policy=SequencePolicy.RANGE)
    delta = _delta(instrument, first=1, last=1)

    assert reconstructor.apply(delta, NOW).reason == "SNAPSHOT_REQUIRED"
    stale = reconstructor.seed(make_snapshot(instrument, age_seconds=3), NOW)
    assert stale.reason == "MARKET_DATA_STALE"
    future = reconstructor.seed(make_snapshot(instrument, age_seconds=-1), NOW)
    assert future.reason == "MARKET_TIME_INVALID"
