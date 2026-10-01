from decimal import Decimal

from application.paper import PaperBroker
from domain.market import OrderBookSnapshot, PriceLevel
from domain.paper import PaperAccount, PaperOrderStatus
from domain.trading import OrderType, Side

from tests.helpers import make_instrument, make_intent, make_snapshot


def test_paper_broker_applies_sell_fill_and_quote_fee() -> None:
    instrument = make_instrument("okx")
    account = PaperAccount("paper-sell", {"BTC": "1", "USDT": "0"})
    broker = PaperBroker(account, fee_bps=Decimal("10"))
    order = broker.submit(
        make_intent(instrument, side=Side.SELL, quantity="0.5", limit_price="100.00"),
        make_snapshot(instrument, bid_price="100.50", ask_price="100.60", bid_quantity="0.2"),
        make_snapshot(instrument).received_timestamp,
    )

    assert order.status is PaperOrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == Decimal("0.2")
    assert order.remaining_quantity == Decimal("0.3")
    assert account.available("BTC") == Decimal("0.8")
    assert account.available("USDT") == Decimal("20.0799")


def test_ioc_without_marketable_liquidity_is_canceled() -> None:
    instrument = make_instrument()
    broker = PaperBroker(PaperAccount("paper", {"BTC": "1", "USDT": "0"}))
    order = broker.submit(
        make_intent(instrument, order_type=OrderType.LIMIT_IOC, limit_price="101.00"),
        make_snapshot(instrument, bid_price="100.00", ask_price="100.10"),
        make_snapshot(instrument).received_timestamp,
    )

    assert order.status is PaperOrderStatus.CANCELED
    assert order.reason == "LIMIT_NOT_MARKETABLE"


def test_paper_broker_consumes_multiple_l2_levels() -> None:
    instrument = make_instrument("okx")
    account = PaperAccount("paper-sell", {"BTC": "1", "USDT": "0"})
    broker = PaperBroker(account)
    market = OrderBookSnapshot(
        instrument=instrument,
        bids=(
            PriceLevel(Decimal("100.50"), Decimal("0.2")),
            PriceLevel(Decimal("100.40"), Decimal("0.4")),
        ),
        asks=(PriceLevel(Decimal("100.60"), Decimal("1")),),
        exchange_timestamp=make_snapshot(instrument).exchange_timestamp,
        received_timestamp=make_snapshot(instrument).received_timestamp,
        sequence=1,
    )

    order = broker.submit(
        make_intent(instrument, quantity="0.5", limit_price="100.00"),
        market,
        make_snapshot(instrument).received_timestamp,
    )

    assert order.status is PaperOrderStatus.FILLED
    assert order.filled_quantity == Decimal("0.5")
    assert len(order.fills) == 2
    assert order.fills[1].quantity == Decimal("0.3")


def test_market_order_is_rejected_in_initial_paper_broker() -> None:
    instrument = make_instrument()
    broker = PaperBroker(PaperAccount("paper", {"BTC": "1", "USDT": "1000"}))
    order = broker.submit(
        make_intent(instrument, side=Side.BUY, order_type=OrderType.MARKET, limit_price=None),
        make_snapshot(instrument),
        make_snapshot(instrument).received_timestamp,
    )

    assert order.status is PaperOrderStatus.REJECTED
    assert order.reason == "MARKET_ORDER_UNSUPPORTED"
