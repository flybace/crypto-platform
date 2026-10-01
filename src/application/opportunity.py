"""Executable cross-venue price comparison with explicit cost accounting."""

from datetime import datetime, timedelta
from decimal import Decimal

from domain.market import MarketType, OrderBookSnapshot
from domain.opportunity import CostModel, FeeSchedule, Opportunity, OpportunityState


class OpportunityScanner:
    def __init__(self, max_market_age_seconds: int = 2) -> None:
        if max_market_age_seconds <= 0:
            raise ValueError("max_market_age_seconds must be positive")
        self._max_market_age_seconds = max_market_age_seconds

    def scan(
        self,
        buy_market: OrderBookSnapshot,
        sell_market: OrderBookSnapshot,
        buy_fees: FeeSchedule,
        sell_fees: FeeSchedule,
        costs: CostModel,
        now: datetime,
        ttl: timedelta = timedelta(seconds=1),
    ) -> Opportunity:
        if ttl <= timedelta(0):
            raise ValueError("opportunity ttl must be positive")
        base = buy_market.instrument
        identity = f"{buy_market.instrument.canonical_symbol}:{buy_market.instrument.market_type.value}"
        if buy_market.instrument.canonical_symbol != sell_market.instrument.canonical_symbol:
            return self._blocked(buy_market.instrument.venue_id, sell_market.instrument.venue_id, identity, now, ttl, "INSTRUMENT_MISMATCH")
        if buy_market.instrument.market_type is not MarketType.SPOT or sell_market.instrument.market_type is not MarketType.SPOT:
            return self._blocked(buy_market.instrument.venue_id, sell_market.instrument.venue_id, identity, now, ttl, "SPOT_ONLY")
        if buy_market.instrument.venue_id == sell_market.instrument.venue_id:
            return self._blocked(base.venue_id, base.venue_id, identity, now, ttl, "VENUES_MUST_DIFFER")
        if buy_fees.venue_id != buy_market.instrument.venue_id or sell_fees.venue_id != sell_market.instrument.venue_id:
            return self._blocked(buy_market.instrument.venue_id, sell_market.instrument.venue_id, identity, now, ttl, "FEE_VENUE_MISMATCH")
        if buy_market.age_seconds(now) > self._max_market_age_seconds or sell_market.age_seconds(now) > self._max_market_age_seconds:
            return self._blocked(buy_market.instrument.venue_id, sell_market.instrument.venue_id, identity, now, ttl, "STALE_MARKET_DATA")
        if buy_market.age_seconds(now) < 0 or sell_market.age_seconds(now) < 0:
            return self._blocked(buy_market.instrument.venue_id, sell_market.instrument.venue_id, identity, now, ttl, "MARKET_TIME_INVALID")

        ask = buy_market.best_ask
        bid = sell_market.best_bid
        if ask is None or bid is None:
            return self._blocked(buy_market.instrument.venue_id, sell_market.instrument.venue_id, identity, now, ttl, "NO_EXECUTABLE_LIQUIDITY")
        quantity = min(ask.quantity, bid.quantity)
        gross = (bid.price - ask.price) * quantity if bid.price > ask.price else Decimal("0")
        buy_value = ask.price * quantity
        sell_value = bid.price * quantity
        fees = buy_value * buy_fees.taker_bps / Decimal("10000") + sell_value * sell_fees.taker_bps / Decimal("10000")
        slippage = (buy_value + sell_value) * costs.expected_slippage_bps / Decimal("10000")
        other = (
            costs.inventory_cost_quote
            + costs.transfer_cost_quote
            + costs.latency_buffer_quote
            + costs.failure_risk_buffer_quote
        )
        net = gross - fees - slippage - other
        state = OpportunityState.VALIDATED if net > 0 else OpportunityState.BLOCKED
        reason = None if state is OpportunityState.VALIDATED else "NET_EDGE_BELOW_ZERO"
        return Opportunity(
            buy_venue_id=buy_market.instrument.venue_id,
            sell_venue_id=sell_market.instrument.venue_id,
            instrument_key=identity,
            quantity=quantity,
            buy_price=ask.price,
            sell_price=bid.price,
            gross_edge_quote=gross,
            fees_quote=fees,
            slippage_quote=slippage,
            other_costs_quote=other,
            net_edge_quote=net,
            created_at=now,
            expires_at=now + ttl,
            state=state,
            blocking_reason=reason,
        )

    @staticmethod
    def _blocked(buy_venue: str, sell_venue: str, instrument_key: str, now: datetime, ttl: timedelta, reason: str) -> Opportunity:
        return Opportunity(
            buy_venue_id=buy_venue,
            sell_venue_id=sell_venue,
            instrument_key=instrument_key,
            quantity=Decimal("0"),
            buy_price=Decimal("0"),
            sell_price=Decimal("0"),
            gross_edge_quote=Decimal("0"),
            fees_quote=Decimal("0"),
            slippage_quote=Decimal("0"),
            other_costs_quote=Decimal("0"),
            net_edge_quote=Decimal("0"),
            created_at=now,
            expires_at=now + ttl,
            state=OpportunityState.BLOCKED,
            blocking_reason=reason,
        )
