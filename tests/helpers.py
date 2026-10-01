"""Small deterministic fixtures shared by M0 tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from domain.market import Instrument, MarketType, OrderBookSnapshot, PriceLevel
from domain.risk import RiskContext, RiskLimits
from domain.trading import Balance, ExecutionMode, OrderIntent, OrderType, Side


NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def make_instrument(venue_id: str = "binance") -> Instrument:
    return Instrument(
        venue_id=venue_id,
        market_type=MarketType.SPOT,
        base_asset="BTC",
        quote_asset="USDT",
        native_symbol="BTCUSDT",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("10"),
    )


def make_snapshot(
    instrument: Instrument | None = None,
    age_seconds: int = 1,
    bid_price: str = "100.00",
    ask_price: str = "100.10",
    bid_quantity: str = "1",
    sequence: int = 1,
) -> OrderBookSnapshot:
    instrument = instrument or make_instrument()
    received = NOW - timedelta(seconds=age_seconds)
    return OrderBookSnapshot(
        instrument=instrument,
        bids=(PriceLevel(Decimal(bid_price), Decimal(bid_quantity)),),
        asks=(PriceLevel(Decimal(ask_price), Decimal("1")),),
        exchange_timestamp=received,
        received_timestamp=received,
        sequence=sequence,
    )


def make_limits(**overrides: object) -> RiskLimits:
    values: dict[str, object] = {
        "enabled": True,
        "mode": ExecutionMode.SELL_ONLY,
        "venue_allowlist": frozenset({"binance"}),
        "instrument_allowlist": frozenset({"binance:spot:BTC/USDT"}),
        "quote_asset_allowlist": frozenset({"USDT"}),
        "max_order_notional_quote": Decimal("100"),
        "max_daily_sell_notional_quote": Decimal("1000"),
        "max_daily_sell_ratio": Decimal("0.1"),
        "min_base_reserve": Decimal("0.2"),
        "allow_market_order": False,
        "max_slippage_bps": Decimal("20"),
        "max_market_age_seconds": 2,
        "max_open_orders": 1,
        "max_orders_per_minute": 1,
    }
    values.update(overrides)
    return RiskLimits(**values)


def make_intent(
    instrument: Instrument | None = None,
    side: Side = Side.SELL,
    quantity: str = "0.1",
    limit_price: str | None = "100.00",
    order_type: OrderType = OrderType.LIMIT,
) -> OrderIntent:
    instrument = instrument or make_instrument()
    return OrderIntent(
        request_id="request-001",
        account_id="account-001",
        venue_id=instrument.venue_id,
        instrument=instrument,
        side=side,
        order_type=order_type,
        quantity=Decimal(quantity),
        limit_price=None if limit_price is None else Decimal(limit_price),
        strategy_id="sell-inventory",
        strategy_version="1.0.0",
        authorization_id="auth-001",
        generated_at=NOW,
        reference_price=Decimal("100.00"),
        max_slippage_bps=Decimal("20"),
    )


def make_context(
    instrument: Instrument | None = None,
    snapshot: OrderBookSnapshot | None = None,
    **overrides: object,
) -> RiskContext:
    instrument = instrument or make_instrument()
    snapshot = snapshot or make_snapshot(instrument)
    values: dict[str, object] = {
        "balance": Balance("BTC", Decimal("1"), Decimal("1")),
        "market": snapshot,
        "now": NOW,
        "daily_sell_notional_quote": Decimal("0"),
        "daily_sold_base_quantity": Decimal("0"),
        "daily_base_reference_quantity": Decimal("1"),
        "open_order_count": 0,
        "unknown_order_count": 0,
        "recent_order_timestamps": (),
    }
    values.update(overrides)
    return RiskContext(**values)
