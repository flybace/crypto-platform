"""Binance Spot public payload normalizer; networking is intentionally separate."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from domain.market import Instrument, MarketType, OrderBookSnapshot, PriceLevel, _aware
from domain.market_events import OrderBookDelta, PriceLevelUpdate
from domain.venue import Venue


class BinanceSpotPublicAdapter:
    VENUE = Venue(
        venue_id="binance",
        display_name="Binance Spot",
        market_types=frozenset({MarketType.SPOT}),
        public_rest_base_url="https://data-api.binance.vision",
        public_ws_base_url="wss://data-stream.binance.vision",
    )

    def instrument_from_exchange_info(self, payload: dict[str, Any]) -> Instrument:
        filters = {item["filterType"]: item for item in payload.get("filters", [])}
        price_filter = filters["PRICE_FILTER"]
        lot_filter = filters["LOT_SIZE"]
        notional_filter = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL")
        if notional_filter is None:
            raise ValueError("Binance symbol payload has no notional filter")
        return Instrument(
            venue_id="binance",
            market_type=MarketType.SPOT,
            base_asset=payload["baseAsset"],
            quote_asset=payload["quoteAsset"],
            native_symbol=payload["symbol"],
            price_tick=Decimal(price_filter["tickSize"]),
            quantity_step=Decimal(lot_filter["stepSize"]),
            min_quantity=Decimal(lot_filter["minQty"]),
            min_notional=Decimal(notional_filter["minNotional"]),
        )

    def normalize_order_book(
        self,
        payload: dict[str, Any],
        instrument: Instrument,
        received_at: datetime,
    ) -> OrderBookSnapshot:
        if instrument.venue_id != "binance" or instrument.market_type is not MarketType.SPOT:
            raise ValueError("Binance Spot adapter received an unsupported instrument")
        _aware(received_at, "received_at")
        event_ms = payload.get("E") or payload.get("T")
        bids = tuple(PriceLevel(Decimal(price), Decimal(quantity)) for price, quantity in payload["bids"])
        asks = tuple(PriceLevel(Decimal(price), Decimal(quantity)) for price, quantity in payload["asks"])
        return OrderBookSnapshot(
            instrument=instrument,
            bids=bids,
            asks=asks,
            exchange_timestamp=received_at if event_ms is None else datetime.fromtimestamp(int(event_ms) / 1000, tz=UTC),
            received_timestamp=received_at,
            sequence=int(payload["lastUpdateId"]),
        )

    def normalize_depth_event(
        self,
        payload: dict[str, Any],
        instrument: Instrument,
        received_at: datetime,
    ) -> OrderBookDelta:
        """Normalize raw or combined-stream Binance diff-depth events."""
        if instrument.venue_id != "binance" or instrument.market_type is not MarketType.SPOT:
            raise ValueError("Binance Spot adapter received an unsupported instrument")
        _aware(received_at, "received_at")
        event = payload.get("data", payload) if "stream" in payload else payload
        if not isinstance(event, dict) or event.get("e") != "depthUpdate":
            raise ValueError("Binance payload is not a depth update")
        if str(event.get("s", "")).upper() != instrument.native_symbol.upper():
            raise ValueError("Binance depth update symbol does not match instrument")
        event_ms = event.get("E") or event.get("T")
        if event_ms is None:
            raise ValueError("Binance depth update lacks event time")
        previous = event.get("pu")
        return OrderBookDelta(
            instrument=instrument,
            bids=tuple(PriceLevelUpdate(price, quantity) for price, quantity in event["b"]),
            asks=tuple(PriceLevelUpdate(price, quantity) for price, quantity in event["a"]),
            exchange_timestamp=datetime.fromtimestamp(int(event_ms) / 1000, tz=UTC),
            received_timestamp=received_at,
            first_sequence=int(event["U"]),
            last_sequence=int(event["u"]),
            previous_sequence=None if previous is None else int(previous),
        )

    @staticmethod
    def depth_stream_url(
        instrument: Instrument,
        speed_ms: int = 1000,
        *,
        base_url: str | None = None,
    ) -> str:
        if instrument.venue_id != "binance" or instrument.market_type is not MarketType.SPOT:
            raise ValueError("Binance Spot adapter received an unsupported instrument")
        if speed_ms not in (100, 1000):
            raise ValueError("Binance depth speed must be 100 or 1000 milliseconds")
        symbol = instrument.native_symbol.strip().lower()
        stream_base = str(base_url or BinanceSpotPublicAdapter.VENUE.public_ws_base_url).strip().rstrip("/")
        if not stream_base:
            raise ValueError("Binance public WebSocket base URL must not be empty")
        return f"{stream_base}/ws/{symbol}@depth@{speed_ms}ms"
