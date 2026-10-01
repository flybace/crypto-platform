"""Bybit V5 Spot public payload normalizer; networking stays outside."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from domain.market import Instrument, MarketType, OrderBookSnapshot, PriceLevel, _aware
from domain.market_events import OrderBookDelta, PriceLevelUpdate
from domain.venue import Venue


class BybitSpotPublicAdapter:
    VENUE = Venue(
        venue_id="bybit",
        display_name="Bybit Spot",
        market_types=frozenset({MarketType.SPOT}),
        public_rest_base_url="https://api.bybit-tr.com",
        public_ws_base_url="wss://stream.bybit.kz/v5/public/spot",
    )
    SUPPORTED_DEPTHS = frozenset({1, 50, 200})

    def instrument_from_metadata(self, payload: dict[str, Any]) -> Instrument:
        filters = payload["lotSizeFilter"]
        price_filter = payload["priceFilter"]
        return Instrument(
            venue_id="bybit",
            market_type=MarketType.SPOT,
            base_asset=payload["baseCoin"],
            quote_asset=payload["quoteCoin"],
            native_symbol=payload["symbol"],
            price_tick=Decimal(price_filter["tickSize"]),
            quantity_step=Decimal(filters.get("qtyStep") or filters["basePrecision"]),
            min_quantity=Decimal(filters["minOrderQty"]),
            min_notional=Decimal(
                filters.get("minOrderAmt", filters.get("minNotional", filters["minOrderQty"]))
            ),
        )

    def normalize_order_book(
        self,
        payload: dict[str, Any],
        instrument: Instrument,
        received_at: datetime,
    ) -> OrderBookSnapshot:
        self._validate_instrument(instrument)
        _aware(received_at, "received_at")
        data = self._data(payload)
        self._validate_symbol(data, instrument)
        event_ms = payload.get("ts") or payload.get("time") or data.get("ts")
        sequence = data.get("u") or data.get("seq")
        if event_ms is None or sequence is None:
            raise ValueError("Bybit order book payload lacks timestamp or sequence")
        return OrderBookSnapshot(
            instrument=instrument,
            bids=self._levels(data.get("b", []), reverse=True),
            asks=self._levels(data.get("a", []), reverse=False),
            exchange_timestamp=datetime.fromtimestamp(int(event_ms) / 1000, tz=UTC),
            received_timestamp=received_at,
            sequence=int(sequence),
        )

    def normalize_order_book_event(
        self,
        payload: dict[str, Any],
        instrument: Instrument,
        received_at: datetime,
    ) -> OrderBookSnapshot | OrderBookDelta | None:
        """Normalize Bybit V5 snapshot/delta events and ignore subscribe acks."""
        self._validate_instrument(instrument)
        _aware(received_at, "received_at")
        if payload.get("op") == "subscribe":
            if payload.get("success") is False:
                raise ValueError("Bybit WebSocket subscription failed")
            return None
        if payload.get("success") is False:
            raise ValueError("Bybit WebSocket returned an error")
        topic = str(payload.get("topic", ""))
        if not topic.startswith("orderbook."):
            return None
        data = self._data(payload)
        self._validate_symbol(data, instrument)
        event_ms = payload.get("ts") or data.get("ts")
        sequence = data.get("u")
        if event_ms is None or sequence is None:
            raise ValueError("Bybit order book event lacks timestamp or update id")
        exchange_timestamp = datetime.fromtimestamp(int(event_ms) / 1000, tz=UTC)
        bids = self._updates(data.get("b", []))
        asks = self._updates(data.get("a", []))
        event_type = payload.get("type")
        if event_type == "snapshot":
            return OrderBookSnapshot(
                instrument=instrument,
                bids=tuple(PriceLevel(item.price, item.quantity) for item in bids if item.quantity > 0),
                asks=tuple(PriceLevel(item.price, item.quantity) for item in asks if item.quantity > 0),
                exchange_timestamp=exchange_timestamp,
                received_timestamp=received_at,
                sequence=int(sequence),
            )
        if event_type != "delta":
            raise ValueError("Bybit order book event has an unsupported type")
        previous = data.get("pu")
        previous_sequence = None if previous is None else int(previous)
        if previous_sequence is not None and previous_sequence < 0:
            raise ValueError("Bybit order book delta has an invalid previous update id")
        return OrderBookDelta(
            instrument=instrument,
            bids=bids,
            asks=asks,
            exchange_timestamp=exchange_timestamp,
            received_timestamp=received_at,
            first_sequence=int(sequence),
            last_sequence=int(sequence),
            previous_sequence=previous_sequence,
        )

    @classmethod
    def orderbook_subscription(cls, instrument: Instrument, depth: int = 50) -> dict[str, object]:
        cls._validate_instrument(instrument)
        if int(depth) not in cls.SUPPORTED_DEPTHS:
            allowed = ", ".join(str(item) for item in sorted(cls.SUPPORTED_DEPTHS))
            raise ValueError(f"Bybit Spot order book depth must be one of: {allowed}")
        return {"op": "subscribe", "args": [f"orderbook.{int(depth)}.{instrument.native_symbol}"]}

    @classmethod
    def _data(cls, payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        if data is None:
            data = payload.get("result")
        if not isinstance(data, dict):
            raise ValueError("Bybit order book payload has no data object")
        return data

    @staticmethod
    def _validate_instrument(instrument: Instrument) -> None:
        if instrument.venue_id != "bybit" or instrument.market_type is not MarketType.SPOT:
            raise ValueError("Bybit Spot adapter received an unsupported instrument")

    @staticmethod
    def _validate_symbol(data: dict[str, Any], instrument: Instrument) -> None:
        if str(data.get("s", "")).upper() != instrument.native_symbol.upper():
            raise ValueError("Bybit order book symbol does not match instrument")

    @staticmethod
    def _levels(raw_levels: Any, *, reverse: bool) -> tuple[PriceLevel, ...]:
        if not isinstance(raw_levels, list):
            raise ValueError("Bybit order book levels must be a list")
        levels = tuple(PriceLevel(Decimal(item[0]), Decimal(item[1])) for item in raw_levels)
        return tuple(sorted(levels, key=lambda item: item.price, reverse=reverse))

    @staticmethod
    def _updates(raw_levels: Any) -> tuple[PriceLevelUpdate, ...]:
        if not isinstance(raw_levels, list):
            raise ValueError("Bybit order book updates must be a list")
        return tuple(PriceLevelUpdate(item[0], item[1]) for item in raw_levels)
