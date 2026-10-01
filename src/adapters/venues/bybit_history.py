"""Bybit V5 Spot public K-line history gateway."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from domain.candle import Candle, CandleInterval, HistoryQuery
from ports.rest import JsonValueRestTransport

from .history_base import HistoryGatewayBase


class BybitSpotHistoryGateway(HistoryGatewayBase):
    INTERVALS = {CandleInterval.DAY: "D", CandleInterval.HOUR: "60", CandleInterval.FIVE_MINUTE: "5"}

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
            "bybit",
            transport,
            page_limit=min(page_limit, 1000),
            max_pages=max_pages,
            pause_seconds=pause_seconds,
            api_routes=api_routes,
        )

    def fetch_candles(self, query: HistoryQuery) -> tuple[Candle, ...]:
        self._validate(query)
        cursor_end = query.end_ms - 1
        candles: list[Candle] = []
        pages = 0
        while cursor_end >= query.start_ms and pages < self._max_pages:
            payload = self._get_value(
                self._route("candles_path"),
                {
                    "category": "spot",
                    "symbol": query.native_symbol,
                    "interval": self.INTERVALS[query.interval],
                    # Bybit V5 names the time bounds start/end. The older
                    # startTime/endTime aliases are ignored by the API and
                    # make every page return the newest candles.
                    "start": str(query.start_ms),
                    "end": str(cursor_end),
                    "limit": str(self._page_limit),
                },
            )
            if not isinstance(payload, dict) or int(payload.get("retCode", -1)) != 0:
                raise self._upstream_error("Bybit K-line response returned an error")
            result = payload.get("result")
            rows = result.get("list") if isinstance(result, dict) else None
            if not isinstance(rows, list):
                raise self._upstream_error("Bybit K-line response has no result list")
            pages += 1
            if not rows:
                break
            page = [self._parse_row(row, query) for row in rows]
            candles.extend(candle for candle in page if query.start_at <= candle.open_time < query.end_at)
            oldest = min(candle.open_time_ms for candle in page)
            next_cursor_end = oldest - 1
            if next_cursor_end >= cursor_end:
                raise self._upstream_error("Bybit K-line pagination did not advance")
            cursor_end = next_cursor_end
            self._pause()
            if len(rows) < self._page_limit or oldest <= query.start_ms:
                break
        self._pagination_limit_reached(query.start_ms, cursor_end + 1, pages)
        return self._unique_sorted(candles)

    @staticmethod
    def _parse_row(row: Any, query: HistoryQuery) -> Candle:
        if not isinstance(row, list) or len(row) < 7:
            raise ValueError("Bybit candle row is incomplete")
        open_time = datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC)
        return Candle(
            venue_id=query.venue_id,
            market_type=query.market_type,
            instrument_key=query.instrument_key,
            native_symbol=query.native_symbol,
            interval=query.interval,
            open_time=open_time,
            close_time=Candle.close_time_for(open_time, query.interval),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=row[5],
            quote_volume=row[6],
            trade_count=None,
        )
