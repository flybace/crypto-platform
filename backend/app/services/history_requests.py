"""Convert user-facing symbols and dates into strict history queries."""

from __future__ import annotations

import re
from datetime import datetime
from collections.abc import Iterable

from domain.candle import CandleInterval, HistoryQuery
from domain.market import MarketType


KNOWN_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USD", "BTC", "ETH", "BNB")
VENUES = frozenset({"binance", "okx", "bybit"})
_ASSET = re.compile(r"^[A-Z0-9]{2,20}$")


def normalize_symbol(venue_id: str, value: str) -> tuple[str, str, str]:
    venue = str(venue_id).strip().lower()
    raw = str(value).strip().upper()
    if venue not in VENUES or not raw:
        raise ValueError("venue or symbol is not supported")
    if "/" in raw or "-" in raw:
        parts = re.split(r"[/\-]", raw)
        if len(parts) != 2:
            raise ValueError("symbol must contain one base and one quote asset")
        base, quote = parts
    else:
        base = ""
        quote = ""
        for candidate in sorted(KNOWN_QUOTES, key=len, reverse=True):
            if raw.endswith(candidate) and len(raw) > len(candidate):
                base, quote = raw[: -len(candidate)], candidate
                break
        if not base:
            raise ValueError("symbol must be like BTC/USDT or BTCUSDT")
    if not _ASSET.fullmatch(base) or not _ASSET.fullmatch(quote) or base == quote:
        raise ValueError("symbol contains an invalid asset code")
    native = f"{base}-{quote}" if venue == "okx" else f"{base}{quote}"
    return base, quote, native


def build_queries(
    venues: Iterable[str],
    symbols: Iterable[str],
    *,
    interval: str | CandleInterval,
    start_at: datetime,
    end_at: datetime,
) -> tuple[HistoryQuery, ...]:
    parsed_interval = CandleInterval.parse(interval)
    normalized_venues = tuple(dict.fromkeys(str(item).strip().lower() for item in venues if str(item).strip()))
    normalized_symbols = tuple(dict.fromkeys(str(item).strip() for item in symbols if str(item).strip()))
    if not normalized_venues or not normalized_symbols:
        raise ValueError("at least one venue and one symbol are required")
    queries = []
    for venue in normalized_venues:
        if venue not in VENUES:
            raise ValueError(f"unsupported venue: {venue}")
        for symbol in normalized_symbols:
            base, quote, native = normalize_symbol(venue, symbol)
            queries.append(
                HistoryQuery(
                    venue_id=venue,
                    market_type=MarketType.SPOT,
                    instrument_key=f"{venue}:spot:{base}/{quote}",
                    native_symbol=native,
                    interval=parsed_interval,
                    start_at=start_at,
                    end_at=end_at,
                )
            )
    return tuple(queries)
