from decimal import Decimal

import pytest

from domain.market import Instrument, MarketType, OrderBookSnapshot, PriceLevel

from tests.helpers import NOW, make_instrument, make_snapshot


def test_instrument_has_stable_venue_scoped_key_and_step_normalization() -> None:
    instrument = make_instrument()

    assert instrument.canonical_symbol == "BTC/USDT"
    assert instrument.key == "binance:spot:BTC/USDT"
    assert instrument.normalize_price("100.019") == Decimal("100.01")
    assert instrument.normalize_quantity("0.1234") == Decimal("0.123")


def test_order_book_exposes_best_levels_and_rejects_crossed_data() -> None:
    snapshot = make_snapshot()

    assert snapshot.best_bid == PriceLevel(Decimal("100.00"), Decimal("1"))
    assert snapshot.best_ask == PriceLevel(Decimal("100.10"), Decimal("1"))
    assert snapshot.age_seconds(NOW) == 1

    with pytest.raises(ValueError, match="must not be crossed"):
        OrderBookSnapshot(
            instrument=make_instrument(),
            bids=(PriceLevel(Decimal("100"), Decimal("1")),),
            asks=(PriceLevel(Decimal("100"), Decimal("1")),),
            exchange_timestamp=NOW,
            received_timestamp=NOW,
            sequence=1,
        )


def test_instrument_rejects_non_positive_exchange_filters() -> None:
    with pytest.raises(ValueError, match="price_tick"):
        Instrument(
            venue_id="binance",
            market_type=MarketType.SPOT,
            base_asset="BTC",
            quote_asset="USDT",
            native_symbol="BTCUSDT",
            price_tick=Decimal("0"),
            quantity_step=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
            min_notional=Decimal("10"),
        )
