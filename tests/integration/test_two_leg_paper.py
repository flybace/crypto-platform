from datetime import timedelta
from decimal import Decimal

from application.paper import PaperBroker
from application.two_leg import TwoLegPaperExecutor
from domain.paper import PaperAccount
from domain.trading import Side
from domain.two_leg import TwoLegPlan, TwoLegState

from tests.helpers import NOW, make_instrument, make_intent, make_snapshot


def _plan() -> tuple[TwoLegPlan, object, object, PaperBroker, PaperBroker]:
    buy_instrument = make_instrument("binance")
    sell_instrument = make_instrument("okx")
    buy_intent = make_intent(buy_instrument, side=Side.BUY, quantity="0.5", limit_price="99.50")
    sell_intent = make_intent(sell_instrument, side=Side.SELL, quantity="0.5", limit_price="100.50")
    plan = TwoLegPlan(
        buy_intent=buy_intent,
        sell_intent=sell_intent,
        max_unhedged_quantity=Decimal("0.5"),
        max_unhedged_duration=timedelta(seconds=2),
    )
    buy_market = make_snapshot(buy_instrument, bid_price="99.40", ask_price="99.50")
    sell_market = make_snapshot(sell_instrument, bid_price="100.50", ask_price="100.60")
    buy_broker = PaperBroker(PaperAccount("buy", {"USDT": "1000"}), fee_bps=Decimal("10"))
    sell_broker = PaperBroker(PaperAccount("sell", {"BTC": "1"}), fee_bps=Decimal("10"))
    return plan, buy_market, sell_market, buy_broker, sell_broker


def test_two_leg_paper_execution_completes_and_reduces_both_inventories() -> None:
    plan, buy_market, sell_market, buy_broker, sell_broker = _plan()

    result = TwoLegPaperExecutor(buy_broker, sell_broker).execute(plan, buy_market, sell_market, NOW)

    assert result.state is TwoLegState.COMPLETED
    assert result.unhedged_quantity == Decimal("0")
    assert result.buy_order is not None and result.sell_order is not None
    assert result.transitions[-1] is TwoLegState.COMPLETED
    assert buy_broker.account.available("BTC") == Decimal("0.5")
    assert sell_broker.account.available("BTC") == Decimal("0.5")


def test_failed_sell_leg_is_explicitly_marked_for_hedge_and_review() -> None:
    plan, buy_market, sell_market, buy_broker, sell_broker = _plan()

    result = TwoLegPaperExecutor(buy_broker, sell_broker).execute(
        plan,
        buy_market,
        sell_market,
        NOW,
        fail_sell=True,
    )

    assert result.state is TwoLegState.HEDGE_REQUIRED
    assert result.reason == "SELL_LEG_FAILED"
    assert result.unhedged_quantity == Decimal("0.5")
    assert result.sell_order is None
    assert sell_broker.all() == ()


def test_partial_sell_leg_requires_reconciliation() -> None:
    plan, buy_market, _, buy_broker, sell_broker = _plan()
    partial_sell_market = make_snapshot(
        plan.sell_intent.instrument,
        bid_price="100.50",
        ask_price="100.60",
        bid_quantity="0.2",
    )

    result = TwoLegPaperExecutor(buy_broker, sell_broker).execute(
        plan,
        buy_market,
        partial_sell_market,
        NOW,
    )

    assert result.state is TwoLegState.RECONCILIATION_REQUIRED
    assert result.unhedged_quantity == Decimal("0.3")
