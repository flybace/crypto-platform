"""Binance Spot public K-line history gateway."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from domain.candle import Candle, CandleInterval, HistoryQuery
from ports.rest import JsonValueRestTransport

from .history_base import HistoryGatewayBase


class BinanceSpotHistoryGateway(HistoryGatewayBase):
    INTERVALS = {CandleInterval.DAY: "1d", CandleInterval.HOUR: "1h", CandleInterval.FIVE_MINUTE: "5m"}

    def __init__(
        self,
        transport: JsonValueRestTransport,
        *,
        page_limit: int = 1000,
        max_pages: int = 2000,
        pause_seconds: float = 0.0,
        api_routes: Mapping[str, str | None] | None = None,
    ) -> None:
        super().__init__(
            "binance",
            transport,
            page_limit=min(page_limit, 1000),
            max_pages=max_pages,
            pause_seconds=pause_seconds,
            api_routes=api_routes,
        )

    def fetch_candles(self, query: HistoryQuery) -> tuple[Candle, ...]:
        self._validate(query)
        interval_ms = query.interval.milliseconds
        cursor = query.start_ms
        candles: list[Candle] = []
        pages = 0
        while cursor < query.end_ms and pages < self._max_pages:
            payload = self._get_value(
                self._route("candles_path"),
                {
                    "symbol": query.native_symbol,
                    "interval": self.INTERVALS[query.interval],
                    "startTime": str(cursor),
                    "endTime": str(query.end_ms - 1),
                    "limit": str(self._page_limit),
                },
            )
            if not isinstance(payload, list):
                raise self._upstream_error("Binance K-line response was not an array")
            pages += 1
            if not payload:
                break
            page = [self._parse_row(row, query) for row in payload]
            candles.extend(candle for candle in page if query.start_at <= candle.open_time < query.end_at)
            last_open = max(candle.open_time_ms for candle in page)
            next_cursor = last_open + interval_ms
            if next_cursor <= cursor:
                raise self._upstream_error("Binance K-line pagination did not advance")
            cursor = next_cursor
            self._pause()
            if len(payload) < self._page_limit:
                break
        self._pagination_limit_reached(cursor, query.end_ms, pages)
        return self._unique_sorted(candles)

    @staticmethod
    def _parse_row(row: Any, query: HistoryQuery) -> Candle:
        if not isinstance(row, list) or len(row) < 9:
            raise ValueError("Binance K-line row is incomplete")
        open_time = datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC)
        close_time = datetime.fromtimestamp(int(row[6]) / 1000, tz=UTC)
        if close_time <= open_time:
            close_time = Candle.close_time_for(open_time, query.interval)
        return Candle(
            venue_id=query.venue_id,
            market_type=query.market_type,
            instrument_key=query.instrument_key,
            native_symbol=query.native_symbol,
            interval=query.interval,
            open_time=open_time,
            close_time=close_time,
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=row[5],
            quote_volume=row[7],
            trade_count=int(row[8]),
        )
