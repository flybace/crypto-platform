"""Append-only JSONL archive for accepted normalized public L2 states."""

import json
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from domain.market import Instrument, MarketType, OrderBookSnapshot, PriceLevel
from ports.market_archive import MarketArchiveError


_SEGMENT_PATTERN = re.compile(r"--(?P<bucket>\d+)(?:--part-(?P<part>\d+))?$")
_MAX_RECOVERY_TAIL_BYTES = 1024 * 1024


class FileMarketArchive:
    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        create: bool = True,
        segment_seconds: int = 3600,
        max_segment_bytes: int | None = 64 * 1024 * 1024,
        retention_seconds: int | None = None,
        cleanup_interval_seconds: float = 60.0,
    ) -> None:
        if segment_seconds <= 0:
            raise ValueError("segment_seconds must be positive")
        if max_segment_bytes is not None and max_segment_bytes <= 0:
            raise ValueError("max_segment_bytes must be positive when provided")
        if retention_seconds is not None and retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive when provided")
        if cleanup_interval_seconds <= 0:
            raise ValueError("cleanup_interval_seconds must be positive")
        self._root = Path(root)
        self._segment_seconds = segment_seconds
        self._max_segment_bytes = max_segment_bytes
        self._retention_seconds = retention_seconds
        self._cleanup_interval_seconds = cleanup_interval_seconds
        self._last_cleanup_monotonic = 0.0
        if create:
            try:
                self._root.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise MarketArchiveError("market archive directory is unavailable") from error

    def append_snapshot(self, snapshot: OrderBookSnapshot) -> None:
        record = json.dumps(
            _snapshot_payload(snapshot),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        path = self._append_path(snapshot, len(record))
        try:
            with path.open("ab") as handle:
                handle.write(record)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as error:
            raise MarketArchiveError("market archive append failed") from error
        self._maybe_prune(snapshot.received_timestamp)

    def recover(self) -> int:
        """Discard only unterminated tail bytes; complete records stay immutable."""
        removed_bytes = 0
        if not self._root.exists():
            return removed_bytes
        try:
            for path in self._all_paths():
                with path.open("r+b") as handle:
                    handle.seek(0, os.SEEK_END)
                    size = handle.tell()
                    if size == 0:
                        continue
                    handle.seek(-1, os.SEEK_END)
                    if handle.read(1) == b"\n":
                        continue
                    tail_size = min(size, _MAX_RECOVERY_TAIL_BYTES)
                    handle.seek(size - tail_size)
                    tail = handle.read(tail_size)
                    boundary = tail.rfind(b"\n")
                    if boundary < 0 and size > tail_size:
                        raise ValueError("incomplete market archive tail exceeds recovery bound")
                    truncate_at = size - tail_size + boundary + 1 if boundary >= 0 else 0
                    removed_bytes += size - truncate_at
                    handle.truncate(truncate_at)
                    handle.flush()
                    os.fsync(handle.fileno())
        except (OSError, ValueError) as error:
            raise MarketArchiveError("market archive recovery failed") from error
        return removed_bytes

    def read_snapshots(
        self,
        instrument_key: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
        tail: bool = False,
    ) -> tuple[OrderBookSnapshot, ...]:
        normalized_key = str(instrument_key).strip()
        if not normalized_key or any(character.isspace() for character in normalized_key):
            raise MarketArchiveError("market archive instrument key is invalid")
        if start is not None and (start.tzinfo is None or start.utcoffset() is None):
            raise MarketArchiveError("market archive start must be timezone-aware")
        if end is not None and (end.tzinfo is None or end.utcoffset() is None):
            raise MarketArchiveError("market archive end must be timezone-aware")
        if start is not None and end is not None and end < start:
            raise MarketArchiveError("market archive time range is invalid")
        if limit is not None and limit <= 0:
            raise MarketArchiveError("market archive limit must be positive")

        snapshots: list[OrderBookSnapshot] = []
        try:
            paths = self._paths(normalized_key, start=start, end=end)
            if tail:
                paths = tuple(reversed(paths))
            for path in paths:
                for line_number, raw_line in enumerate(_read_lines(path, reverse=tail), start=1):
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    snapshot = _snapshot_from_payload(json.loads(line))
                    if snapshot.instrument.key != normalized_key:
                        raise ValueError(f"instrument mismatch at line {line_number}")
                    received = snapshot.received_timestamp
                    if start is not None and received < start:
                        continue
                    if end is not None and received > end:
                        continue
                    snapshots.append(snapshot)
                    if limit is not None and len(snapshots) >= limit:
                        if tail:
                            snapshots.reverse()
                        return tuple(snapshots)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise MarketArchiveError("market archive file is invalid") from error
        if tail:
            snapshots.reverse()
        return tuple(snapshots)

    def prune(self, *, before: datetime) -> int:
        if before.tzinfo is None or before.utcoffset() is None:
            raise MarketArchiveError("market archive prune boundary must be timezone-aware")
        removed = 0
        try:
            for path in self._all_paths():
                if not self._is_prunable(path, before):
                    continue
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
                removed += 1
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise MarketArchiveError("market archive prune failed") from error
        return removed

    def status(self) -> dict[str, Any]:
        """Describe archive footprint and retention policy for operational checks."""
        try:
            paths = self._all_paths()
            total_bytes = sum(path.stat().st_size for path in paths)
            segment_starts = tuple(
                start for path in paths if (start := self._segment_start(path)) is not None
            )
        except OSError as error:
            raise MarketArchiveError("market archive status is unavailable") from error
        return {
            "schema": "market-archive-status-v1",
            "root_exists": self._root.exists(),
            "segment_count": len(paths),
            "total_bytes": total_bytes,
            "segment_seconds": self._segment_seconds,
            "max_segment_bytes": self._max_segment_bytes,
            "retention_seconds": self._retention_seconds,
            "cleanup_interval_seconds": self._cleanup_interval_seconds,
            "oldest_segment_at": min(segment_starts).isoformat() if segment_starts else None,
            "latest_segment_at": max(segment_starts).isoformat() if segment_starts else None,
        }
    def _append_path(self, snapshot: OrderBookSnapshot, record_bytes: int) -> Path:
        instrument_key = snapshot.instrument.key
        bucket = int(snapshot.received_timestamp.astimezone(UTC).timestamp()) // self._segment_seconds
        prefix = self._prefix(instrument_key)
        if self._max_segment_bytes is not None and record_bytes > self._max_segment_bytes:
            raise MarketArchiveError("market archive record exceeds the segment byte limit")
        candidate = self._root / f"{prefix}--{bucket}.jsonl"
        if self._segment_available(candidate, record_bytes):
            return candidate
        part = 1
        while True:
            candidate = self._root / f"{prefix}--{bucket}--part-{part}.jsonl"
            if self._segment_available(candidate, record_bytes):
                return candidate
            part += 1

    def _segment_available(self, path: Path, record_bytes: int) -> bool:
        if not path.exists():
            return True
        if self._max_segment_bytes is None:
            return False
        try:
            return path.stat().st_size + record_bytes <= self._max_segment_bytes
        except OSError as error:
            raise MarketArchiveError("market archive segment is unavailable") from error

    def _paths(
        self,
        instrument_key: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[Path, ...]:
        prefix = self._prefix(instrument_key)
        paths = [path for path in self._root.glob(f"{prefix}--*.jsonl") if path.is_file()]
        legacy = self._root / f"{prefix}.jsonl"
        if legacy.is_file():
            paths.append(legacy)
        if start is not None or end is not None:
            filtered: list[Path] = []
            for path in paths:
                segment_start = self._segment_start(path)
                if segment_start is None:
                    filtered.append(path)
                    continue
                segment_end = segment_start + timedelta(seconds=self._segment_seconds)
                if start is not None and segment_end <= start:
                    continue
                if end is not None and segment_start > end:
                    continue
                filtered.append(path)
            paths = filtered
        return tuple(sorted(paths, key=self._path_order))

    def _all_paths(self) -> tuple[Path, ...]:
        return tuple(path for path in self._root.glob("book-*.jsonl") if path.is_file())

    def _is_prunable(self, path: Path, before: datetime) -> bool:
        segment_start = self._segment_start(path)
        if segment_start is not None:
            return segment_start + timedelta(seconds=self._segment_seconds) <= before
        latest = self._latest_received(path)
        return latest is not None and latest <= before

    def _latest_received(self, path: Path) -> datetime | None:
        latest: datetime | None = None
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                received = _snapshot_from_payload(json.loads(line)).received_timestamp
                latest = received if latest is None else max(latest, received)
        return latest

    def _maybe_prune(self, received: datetime) -> None:
        if self._retention_seconds is None:
            return
        now = time.monotonic()
        if now - self._last_cleanup_monotonic < self._cleanup_interval_seconds:
            return
        removed = self.prune(before=received - timedelta(seconds=self._retention_seconds))
        self._last_cleanup_monotonic = now
        if removed:
            return

    def _prefix(self, instrument_key: str) -> str:
        normalized_key = str(instrument_key).strip()
        if not normalized_key or any(character.isspace() for character in normalized_key):
            raise MarketArchiveError("market archive instrument key is invalid")
        return f"book-{quote(normalized_key, safe='')}"

    def _segment_start(self, path: Path) -> datetime | None:
        match = _SEGMENT_PATTERN.search(path.stem)
        if match is None:
            return None
        bucket = int(match.group("bucket"))
        return datetime.fromtimestamp(bucket * self._segment_seconds, tz=UTC)

    def _path_order(self, path: Path) -> tuple[int, int, str]:
        segment_start = self._segment_start(path)
        bucket = -1 if segment_start is None else int(segment_start.timestamp())
        match = _SEGMENT_PATTERN.search(path.stem)
        part = 0 if match is None or match.group("part") is None else int(match.group("part"))
        return bucket, part, path.name


def _snapshot_payload(snapshot: OrderBookSnapshot) -> dict[str, Any]:
    return {
        "schema": "market-book-state-v1",
        "instrument": {
            "venue_id": snapshot.instrument.venue_id,
            "market_type": snapshot.instrument.market_type.value,
            "base_asset": snapshot.instrument.base_asset,
            "quote_asset": snapshot.instrument.quote_asset,
            "native_symbol": snapshot.instrument.native_symbol,
            "price_tick": str(snapshot.instrument.price_tick),
            "quantity_step": str(snapshot.instrument.quantity_step),
            "min_quantity": str(snapshot.instrument.min_quantity),
            "min_notional": str(snapshot.instrument.min_notional),
        },
        "bids": [[str(level.price), str(level.quantity)] for level in snapshot.bids],
        "asks": [[str(level.price), str(level.quantity)] for level in snapshot.asks],
        "exchange_timestamp": snapshot.exchange_timestamp.isoformat(),
        "received_timestamp": snapshot.received_timestamp.isoformat(),
        "sequence": snapshot.sequence,
    }


def _read_lines(path: Path, *, reverse: bool):
    if not reverse:
        with path.open("rb") as handle:
            yield from handle
        return

    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        carry = b""
        while position:
            start = max(0, position - 64 * 1024)
            handle.seek(start)
            chunk = handle.read(position - start)
            position = start
            lines = (chunk + carry).split(b"\n")
            carry = lines.pop(0)
            yield from reversed(lines)
        if carry:
            yield carry


def _snapshot_from_payload(payload: dict[str, Any]) -> OrderBookSnapshot:
    if payload.get("schema") != "market-book-state-v1":
        raise ValueError("unsupported market archive schema")
    item = payload["instrument"]
    instrument = Instrument(
        venue_id=item["venue_id"],
        market_type=MarketType(item["market_type"]),
        base_asset=item["base_asset"],
        quote_asset=item["quote_asset"],
        native_symbol=item["native_symbol"],
        price_tick=item["price_tick"],
        quantity_step=item["quantity_step"],
        min_quantity=item["min_quantity"],
        min_notional=item["min_notional"],
    )
    return OrderBookSnapshot(
        instrument=instrument,
        bids=tuple(PriceLevel(price, quantity) for price, quantity in payload["bids"]),
        asks=tuple(PriceLevel(price, quantity) for price, quantity in payload["asks"]),
        exchange_timestamp=datetime.fromisoformat(payload["exchange_timestamp"]),
        received_timestamp=datetime.fromisoformat(payload["received_timestamp"]),
        sequence=int(payload["sequence"]),
    )
