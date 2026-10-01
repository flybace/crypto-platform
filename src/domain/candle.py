"""Canonical OHLCV candles used by historical research and replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from .market import MarketType, _aware


class CandleInterval(StrEnum):
    DAY = "1d"
    HOUR = "1h"
    FIVE_MINUTE = "5m"

    @property
    def milliseconds(self) -> int:
        return {
            CandleInterval.DAY: 86_400_000,
            CandleInterval.HOUR: 3_600_000,
            CandleInterval.FIVE_MINUTE: 300_000,
        }[self]

    @classmethod
    def parse(cls, value: str | "CandleInterval") -> "CandleInterval":
        try:
            return value if isinstance(value, cls) else cls(str(value).strip().lower())
        except ValueError as error:
            raise ValueError("interval must be one of 1d, 1h, 5m") from error


def _decimal(value: Decimal | int | str, field: str) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be a decimal value") from error
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


def _positive(value: Decimal, field: str, *, allow_zero: bool = False) -> Decimal:
    if value < 0 or (not allow_zero and value == 0):
        comparison = "non-negative" if allow_zero else "greater than zero"
        raise ValueError(f"{field} must be {comparison}")
    return value


def _utc(value: datetime, field: str) -> datetime:
    _aware(value, field)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class HistoryQuery:
    """One venue-scoped, half-open historical data request."""

    venue_id: str
    market_type: MarketType
    instrument_key: str
    native_symbol: str
    interval: CandleInterval
    start_at: datetime
    end_at: datetime

    def __post_init__(self) -> None:
        venue_id = str(self.venue_id).strip().lower()
        instrument_key = str(self.instrument_key).strip()
        native_symbol = str(self.native_symbol).strip().upper()
        market_type = self.market_type if isinstance(self.market_type, MarketType) else MarketType(self.market_type)
        interval = CandleInterval.parse(self.interval)
        start_at = _utc(self.start_at, "start_at")
        end_at = _utc(self.end_at, "end_at")
        if not venue_id or not instrument_key or not native_symbol:
            raise ValueError("history query identity fields must not be empty")
        if end_at <= start_at:
            raise ValueError("end_at must be after start_at")
        object.__setattr__(self, "venue_id", venue_id)
        object.__setattr__(self, "market_type", market_type)
        object.__setattr__(self, "instrument_key", instrument_key)
        object.__setattr__(self, "native_symbol", native_symbol)
        object.__setattr__(self, "interval", interval)
        object.__setattr__(self, "start_at", start_at)
        object.__setattr__(self, "end_at", end_at)

    @property
    def start_ms(self) -> int:
        return int(self.start_at.timestamp() * 1000)

    @property
    def end_ms(self) -> int:
        return int(self.end_at.timestamp() * 1000)


@dataclass(frozen=True, slots=True)
class Candle:
    """Validated OHLCV row with exchange-neutral field names."""

    venue_id: str
    market_type: MarketType
    instrument_key: str
    native_symbol: str
    interval: CandleInterval
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trade_count: int | None = None

    def __post_init__(self) -> None:
        venue_id = str(self.venue_id).strip().lower()
        instrument_key = str(self.instrument_key).strip()
        native_symbol = str(self.native_symbol).strip().upper()
        market_type = self.market_type if isinstance(self.market_type, MarketType) else MarketType(self.market_type)
        interval = CandleInterval.parse(self.interval)
        open_time = _utc(self.open_time, "open_time")
        close_time = _utc(self.close_time, "close_time")
        if not venue_id or not instrument_key or not native_symbol:
            raise ValueError("candle identity fields must not be empty")
        if close_time <= open_time:
            raise ValueError("close_time must be after open_time")
        prices = {
            "open": _positive(_decimal(self.open, "open"), "open"),
            "high": _positive(_decimal(self.high, "high"), "high"),
            "low": _positive(_decimal(self.low, "low"), "low"),
            "close": _positive(_decimal(self.close, "close"), "close"),
        }
        if prices["high"] < max(prices["open"], prices["close"]) or prices["low"] > min(prices["open"], prices["close"]):
            raise ValueError("candle high and low must contain open and close")
        volume = _positive(_decimal(self.volume, "volume"), "volume", allow_zero=True)
        quote_volume = _positive(_decimal(self.quote_volume, "quote_volume"), "quote_volume", allow_zero=True)
        trade_count = self.trade_count
        if trade_count is not None:
            if isinstance(trade_count, bool) or int(trade_count) != trade_count or trade_count < 0:
                raise ValueError("trade_count must be a non-negative integer")
            trade_count = int(trade_count)
        object.__setattr__(self, "venue_id", venue_id)
        object.__setattr__(self, "market_type", market_type)
        object.__setattr__(self, "instrument_key", instrument_key)
        object.__setattr__(self, "native_symbol", native_symbol)
        object.__setattr__(self, "interval", interval)
        object.__setattr__(self, "open_time", open_time)
        object.__setattr__(self, "close_time", close_time)
        for field, value in prices.items():
            object.__setattr__(self, field, value)
        object.__setattr__(self, "volume", volume)
        object.__setattr__(self, "quote_volume", quote_volume)
        object.__setattr__(self, "trade_count", trade_count)

    @property
    def open_time_ms(self) -> int:
        return int(self.open_time.timestamp() * 1000)

    @property
    def close_time_ms(self) -> int:
        return int(self.close_time.timestamp() * 1000)

    @property
    def key(self) -> tuple[str, str, int]:
        return self.venue_id, self.instrument_key, self.open_time_ms

    @classmethod
    def close_time_for(cls, open_time: datetime, interval: CandleInterval) -> datetime:
        return _utc(open_time, "open_time") + timedelta(milliseconds=interval.milliseconds - 1)
