"""OKX Spot public historical-candles gateway."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from domain.candle import Candle, CandleInterval, HistoryQuery
from ports.rest import JsonValueRestTransport

from .history_base import HistoryGatewayBase


class OkxSpotHistoryGateway(HistoryGatewayBase):
    INTERVALS = {CandleInterval.DAY: "1D", CandleInterval.HOUR: "1H", CandleInterval.FIVE_MINUTE: "5m"}

    def __init__(
        self,
        transport: JsonValueRestTransport,
        *,
        page_limit: int = 100,
        max_pages: int = 2000,
        pause_seconds: float = 0.0,
        api_routes: Mapping[str, str | None] | None = None,
    ) -> None:
        super().__init__(
            "okx",
            transport,
            page_limit=min(page_limit, 100),
            max_pages=max_pages,
            pause_seconds=pause_seconds,
            api_routes=api_routes,
        )

    def fetch_candles(self, query: HistoryQuery) -> tuple[Candle, ...]:
        self._validate(query)
        cursor = query.end_ms
        candles: list[Candle] = []
        pages = 0
        while cursor > query.start_ms and pages < self._max_pages:
            payload = self._get_value(
                self._route("candles_path"),
                {
                    "instId": query.native_symbol,
                    "bar": self.INTERVALS[query.interval],
                    "after": str(cursor),
                    "limit": str(self._page_limit),
                },
            )
            if not isinstance(payload, dict) or str(payload.get("code", "0")) != "0":
                raise self._upstream_error("OKX historical-candles response returned an error")
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise self._upstream_error("OKX historical-candles response has no data array")
            pages += 1
            if not rows:
                break
            page = [self._parse_row(row, query) for row in rows]
            candles.extend(candle for candle in page if query.start_at <= candle.open_time < query.end_at)
            oldest = min(candle.open_time_ms for candle in page)
            next_cursor = oldest - 1
            if next_cursor >= cursor:
                raise self._upstream_error("OKX historical-candles pagination did not advance")
            cursor = next_cursor
            self._pause()
            if len(rows) < self._page_limit or oldest <= query.start_ms:
                break
        self._pagination_limit_reached(query.start_ms, cursor, pages)
        return self._unique_sorted(candles)

    @staticmethod
    def _parse_row(row: Any, query: HistoryQuery) -> Candle:
        if not isinstance(row, list) or len(row) < 8:
            raise ValueError("OKX candle row is incomplete")
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
            quote_volume=row[7],
            trade_count=None,
        )
