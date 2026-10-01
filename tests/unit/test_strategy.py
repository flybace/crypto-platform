from decimal import Decimal

import pytest

from application.strategy import signal_to_order_intent
from domain.strategy import InventorySellStrategy, StrategyMode, StrategySpec
from domain.trading import Side

from tests.helpers import NOW, make_instrument


def test_inventory_strategy_emits_signal_then_maps_to_order_intent() -> None:
    strategy = InventorySellStrategy(StrategySpec("inventory-sell", "1.0.0", StrategyMode.PAPER))
    signal = strategy.generate(
        signal_id="signal-001",
        account_id="paper-001",
        instrument=make_instrument("binance"),
        quantity=Decimal("0.1"),
        limit_price=Decimal("100"),
        reference_price=Decimal("100"),
        max_slippage_bps=Decimal("20"),
        generated_at=NOW,
    )
    intent = signal_to_order_intent(signal, "auth-paper")

    assert signal.side is Side.SELL
    assert intent.request_id == "signal-001"
    assert intent.strategy_version == "1.0.0"
    assert intent.authorization_id == "auth-paper"


def test_observe_strategy_cannot_emit_execution_signal() -> None:
    strategy = InventorySellStrategy(StrategySpec("observer", "1.0.0", StrategyMode.OBSERVE))

    with pytest.raises(ValueError, match="cannot emit"):
        strategy.generate(
            signal_id="signal-002",
            account_id="paper-001",
            instrument=make_instrument(),
            quantity=Decimal("0.1"),
            limit_price=Decimal("100"),
            reference_price=Decimal("100"),
            max_slippage_bps=Decimal("20"),
            generated_at=NOW,
        )
