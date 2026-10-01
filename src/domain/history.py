"""Dataset identity and quality metadata for reproducible replay."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib

from .market import MarketType, _aware


class DataLevel(StrEnum):
    KLINE = "KLINE"
    TRADE = "TRADE"
    L1 = "L1"
    L2 = "L2"


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    dataset_id: str
    venue_id: str
    market_type: MarketType
    instrument_key: str
    data_level: DataLevel
    start_at: datetime
    end_at: datetime
    file_format: str
    source: str
    row_count: int
    content_sha256: str
    gap_count: int = 0
    duplicate_count: int = 0
    interval: str = "unknown"

    def __post_init__(self) -> None:
        venue_id = str(self.venue_id).strip().lower()
        instrument_key = str(self.instrument_key).strip()
        file_format = str(self.file_format).strip().lower()
        source = str(self.source).strip()
        interval = str(self.interval).strip().lower()
        market_type = self.market_type if isinstance(self.market_type, MarketType) else MarketType(self.market_type)
        data_level = self.data_level if isinstance(self.data_level, DataLevel) else DataLevel(self.data_level)
        if not venue_id or not instrument_key or not file_format or not source or not interval:
            raise ValueError("dataset identity fields must not be empty")
        _aware(self.start_at, "start_at")
        _aware(self.end_at, "end_at")
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        if self.row_count < 0 or self.gap_count < 0 or self.duplicate_count < 0:
            raise ValueError("dataset counts must not be negative")
        digest = str(self.content_sha256).strip().lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("content_sha256 must be a 64-character hexadecimal digest")
        if not str(self.dataset_id).strip():
            raise ValueError("dataset_id must not be empty")
        object.__setattr__(self, "venue_id", venue_id)
        object.__setattr__(self, "market_type", market_type)
        object.__setattr__(self, "instrument_key", instrument_key)
        object.__setattr__(self, "data_level", data_level)
        object.__setattr__(self, "file_format", file_format)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "interval", interval)
        object.__setattr__(self, "content_sha256", digest)

    @classmethod
    def build(
        cls,
        *,
        venue_id: str,
        market_type: MarketType,
        instrument_key: str,
        data_level: DataLevel,
        start_at: datetime,
        end_at: datetime,
        file_format: str,
        source: str,
        row_count: int,
        content: bytes,
        gap_count: int = 0,
        duplicate_count: int = 0,
        interval: str = "unknown",
    ) -> "DatasetManifest":
        digest = hashlib.sha256(content).hexdigest()
        normalized_interval = str(interval).strip().lower()
        dataset_id = f"{venue_id}:{instrument_key}:{data_level.value}:{normalized_interval}:{start_at.isoformat()}:{end_at.isoformat()}:{digest[:16]}"
        return cls(
            dataset_id=dataset_id,
            venue_id=venue_id,
            market_type=market_type,
            instrument_key=instrument_key,
            data_level=data_level,
            start_at=start_at,
            end_at=end_at,
            file_format=file_format,
            source=source,
            row_count=row_count,
            content_sha256=digest,
            gap_count=gap_count,
            duplicate_count=duplicate_count,
            interval=normalized_interval,
        )
