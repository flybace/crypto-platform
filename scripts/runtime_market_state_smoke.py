"""Write one synthetic market state for a mounted-runtime smoke check."""

import os
from datetime import UTC, datetime
from decimal import Decimal

from adapters.standalone.file_market_state import FileMarketStateStore
from domain.market import Instrument, MarketType, OrderBookSnapshot, PriceLevel
from domain.market_status import MarketConnectionState, MarketStatus


def main() -> None:
    instrument = Instrument(
        venue_id="smoke",
        market_type=MarketType.SPOT,
        base_asset="TST",
        quote_asset="USDT",
        native_symbol="TSTUSDT",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("10"),
    )
    now = datetime.now(UTC)
    snapshot = OrderBookSnapshot(
        instrument=instrument,
        bids=(PriceLevel(Decimal("100.00"), Decimal("1")),),
        asks=(PriceLevel(Decimal("100.10"), Decimal("1")),),
        exchange_timestamp=now,
        received_timestamp=now,
        sequence=7,
    )
    store = FileMarketStateStore(os.getenv("CRYPTO_MARKET_STATE_PATH", "/runtime/market"))
    store.write_snapshot(snapshot)
    store.write_status(
        MarketStatus(
            venue_id="smoke",
            state=MarketConnectionState.CONNECTED,
            last_received_at=now,
            last_sequence=snapshot.sequence,
        )
    )
    print(snapshot.instrument.key)


if __name__ == "__main__":
    main()
