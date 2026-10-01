"""Optional Parquet mirrors for verified historical candle datasets."""

from __future__ import annotations

import os
import hashlib
import tempfile
from pathlib import Path
from typing import Any

from .history_storage import HistoryStorage, StoredDataset


class HistoryArchiveError(RuntimeError):
    """The columnar history archive cannot be created or inspected."""


class ParquetHistoryArchive:
    """Write a typed, immutable-by-source mirror of verified CSV history.

    CSV and its Manifest remain the source of truth in the current phase. The
    Parquet file carries the source dataset id and content digest so a stale
    mirror is never silently used as a current archive.
    """

    def __init__(self, history_root: str | Path, archive_root: str | Path | None = None) -> None:
        self.history_root = Path(history_root)
        self.archive_root = Path(archive_root) if archive_root is not None else self.history_root / "_parquet"

    def status(self, datasets: tuple[StoredDataset, ...]) -> dict[str, object]:
        backend = self._backend()
        items: list[dict[str, object]] = []
        for dataset in datasets:
            path = self.archive_path(dataset.storage_key)
            state = "UNAVAILABLE" if backend[0] is None else self._state(path, dataset, backend[1])
            items.append(self._item(dataset, path, state))
        archived = sum(1 for item in items if item["state"] == "ARCHIVED")
        stale = sum(1 for item in items if item["state"] == "STALE")
        missing = sum(1 for item in items if item["state"] == "MISSING")
        if backend[0] is None:
            status = "UNAVAILABLE"
        elif archived == len(items) and items:
            status = "READY"
        elif archived:
            status = "PARTIAL"
        else:
            status = "MISSING" if items else "EMPTY"
        return {
            "status": status,
            "available": backend[0] is not None,
            "error": backend[2],
            "archive_root": self._relative_archive_root(),
            "dataset_count": len(items),
            "archived_count": archived,
            "stale_count": stale,
            "missing_count": missing,
            "items": items,
        }

    def archive_all(self, storage: HistoryStorage) -> dict[str, object]:
        """Archive every currently verified dataset and report per-file errors."""
        with storage.locked():
            datasets = storage.list_datasets()
            backend = self._backend()
            if backend[0] is None:
                raise HistoryArchiveError(backend[2] or "pyarrow is unavailable")
            items: list[dict[str, object]] = []
            errors: list[dict[str, str]] = []
            for dataset in datasets:
                path = self.archive_path(dataset.storage_key)
                try:
                    self._write_dataset(storage, dataset, path, backend[0], backend[1])
                except (OSError, ValueError, TypeError, HistoryArchiveError) as error:
                    errors.append({"storage_key": dataset.storage_key, "message": str(error)})
                else:
                    items.append(self._item(dataset, path, "ARCHIVED", include_digest=True))
            archived = len(items)
            status = "COMPLETED" if not errors else "PARTIAL"
            return {
                "status": status,
                "available": True,
                "error": None,
                "archive_root": self._relative_archive_root(),
                "dataset_count": len(datasets),
                "archived_count": archived,
                "stale_count": 0,
                "missing_count": len(datasets) - archived,
                "failed_count": len(errors),
                "items": items,
                "errors": errors,
            }

    def archive_path(self, storage_key: str) -> Path:
        """Resolve one CSV storage key beneath the dedicated archive root."""
        relative = Path(str(storage_key))
        if relative.is_absolute() or relative.suffix.lower() != ".csv":
            raise HistoryArchiveError("history archive accepts relative CSV storage keys only")
        path = self.archive_root / relative.with_suffix(".parquet")
        root = self.archive_root.resolve()
        try:
            path.resolve().relative_to(root)
        except ValueError as error:
            raise HistoryArchiveError("history archive path escaped its root") from error
        return path

    @staticmethod
    def _backend() -> tuple[Any | None, Any | None, str | None]:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except (ImportError, OSError) as error:
            return None, None, f"pyarrow unavailable: {error}"
        return pa, pq, None

    def _state(self, path: Path, dataset: StoredDataset, parquet: Any) -> str:
        if not path.exists():
            return "MISSING"
        try:
            metadata = parquet.read_metadata(path).metadata or {}
            values = {
                key: self._metadata_value(metadata, key)
                for key in ("crypto_dataset_id", "crypto_content_sha256", "crypto_row_count")
            }
            manifest = dataset.manifest
            if values["crypto_dataset_id"] != manifest.dataset_id:
                return "STALE"
            if values["crypto_content_sha256"] != manifest.content_sha256:
                return "STALE"
            if int(values["crypto_row_count"] or "-1") != manifest.row_count:
                return "STALE"
        except (OSError, TypeError, ValueError, KeyError):
            return "STALE"
        return "ARCHIVED"

    def _write_dataset(self, storage: HistoryStorage, dataset: StoredDataset, path: Path, pa: Any, parquet: Any) -> None:
        page = storage.read_all(dataset)
        if not page.items:
            raise HistoryArchiveError("cannot archive an empty dataset")
        candles = page.items
        schema = pa.schema(
            [
                pa.field("open_time", pa.timestamp("us", tz="UTC")),
                pa.field("close_time", pa.timestamp("us", tz="UTC")),
                pa.field("open", pa.string()),
                pa.field("high", pa.string()),
                pa.field("low", pa.string()),
                pa.field("close", pa.string()),
                pa.field("volume", pa.string()),
                pa.field("quote_volume", pa.string()),
                pa.field("trade_count", pa.int64()),
            ]
        )
        arrays = [
            pa.array([candle.open_time for candle in candles], type=schema.field("open_time").type),
            pa.array([candle.close_time for candle in candles], type=schema.field("close_time").type),
            pa.array([str(candle.open) for candle in candles], type=pa.string()),
            pa.array([str(candle.high) for candle in candles], type=pa.string()),
            pa.array([str(candle.low) for candle in candles], type=pa.string()),
            pa.array([str(candle.close) for candle in candles], type=pa.string()),
            pa.array([str(candle.volume) for candle in candles], type=pa.string()),
            pa.array([str(candle.quote_volume) for candle in candles], type=pa.string()),
            pa.array([candle.trade_count for candle in candles], type=pa.int64()),
        ]
        metadata = {
            b"crypto_dataset_id": dataset.manifest.dataset_id.encode("utf-8"),
            b"crypto_content_sha256": dataset.manifest.content_sha256.encode("ascii"),
            b"crypto_row_count": str(dataset.manifest.row_count).encode("ascii"),
            b"crypto_storage_key": dataset.storage_key.encode("utf-8"),
        }
        table = pa.Table.from_arrays(arrays, schema=schema).replace_schema_metadata(metadata)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
            parquet.write_table(table, temp_name, compression="zstd")
            with open(temp_name, "rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _metadata_value(metadata: dict[bytes, bytes], key: str) -> str | None:
        value = metadata.get(key.encode("ascii"))
        return value.decode("utf-8") if value is not None else None

    def _item(
        self,
        dataset: StoredDataset,
        path: Path,
        state: str,
        *,
        include_digest: bool = False,
    ) -> dict[str, object]:
        try:
            archive_key = path.relative_to(self.history_root).as_posix()
        except ValueError:
            archive_key = str(path)
        return {
            "dataset_id": dataset.manifest.dataset_id,
            "storage_key": dataset.storage_key,
            "archive_key": archive_key,
            "state": state,
            "row_count": dataset.manifest.row_count,
            "size_bytes": path.stat().st_size if path.exists() else 0,
            "archive_sha256": self._file_sha256(path) if include_digest and path.exists() else None,
        }

    def _relative_archive_root(self) -> str:
        try:
            return self.archive_root.relative_to(self.history_root).as_posix()
        except ValueError:
            return str(self.archive_root)

    @staticmethod
    def _file_sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
