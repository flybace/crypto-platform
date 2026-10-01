"""Map strategy signals to intents without bypassing risk or execution ports."""

from domain.strategy import StrategySignal
from domain.trading import OrderIntent, OrderType


def signal_to_order_intent(signal: StrategySignal, authorization_id: str, order_type: OrderType = OrderType.LIMIT) -> OrderIntent:
    return OrderIntent(
        request_id=signal.signal_id,
        account_id=signal.account_id,
        venue_id=signal.instrument.venue_id,
        instrument=signal.instrument,
        side=signal.side,
        order_type=order_type,
        quantity=signal.quantity,
        limit_price=signal.limit_price,
        strategy_id=signal.strategy.strategy_id,
        strategy_version=signal.strategy.version,
        authorization_id=authorization_id,
        generated_at=signal.generated_at,
        reference_price=signal.reference_price,
        max_slippage_bps=signal.max_slippage_bps,
    )
