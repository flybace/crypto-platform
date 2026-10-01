"""Atomic CSV storage and durable manifests for historical candles."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import BinaryIO, Iterable

from domain.candle import Candle, HistoryQuery
from domain.history import DataLevel, DatasetManifest


class HistoryStorageError(RuntimeError):
    """The historical data file or its manifest cannot be trusted."""


@dataclass(frozen=True, slots=True)
class StoredDataset:
    manifest: DatasetManifest
    storage_key: str


@dataclass(frozen=True, slots=True)
class CandlePage:
    """A bounded, chronological view of one verified dataset."""

    items: tuple[Candle, ...]
    total_count: int
    truncated: bool


class HistoryStorage:
    """A low-dependency, one-file-per-series history store.

    CSV is the baseline interchange format. A later Parquet writer can consume
    the same canonical candles without changing the downloader contract.
    """

    COLUMNS = (
        "open_time",
        "close_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "trade_count",
    )

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = RLock()
        self._file_lock_depth = 0
        self._file_lock_handle: BinaryIO | None = None

    @contextmanager
    def locked(self):
        """Coordinate history readers and writers across API, worker, and scheduler processes."""
        with self._lock:
            if self._file_lock_depth == 0:
                self.root.mkdir(parents=True, exist_ok=True)
                handle = self._open_file_lock()
                self._acquire_file_lock(handle)
                self._file_lock_handle = handle
            self._file_lock_depth += 1
            try:
                yield self
            finally:
                self._file_lock_depth -= 1
                if self._file_lock_depth == 0:
                    handle = self._file_lock_handle
                    self._file_lock_handle = None
                    if handle is not None:
                        self._release_file_lock(handle)
                        handle.close()

    def upsert(self, query: HistoryQuery, candles: Iterable[Candle], *, source: str) -> StoredDataset:
        incoming = tuple(candles)
        for candle in incoming:
            self._validate_candle(query, candle)
        with self.locked():
            path = self._data_path(query)
            existing = self._read_csv(path, query) if path.exists() else ()
            incoming_unique_count = len({candle.open_time_ms for candle in incoming})
            merged = {candle.open_time_ms: candle for candle in existing}
            merged.update({candle.open_time_ms: candle for candle in incoming})
            ordered = tuple(merged[key] for key in sorted(merged))
            if not ordered:
                raise HistoryStorageError("cannot persist an empty historical dataset")
            content = self._csv_bytes(ordered)
            start_at = ordered[0].open_time
            end_at = ordered[-1].close_time + timedelta(milliseconds=1)
            manifest = DatasetManifest.build(
                venue_id=query.venue_id,
                market_type=query.market_type,
                instrument_key=query.instrument_key,
                data_level=DataLevel.KLINE,
                start_at=start_at,
                end_at=end_at,
                file_format="csv",
                source=source,
                row_count=len(ordered),
                content=content,
                gap_count=self._gap_count(ordered),
                duplicate_count=len(incoming) - incoming_unique_count,
                interval=query.interval.value,
            )
            self._atomic_write(path, content)
            self._atomic_write(
                self._manifest_path(path),
                json.dumps(self._manifest_payload(manifest), ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
                + b"\n",
            )
            return StoredDataset(manifest=manifest, storage_key=self._storage_key(path))

    def list_datasets(self) -> tuple[StoredDataset, ...]:
        with self.locked():
            if not self.root.exists():
                return ()
            records: list[StoredDataset] = []
            for manifest_path in sorted(self.root.rglob("*.manifest.json")):
                try:
                    manifest = self._read_manifest(manifest_path)
                except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
                    raise HistoryStorageError(f"invalid history manifest: {manifest_path.name}") from error
                data_path = Path(str(manifest_path)[: -len(".manifest.json")])
                if not data_path.exists():
                    raise HistoryStorageError(f"history data is missing: {data_path.name}")
                if hashlib.sha256(data_path.read_bytes()).hexdigest() != manifest.content_sha256:
                    raise HistoryStorageError(f"history data hash mismatch: {data_path.name}")
                records.append(StoredDataset(manifest=manifest, storage_key=self._storage_key(data_path)))
            return tuple(sorted(records, key=lambda item: item.manifest.dataset_id))

    def coverage(self) -> dict[str, object]:
        datasets = self.list_datasets()
        by_venue: dict[str, dict[str, int]] = {}
        for dataset in datasets:
            venue = by_venue.setdefault(dataset.manifest.venue_id, {"datasets": 0, "rows": 0})
            venue["datasets"] += 1
            venue["rows"] += dataset.manifest.row_count
        return {
            "dataset_count": len(datasets),
            "row_count": sum(item.manifest.row_count for item in datasets),
            "by_venue": by_venue,
            "datasets": [self.dataset_dict(item) for item in datasets],
        }

    def find_dataset(
        self,
        *,
        venue_id: str,
        instrument_key: str,
        interval: str,
    ) -> StoredDataset | None:
        """Find one series after validating every candidate's manifest and hash."""
        normalized_venue = str(venue_id).strip().lower()
        normalized_instrument = str(instrument_key).strip()
        normalized_interval = str(interval).strip().lower()
        matches = tuple(
            item
            for item in self.list_datasets()
            if item.manifest.venue_id == normalized_venue
            and item.manifest.instrument_key == normalized_instrument
            and item.manifest.interval == normalized_interval
        )
        if len(matches) > 1:
            raise HistoryStorageError("multiple history datasets match one series")
        return matches[0] if matches else None

    def read_page(
        self,
        dataset: StoredDataset,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int | None = 5000,
        tail: bool = False,
    ) -> CandlePage:
        """Read a bounded page from a dataset whose hash has been rechecked."""
        if limit is not None and limit <= 0:
            raise ValueError("history read limit must be positive")
        manifest = dataset.manifest
        query = HistoryQuery(
            venue_id=manifest.venue_id,
            market_type=manifest.market_type,
            instrument_key=manifest.instrument_key,
            native_symbol=self._native_symbol(dataset.storage_key),
            interval=manifest.interval,
            start_at=manifest.start_at,
            end_at=manifest.end_at,
        )
        data_path = self.root / Path(dataset.storage_key)
        with self.locked():
            if not data_path.exists():
                raise HistoryStorageError(f"history data is missing: {data_path.name}")
            if hashlib.sha256(data_path.read_bytes()).hexdigest() != manifest.content_sha256:
                raise HistoryStorageError(f"history data hash mismatch: {data_path.name}")
            candles = self._read_csv(data_path, query)
        lower = self._normalize_bound(start_at, "start_at") if start_at is not None else manifest.start_at
        upper = self._normalize_bound(end_at, "end_at") if end_at is not None else manifest.end_at
        if upper <= lower:
            raise ValueError("end_at must be after start_at")
        filtered = tuple(candle for candle in candles if lower <= candle.open_time < upper)
        if limit is None:
            selected = filtered
        else:
            selected = filtered[-limit:] if tail else filtered[:limit]
        return CandlePage(
            items=selected,
            total_count=len(filtered),
            truncated=limit is not None and len(filtered) > limit,
        )

    def read_all(
        self,
        dataset: StoredDataset,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> CandlePage:
        """Read the complete verified range for research and backtesting."""
        return self.read_page(dataset, start_at=start_at, end_at=end_at, limit=None)

    @classmethod
    def dataset_dict(cls, dataset: StoredDataset) -> dict[str, object]:
        manifest = dataset.manifest
        return {
            "dataset_id": manifest.dataset_id,
            "venue_id": manifest.venue_id,
            "native_symbol": cls._native_symbol(dataset.storage_key),
            "market_type": manifest.market_type.value,
            "instrument_key": manifest.instrument_key,
            "data_level": manifest.data_level.value,
            "interval": manifest.interval,
            "start_at": manifest.start_at.isoformat(),
            "end_at": manifest.end_at.isoformat(),
            "file_format": manifest.file_format,
            "source": manifest.source,
            "row_count": manifest.row_count,
            "gap_count": manifest.gap_count,
            "duplicate_count": manifest.duplicate_count,
            "content_sha256": manifest.content_sha256,
            "storage_key": dataset.storage_key,
        }

    def _data_path(self, query: HistoryQuery) -> Path:
        components = (query.venue_id, query.market_type.value, query.native_symbol, query.interval.value)
        if any(not component or component in {".", ".."} or any(char in component for char in "\\/:\x00") for component in components):
            raise HistoryStorageError("history query contains an unsafe storage component")
        return self.root.joinpath(*components[:-1], f"{components[-1]}.csv")

    def _open_file_lock(self) -> BinaryIO:
        path = self.root / ".history.lock"
        handle = path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        return handle

    @staticmethod
    def _acquire_file_lock(handle: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            return
        try:
            import fcntl
        except ImportError:  # pragma: no cover - platform without advisory locks
            return
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    @staticmethod
    def _release_file_lock(handle: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        try:
            import fcntl
        except ImportError:  # pragma: no cover - platform without advisory locks
            return
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _manifest_path(data_path: Path) -> Path:
        return data_path.with_name(f"{data_path.name}.manifest.json")

    def _storage_key(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError as error:
            raise HistoryStorageError("history path escaped storage root") from error

    @staticmethod
    def _native_symbol(storage_key: str) -> str:
        parts = Path(storage_key).parts
        if len(parts) < 3 or not parts[-2] or not parts[-1].endswith(".csv"):
            raise HistoryStorageError("history storage key does not contain a native symbol")
        return parts[-2]

    @staticmethod
    def _normalize_bound(value: datetime, field: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field} must include timezone information")
        return value.astimezone(UTC)

    @classmethod
    def _validate_candle(cls, query: HistoryQuery, candle: Candle) -> None:
        if candle.venue_id != query.venue_id or candle.instrument_key != query.instrument_key:
            raise HistoryStorageError("candle does not belong to the requested series")
        if candle.interval != query.interval or candle.native_symbol != query.native_symbol:
            raise HistoryStorageError("candle interval or symbol does not match the requested series")

    @classmethod
    def _csv_bytes(cls, candles: tuple[Candle, ...]) -> bytes:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=cls.COLUMNS, lineterminator="\n")
        writer.writeheader()
        for candle in candles:
            writer.writerow(
                {
                    "open_time": candle.open_time.isoformat(),
                    "close_time": candle.close_time.isoformat(),
                    "open": str(candle.open),
                    "high": str(candle.high),
                    "low": str(candle.low),
                    "close": str(candle.close),
                    "volume": str(candle.volume),
                    "quote_volume": str(candle.quote_volume),
                    "trade_count": "" if candle.trade_count is None else str(candle.trade_count),
                }
            )
        return stream.getvalue().encode("utf-8")

    @classmethod
    def _read_csv(cls, path: Path, query: HistoryQuery) -> tuple[Candle, ...]:
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                if tuple(reader.fieldnames or ()) != cls.COLUMNS:
                    raise HistoryStorageError(f"unexpected history columns in {path.name}")
                candles = []
                for row in reader:
                    candles.append(
                        Candle(
                            venue_id=query.venue_id,
                            market_type=query.market_type,
                            instrument_key=query.instrument_key,
                            native_symbol=query.native_symbol,
                            interval=query.interval,
                            open_time=datetime.fromisoformat(row["open_time"]),
                            close_time=datetime.fromisoformat(row["close_time"]),
                            open=row["open"],
                            high=row["high"],
                            low=row["low"],
                            close=row["close"],
                            volume=row["volume"],
                            quote_volume=row["quote_volume"],
                            trade_count=None if not row["trade_count"] else int(row["trade_count"]),
                        )
                    )
                return tuple(candles)
        except HistoryStorageError:
            raise
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise HistoryStorageError(f"unable to read history data: {path.name}") from error

    @staticmethod
    def _gap_count(candles: tuple[Candle, ...]) -> int:
        if len(candles) < 2:
            return 0
        interval_ms = candles[0].interval.milliseconds
        span = candles[-1].open_time_ms - candles[0].open_time_ms
        expected = span // interval_ms + 1
        return max(expected - len(candles), 0)

    @staticmethod
    def _manifest_payload(manifest: DatasetManifest) -> dict[str, object]:
        return {
            "dataset_id": manifest.dataset_id,
            "venue_id": manifest.venue_id,
            "market_type": manifest.market_type.value,
            "instrument_key": manifest.instrument_key,
            "data_level": manifest.data_level.value,
            "interval": manifest.interval,
            "start_at": manifest.start_at.isoformat(),
            "end_at": manifest.end_at.isoformat(),
            "file_format": manifest.file_format,
            "source": manifest.source,
            "row_count": manifest.row_count,
            "content_sha256": manifest.content_sha256,
            "gap_count": manifest.gap_count,
            "duplicate_count": manifest.duplicate_count,
        }

    @staticmethod
    def _read_manifest(path: Path) -> DatasetManifest:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return DatasetManifest(
            dataset_id=payload["dataset_id"],
            venue_id=payload["venue_id"],
            market_type=payload["market_type"],
            instrument_key=payload["instrument_key"],
            data_level=payload["data_level"],
            interval=payload.get("interval", "unknown"),
            start_at=datetime.fromisoformat(payload["start_at"]),
            end_at=datetime.fromisoformat(payload["end_at"]),
            file_format=payload["file_format"],
            source=payload["source"],
            row_count=int(payload["row_count"]),
            content_sha256=payload["content_sha256"],
            gap_count=int(payload.get("gap_count", 0)),
            duplicate_count=int(payload.get("duplicate_count", 0)),
        )

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
                temp_name = handle.name
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass
