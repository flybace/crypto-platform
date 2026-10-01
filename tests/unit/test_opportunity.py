from dataclasses import replace
from decimal import Decimal

from application.opportunity import OpportunityScanner
from domain.opportunity import CostModel, FeeSchedule, OpportunityState

from tests.helpers import NOW, make_instrument, make_snapshot


def _fees(venue_id: str) -> FeeSchedule:
    return FeeSchedule(venue_id, Decimal("10"), Decimal("10"), NOW)


def test_scanner_uses_depth_and_deducts_explicit_costs() -> None:
    buy_instrument = make_instrument("binance")
    sell_instrument = make_instrument("okx")
    buy_market = make_snapshot(buy_instrument, bid_price="99.40", ask_price="99.50")
    sell_market = make_snapshot(sell_instrument, bid_price="100.50", ask_price="100.60", bid_quantity="0.5")

    opportunity = OpportunityScanner().scan(
        buy_market,
        sell_market,
        _fees("binance"),
        _fees("okx"),
        CostModel(expected_slippage_bps=Decimal("5")),
        NOW,
    )

    assert opportunity.state is OpportunityState.VALIDATED
    assert opportunity.quantity == Decimal("0.5")
    assert opportunity.gross_edge_quote == Decimal("0.5")
    assert opportunity.net_edge_quote == Decimal("0.35")


def test_scanner_blocks_when_costs_exceed_gross_edge() -> None:
    buy_market = make_snapshot(make_instrument("binance"), bid_price="99.99", ask_price="100.00")
    sell_market = make_snapshot(make_instrument("okx"), bid_price="100.01", ask_price="100.02")

    opportunity = OpportunityScanner().scan(
        buy_market,
        sell_market,
        _fees("binance"),
        _fees("okx"),
        CostModel(failure_risk_buffer_quote=Decimal("1")),
        NOW,
    )

    assert opportunity.state is OpportunityState.BLOCKED
    assert opportunity.blocking_reason == "NET_EDGE_BELOW_ZERO"
    assert opportunity.net_edge_quote < 0


def test_scanner_blocks_stale_or_mismatched_markets() -> None:
    buy_market = make_snapshot(make_instrument("binance"), age_seconds=3)
    mismatched = make_snapshot(make_instrument("okx"))

    stale = OpportunityScanner().scan(
        buy_market,
        mismatched,
        _fees("binance"),
        _fees("okx"),
        CostModel(),
        NOW,
    )
    different_symbol = make_snapshot(
        replace(make_instrument("okx"), base_asset="ETH", native_symbol="ETHUSDT")
    )
    other = OpportunityScanner().scan(
        make_snapshot(make_instrument("binance")),
        different_symbol,
        _fees("binance"),
        _fees("okx"),
        CostModel(),
        NOW,
    )

    assert stale.blocking_reason == "STALE_MARKET_DATA"
    assert other.blocking_reason == "INSTRUMENT_MISMATCH"
