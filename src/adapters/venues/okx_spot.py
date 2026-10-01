"""OKX Spot public payload normalizer; networking remains an outer concern."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from domain.market import Instrument, MarketType, OrderBookSnapshot, PriceLevel, _aware
from domain.market_events import OrderBookDelta, PriceLevelUpdate
from domain.venue import Venue


class OkxSpotPublicAdapter:
    VENUE = Venue(
        venue_id="okx",
        display_name="OKX Spot",
        market_types=frozenset({MarketType.SPOT}),
        public_rest_base_url="https://www.okx.com",
        public_ws_base_url="wss://ws.okx.com:8443/ws/v5/public",
    )

    def instrument_from_metadata(self, payload: dict[str, Any]) -> Instrument:
        inst_id = str(payload["instId"])
        try:
            base_asset, quote_asset = inst_id.split("-", 1)
        except ValueError as error:
            raise ValueError("OKX instId must contain base and quote assets") from error
        return Instrument(
            venue_id="okx",
            market_type=MarketType.SPOT,
            base_asset=base_asset,
            quote_asset=quote_asset,
            native_symbol=inst_id,
            price_tick=Decimal(payload["tickSz"]),
            quantity_step=Decimal(payload["lotSz"]),
            min_quantity=Decimal(payload["minSz"]),
            min_notional=Decimal(payload.get("minNotional", payload["minSz"])),
        )

    def normalize_order_book(
        self,
        payload: dict[str, Any],
        instrument: Instrument,
        received_at: datetime,
    ) -> OrderBookSnapshot:
        if instrument.venue_id != "okx" or instrument.market_type is not MarketType.SPOT:
            raise ValueError("OKX Spot adapter received an unsupported instrument")
        _aware(received_at, "received_at")
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise ValueError("OKX order book payload has no data")
        book = data[0]
        event_ms = book.get("ts")
        sequence = book.get("seqId")
        if event_ms is None or sequence is None:
            raise ValueError("OKX order book payload lacks timestamp or sequence")
        bids = tuple(PriceLevel(Decimal(level[0]), Decimal(level[1])) for level in book.get("bids", []))
        asks = tuple(PriceLevel(Decimal(level[0]), Decimal(level[1])) for level in book.get("asks", []))
        return OrderBookSnapshot(
            instrument=instrument,
            bids=bids,
            asks=asks,
            exchange_timestamp=datetime.fromtimestamp(int(event_ms) / 1000, tz=UTC),
            received_timestamp=received_at,
            sequence=int(sequence),
        )

    def normalize_books_event(
        self,
        payload: dict[str, Any],
        instrument: Instrument,
        received_at: datetime,
    ) -> OrderBookSnapshot | OrderBookDelta | None:
        """Normalize an OKX books snapshot or update event."""
        if instrument.venue_id != "okx" or instrument.market_type is not MarketType.SPOT:
            raise ValueError("OKX Spot adapter received an unsupported instrument")
        _aware(received_at, "received_at")
        control_event = payload.get("event")
        if control_event == "subscribe":
            return None
        if control_event == "error":
            raise ValueError("OKX WebSocket subscription failed")
        arg = payload.get("arg")
        if not isinstance(arg, dict) or arg.get("instId") != instrument.native_symbol:
            raise ValueError("OKX books event symbol does not match instrument")
        if arg.get("channel") not in {"books", "books5", "bbo-tbt"}:
            raise ValueError("OKX payload is not a supported books event")
        data = payload.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise ValueError("OKX books event has no data")
        book = data[0]
        event_ms = book.get("ts")
        sequence = book.get("seqId")
        if event_ms is None or sequence is None:
            raise ValueError("OKX books event lacks timestamp or sequence")
        bids = tuple(PriceLevelUpdate(level[0], level[1]) for level in book.get("bids", []))
        asks = tuple(PriceLevelUpdate(level[0], level[1]) for level in book.get("asks", []))
        exchange_timestamp = datetime.fromtimestamp(int(event_ms) / 1000, tz=UTC)
        action = payload.get("action")
        if action == "snapshot":
            snapshot_bids = tuple(
                sorted(
                    (PriceLevel(level.price, level.quantity) for level in bids if level.quantity > 0),
                    key=lambda level: level.price,
                    reverse=True,
                )
            )
            snapshot_asks = tuple(
                sorted(
                    (PriceLevel(level.price, level.quantity) for level in asks if level.quantity > 0),
                    key=lambda level: level.price,
                )
            )
            return OrderBookSnapshot(
                instrument=instrument,
                bids=snapshot_bids,
                asks=snapshot_asks,
                exchange_timestamp=exchange_timestamp,
                received_timestamp=received_at,
                sequence=int(sequence),
            )
        if action != "update":
            raise ValueError("OKX books event has an unsupported action")
        previous = book.get("prevSeqId")
        if previous is None:
            raise ValueError("OKX update lacks previous sequence")
        previous_sequence = int(previous)
        if previous_sequence < 0:
            raise ValueError("OKX update has an invalid previous sequence")
        return OrderBookDelta(
            instrument=instrument,
            bids=bids,
            asks=asks,
            exchange_timestamp=exchange_timestamp,
            received_timestamp=received_at,
            first_sequence=previous_sequence + 1,
            last_sequence=int(sequence),
            previous_sequence=previous_sequence,
        )

    @staticmethod
    def books_subscription(instrument: Instrument, channel: str = "books") -> dict[str, object]:
        if instrument.venue_id != "okx" or instrument.market_type is not MarketType.SPOT:
            raise ValueError("OKX Spot adapter received an unsupported instrument")
        if channel not in {"books", "books5", "bbo-tbt"}:
            raise ValueError("OKX channel is not a supported books channel")
        return {"op": "subscribe", "args": [{"channel": channel, "instId": instrument.native_symbol}]}
