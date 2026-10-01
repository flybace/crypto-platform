from decimal import Decimal

from application.backtest import PaperBacktestRunner
from application.paper import PaperBroker
from domain.paper import PaperAccount
from domain.trading import Side

from tests.helpers import make_instrument, make_intent, make_snapshot


def test_paper_backtest_replays_intents_and_returns_a_reproducible_summary() -> None:
    instrument = make_instrument("binance")
    market = make_snapshot(instrument, bid_price="100.50", ask_price="100.60")
    broker = PaperBroker(PaperAccount("paper", {"BTC": "1", "USDT": "0"}), fee_bps=Decimal("10"))
    intent = make_intent(instrument, side=Side.SELL, quantity="0.5", limit_price="100.00")

    result = PaperBacktestRunner(broker).run(
        run_id="backtest-001",
        dataset_id="dataset-001",
        intents=(intent,),
        markets={instrument.key: market},
    )

    assert result.submitted_orders == 1
    assert result.filled_orders == 1
    assert result.filled_quantity == Decimal("0.5")
    assert result.fees_quote == Decimal("0.05025")
    assert result.final_balances["BTC"] == Decimal("0.5")
