from decimal import Decimal

from application.opportunity import OpportunityScanner
from domain.opportunity import CostModel, FeeSchedule, OpportunityState

from tests.helpers import NOW, make_instrument, make_snapshot


def test_two_venue_replay_can_produce_a_cost_aware_opportunity() -> None:
    buy = make_instrument("binance")
    sell = make_instrument("okx")
    result = OpportunityScanner().scan(
        make_snapshot(buy, bid_price="99.0", ask_price="99.5"),
        make_snapshot(sell, bid_price="100.5", ask_price="101.0"),
        FeeSchedule("binance", Decimal("5"), Decimal("5"), NOW),
        FeeSchedule("okx", Decimal("5"), Decimal("5"), NOW),
        CostModel(expected_slippage_bps=Decimal("2")),
        NOW,
    )

    assert result.state is OpportunityState.VALIDATED
    assert result.net_edge_quote > 0
