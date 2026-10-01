from datetime import timedelta

from application.risk_precheck import SellOnlyRiskPrechecker
from domain.trading import Side

from tests.helpers import NOW, make_context, make_instrument, make_intent, make_limits, make_snapshot


def test_valid_sell_intent_passes_all_m0_gates() -> None:
    decision = SellOnlyRiskPrechecker().evaluate(make_intent(), make_limits(), make_context())

    assert decision.allowed is True
    assert decision.reasons == ()


def test_disabled_mode_fails_closed() -> None:
    decision = SellOnlyRiskPrechecker().evaluate(
        make_intent(),
        make_limits(enabled=False),
        make_context(),
    )

    assert decision.allowed is False
    assert "TRADING_DISABLED" in decision.reasons


def test_buy_intent_is_rejected_without_reaching_execution() -> None:
    decision = SellOnlyRiskPrechecker().evaluate(
        make_intent(side=Side.BUY),
        make_limits(),
        make_context(),
    )

    assert decision.allowed is False
    assert "BUY_NOT_ALLOWED" in decision.reasons


def test_stale_market_and_unknown_order_block_new_sell() -> None:
    context = make_context(
        snapshot=make_snapshot(age_seconds=3),
        unknown_order_count=1,
    )

    decision = SellOnlyRiskPrechecker().evaluate(make_intent(), make_limits(), context)

    assert decision.allowed is False
    assert {"MARKET_DATA_STALE", "UNKNOWN_ORDER_PRESENT"}.issubset(decision.reasons)


def test_market_context_for_another_instrument_blocks_new_sell() -> None:
    other_instrument = make_instrument("okx")
    other_snapshot = make_snapshot(other_instrument, bid_price="200.00", ask_price="200.10")
    context = make_context(snapshot=other_snapshot)
    intent = make_intent()

    decision = SellOnlyRiskPrechecker().evaluate(intent, make_limits(), context)

    assert decision.allowed is False
    assert "MARKET_INSTRUMENT_MISMATCH" in decision.reasons


def test_missing_limits_are_not_inferred_as_safe() -> None:
    decision = SellOnlyRiskPrechecker().evaluate(
        make_intent(),
        make_limits(
            max_order_notional_quote="0",
            max_daily_sell_notional_quote="0",
            max_daily_sell_ratio="0",
            min_base_reserve=None,
            max_slippage_bps="0",
        ),
        make_context(),
    )

    assert decision.allowed is False
    assert "ORDER_LIMIT_UNCONFIGURED" in decision.reasons
    assert "DAILY_LIMIT_UNCONFIGURED" in decision.reasons
    assert "DAILY_RATIO_UNCONFIGURED" in decision.reasons
    assert "BASE_RESERVE_UNCONFIGURED" in decision.reasons
    assert "SLIPPAGE_LIMIT_UNCONFIGURED" in decision.reasons


def test_recent_order_frequency_blocks_within_one_minute() -> None:
    context = make_context(recent_order_timestamps=(NOW - timedelta(seconds=30),))

    decision = SellOnlyRiskPrechecker().evaluate(make_intent(), make_limits(), context)

    assert decision.allowed is False
    assert "ORDER_FREQUENCY_LIMIT" in decision.reasons
