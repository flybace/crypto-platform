"""Fail-closed service-side checks for the first real execution mode."""

from datetime import timedelta
from decimal import Decimal

from domain.market import MarketType
from domain.risk import RiskContext, RiskDecision, RiskLimits
from domain.trading import OrderIntent, OrderType, Side


class SellOnlyRiskPrechecker:
    """Evaluate every hard gate before an intent can reach an execution port."""

    def evaluate(self, intent: OrderIntent, limits: RiskLimits, context: RiskContext) -> RiskDecision:
        reasons: list[str] = []
        instrument = intent.instrument

        if not limits.enabled or limits.mode.value == "DISABLED":
            reasons.append("TRADING_DISABLED")
        if limits.mode.value != "SELL_ONLY":
            reasons.append("MODE_NOT_SUPPORTED")
        if intent.side is not Side.SELL:
            reasons.append("BUY_NOT_ALLOWED")
        if instrument.market_type is not MarketType.SPOT:
            reasons.append("SPOT_ONLY")
        if context.market.instrument != instrument:
            reasons.append("MARKET_INSTRUMENT_MISMATCH")
        if intent.venue_id not in limits.venue_allowlist:
            reasons.append("VENUE_NOT_ALLOWLISTED")
        if instrument.key not in limits.instrument_allowlist and instrument.canonical_symbol not in limits.instrument_allowlist:
            reasons.append("INSTRUMENT_NOT_ALLOWLISTED")
        if instrument.quote_asset not in limits.quote_asset_allowlist:
            reasons.append("QUOTE_ASSET_NOT_ALLOWLISTED")
        if intent.order_type is OrderType.MARKET and not limits.allow_market_order:
            reasons.append("MARKET_ORDER_NOT_ALLOWED")
        if intent.order_type is not OrderType.MARKET:
            if intent.limit_price is None:
                reasons.append("LIMIT_PRICE_REQUIRED")
            elif instrument.normalize_price(intent.limit_price) != intent.limit_price:
                reasons.append("PRICE_STEP_INVALID")
        if instrument.normalize_quantity(intent.quantity) != intent.quantity:
            reasons.append("QUANTITY_STEP_INVALID")
        if intent.quantity < instrument.min_quantity:
            reasons.append("MIN_QUANTITY_BREACH")

        if context.balance.asset != instrument.base_asset:
            reasons.append("BALANCE_ASSET_MISMATCH")
        if context.unknown_order_count > 0:
            reasons.append("UNKNOWN_ORDER_PRESENT")
        if context.open_order_count >= limits.max_open_orders:
            reasons.append("OPEN_ORDER_LIMIT")

        best_bid = context.market.best_bid
        if best_bid is None:
            reasons.append("NO_BID_LIQUIDITY")
        elif best_bid.quantity < intent.quantity:
            reasons.append("BID_DEPTH_INSUFFICIENT")

        age_seconds = context.market.age_seconds(context.now)
        if age_seconds < 0:
            reasons.append("MARKET_TIME_INVALID")
        elif age_seconds > limits.max_market_age_seconds:
            reasons.append("MARKET_DATA_STALE")

        if limits.min_base_reserve is None:
            reasons.append("BASE_RESERVE_UNCONFIGURED")
        else:
            remaining = context.balance.available - intent.quantity
            if remaining < limits.min_base_reserve:
                reasons.append("BASE_RESERVE_BREACH")
        if context.balance.available < intent.quantity:
            reasons.append("INSUFFICIENT_BASE_BALANCE")

        notional = intent.notional if intent.limit_price is not None else Decimal("0")
        if intent.limit_price is not None and notional < instrument.min_notional:
            reasons.append("MIN_NOTIONAL_BREACH")
        if intent.order_type is OrderType.MARKET:
            reasons.append("MARKET_ORDER_UNSUPPORTED_IN_M0")
        if limits.max_order_notional_quote <= 0:
            reasons.append("ORDER_LIMIT_UNCONFIGURED")
        elif notional > limits.max_order_notional_quote:
            reasons.append("ORDER_LIMIT_BREACH")
        if limits.max_daily_sell_notional_quote <= 0:
            reasons.append("DAILY_LIMIT_UNCONFIGURED")
        elif context.daily_sell_notional_quote + notional > limits.max_daily_sell_notional_quote:
            reasons.append("DAILY_LIMIT_BREACH")
        if limits.max_daily_sell_ratio <= 0:
            reasons.append("DAILY_RATIO_UNCONFIGURED")
        elif context.daily_base_reference_quantity <= 0:
            reasons.append("DAILY_RATIO_REFERENCE_MISSING")
        elif context.daily_sold_base_quantity + intent.quantity > context.daily_base_reference_quantity * limits.max_daily_sell_ratio:
            reasons.append("DAILY_RATIO_BREACH")

        if intent.reference_price is None:
            reasons.append("REFERENCE_PRICE_REQUIRED")
        elif intent.max_slippage_bps <= 0 or limits.max_slippage_bps <= 0:
            reasons.append("SLIPPAGE_LIMIT_UNCONFIGURED")
        elif intent.max_slippage_bps > limits.max_slippage_bps:
            reasons.append("SLIPPAGE_LIMIT_BREACH")
        elif best_bid is not None:
            minimum_bid = intent.reference_price * (Decimal("1") - intent.max_slippage_bps / Decimal("10000"))
            if best_bid.price < minimum_bid:
                reasons.append("SLIPPAGE_EXCEEDED")
            if intent.limit_price is not None and best_bid.price < intent.limit_price:
                reasons.append("BID_BELOW_LIMIT")

        if len([timestamp for timestamp in context.recent_order_timestamps if timedelta(0) <= context.now - timestamp <= timedelta(minutes=1)]) >= limits.max_orders_per_minute:
            reasons.append("ORDER_FREQUENCY_LIMIT")

        if reasons:
            return RiskDecision.deny(*reasons)
        return RiskDecision.allow()
