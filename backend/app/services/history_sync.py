"""Plan and submit incremental public-history synchronization jobs."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore
from application.history_storage import HistoryStorage, HistoryStorageError
from domain.candle import CandleInterval, HistoryQuery
from domain.market import MarketType

from .history_requests import normalize_symbol


DEFAULT_VENUES = ("binance", "okx", "bybit")
DEFAULT_SYMBOLS = ("BTC/USDT", "ETH/USDT", "BNB/USDT")
DEFAULT_INTERVALS = ("1d", "1h", "5m")
SUPPORTED_MODES = frozenset({"incremental", "backfill"})


class HistorySyncError(ValueError):
    """Raised when a history synchronization request is invalid."""


class HistorySyncService:
    """Turn verified datasets into bounded, repeatable downloader requests.

    The service does not download data itself. It only derives windows from
    the current files and delegates actual network work to HistoryJobManager.
    This keeps planning deterministic and preserves the existing per-item
    error, retry, cancellation, and manifest gates.
    """

    def __init__(
        self,
        storage: HistoryStorage,
        *,
        state_path: str | Path | None = None,
        state_store: StateStore | None = None,
        default_lookback_days: int = 365,
    ) -> None:
        if int(default_lookback_days) <= 0:
            raise ValueError("default_lookback_days must be positive")
        self._storage = storage
        self._state = state_store if state_store is not None else (
            JsonStateStore(state_path) if state_path else None
        )
        self._default_lookback_days = int(default_lookback_days)
        self._last_plan: dict[str, object] | None = None
        self._last_job_id: str | None = None
        self._lock = RLock()
        self._load()

    def plan(
        self,
        *,
        mode: str = "incremental",
        venues: Iterable[str] = DEFAULT_VENUES,
        symbols: Iterable[str] = DEFAULT_SYMBOLS,
        intervals: Iterable[str] = DEFAULT_INTERVALS,
        lookback_days: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, object]:
        normalized_mode = self._mode(mode)
        normalized_venues = self._values(venues, "venues", allowed=DEFAULT_VENUES)
        normalized_symbols = self._values(symbols, "symbols", maximum=20)
        normalized_intervals = self._values(intervals, "intervals", allowed=DEFAULT_INTERVALS)
        days = self._lookback(lookback_days)
        as_of = self._utc(now or datetime.now(UTC))
        end_at = self._floor(as_of, "5m")
        with self._lock:
            self._load()
        items: list[dict[str, object]] = []
        for venue_id in normalized_venues:
            for symbol in normalized_symbols:
                base, quote, native = normalize_symbol(venue_id, symbol)
                canonical = f"{base}/{quote}"
                instrument_key = f"{venue_id}:spot:{canonical}"
                for interval in normalized_intervals:
                    items.append(
                        self._target(
                            mode=normalized_mode,
                            venue_id=venue_id,
                            symbol=canonical,
                            native_symbol=native,
                            instrument_key=instrument_key,
                            interval=interval,
                            end_at=end_at,
                            lookback_days=days,
                            )
                        )
        verified_items = [
            item
            for item in items
            if isinstance(item.get("existing"), dict)
            and int(item["existing"].get("gap_count", 0) or 0) == 0
            and int(item["existing"].get("duplicate_count", 0) or 0) == 0
        ]
        missing_count = sum(1 for item in items if item.get("existing") is None)
        quality_blocked_count = sum(
            1
            for item in items
            if isinstance(item.get("existing"), dict)
            and (
                int(item["existing"].get("gap_count", 0) or 0) > 0
                or int(item["existing"].get("duplicate_count", 0) or 0) > 0
            )
        )
        download_count = sum(
            1
            for item in items
            if item["status"] in {"MISSING", "STALE", "BACKFILL_REQUIRED", "QUALITY_BLOCKED"}
        )
        plan = {
            "plan_id": self._plan_id(normalized_mode, normalized_venues, normalized_symbols, normalized_intervals, end_at),
            "mode": normalized_mode,
            "created_at": as_of.isoformat(),
            "end_at": end_at.isoformat(),
            "lookback_days": days,
            "target_count": len(items),
            "download_count": download_count,
            "up_to_date_count": sum(1 for item in items if item["status"] == "UP_TO_DATE"),
            "verified_dataset_count": len(verified_items),
            "verified_row_count": sum(
                int(item["existing"].get("row_count", 0) or 0)
                for item in verified_items
            ),
            "missing_count": missing_count,
            "quality_blocked_count": quality_blocked_count,
            "status": "UP_TO_DATE" if download_count == 0 else "ACTIONABLE",
            "items": items,
            "last_job_id": self._last_job_id,
        }
        with self._lock:
            self._load()
            plan["last_job_id"] = self._last_job_id
            self._last_plan = deepcopy(plan)
            self._persist_locked()
        return plan

    def submit(
        self,
        job_manager: Any,
        *,
        mode: str = "incremental",
        venues: Iterable[str] = DEFAULT_VENUES,
        symbols: Iterable[str] = DEFAULT_SYMBOLS,
        intervals: Iterable[str] = DEFAULT_INTERVALS,
        lookback_days: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, object]:
        plan = self.plan(
            mode=mode,
            venues=venues,
            symbols=symbols,
            intervals=intervals,
            lookback_days=lookback_days,
            now=now,
        )
        queries = tuple(self._query(item) for item in plan["items"] if item.get("download") is True)
        job: dict[str, object] | None = None
        if queries:
            job = job_manager.submit(queries)
            with self._lock:
                self._last_job_id = str(job.get("job_id"))
                plan["last_job_id"] = self._last_job_id
                self._last_plan = deepcopy(plan)
                self._persist_locked()
        return {
            "plan": plan,
            "job": job,
            "status": "submitted" if job else "up_to_date",
        }

    def last_plan(self) -> dict[str, object] | None:
        with self._lock:
            self._load()
            return deepcopy(self._last_plan)

    def _target(
        self,
        *,
        mode: str,
        venue_id: str,
        symbol: str,
        native_symbol: str,
        instrument_key: str,
        interval: str,
        end_at: datetime,
        lookback_days: int,
    ) -> dict[str, object]:
        target_end_at = self._floor(end_at, interval)
        dataset = self._find(instrument_key, venue_id, interval)
        requested_start = self._floor(target_end_at - timedelta(days=lookback_days), interval)
        status = "MISSING"
        start_at = requested_start
        existing: dict[str, object] | None = None
        reason = "未发现已校验数据集，将执行首次历史下载"
        if dataset is not None:
            manifest = dataset.manifest
            existing = {
                "dataset_id": manifest.dataset_id,
                "row_count": manifest.row_count,
                "start_at": manifest.start_at.isoformat(),
                "end_at": manifest.end_at.isoformat(),
                "gap_count": manifest.gap_count,
                "duplicate_count": manifest.duplicate_count,
            }
            quality_blocked = bool(manifest.gap_count or manifest.duplicate_count)
            if mode == "backfill" and manifest.start_at > requested_start:
                status = "BACKFILL_REQUIRED"
                start_at = requested_start
                reason = "已存在数据，但早于目标起点的历史区间尚未覆盖"
            elif manifest.end_at < target_end_at:
                status = "QUALITY_BLOCKED" if quality_blocked else "STALE"
                start_at = manifest.end_at
                reason = "已有数据质量未通过，将用公开源重建缺口" if quality_blocked else "已有数据未覆盖到当前归档边界"
            elif quality_blocked:
                status = "QUALITY_BLOCKED"
                start_at = requested_start if mode == "backfill" else manifest.start_at
                reason = "已有 Manifest 的连续性或重复检查未通过"
            else:
                status = "UP_TO_DATE"
                start_at = manifest.end_at
                reason = "已覆盖到当前完整周期，无需重复下载"
        download = status in {"MISSING", "STALE", "BACKFILL_REQUIRED", "QUALITY_BLOCKED"} and start_at < target_end_at
        if not download and status not in {"UP_TO_DATE", "QUALITY_BLOCKED"}:
            status = "UP_TO_DATE"
            reason = "目标窗口没有可下载的完整周期"
        return {
            "venue_id": venue_id,
            "symbol": symbol,
            "native_symbol": native_symbol,
            "instrument_key": instrument_key,
            "interval": interval,
            "status": status,
            "download": download,
            "start_at": start_at.isoformat(),
            "end_at": target_end_at.isoformat(),
            "existing": existing,
            "reason": reason,
        }

    def _find(self, instrument_key: str, venue_id: str, interval: str):
        try:
            return self._storage.find_dataset(
                venue_id=venue_id,
                instrument_key=instrument_key,
                interval=interval,
            )
        except HistoryStorageError as error:
            raise HistorySyncError("history storage cannot be trusted while building sync plan") from error

    @staticmethod
    def _query(item: dict[str, object]) -> HistoryQuery:
        try:
            return HistoryQuery(
                venue_id=str(item["venue_id"]),
                market_type=MarketType.SPOT,
                instrument_key=str(item["instrument_key"]),
                native_symbol=str(item["native_symbol"]),
                interval=CandleInterval.parse(str(item["interval"])),
                start_at=datetime.fromisoformat(str(item["start_at"])),
                end_at=datetime.fromisoformat(str(item["end_at"])),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise HistorySyncError("sync plan contains an invalid download window") from error

    def _load(self) -> None:
        if self._state is None:
            return
        try:
            payload = self._state.load({"version": 1, "last_plan": None, "last_job_id": None})
        except JsonStateError as error:
            raise RuntimeError("history sync state is unreadable") from error
        if not isinstance(payload, dict):
            raise RuntimeError("history sync state must be an object")
        plan = payload.get("last_plan")
        self._last_plan = deepcopy(plan) if isinstance(plan, dict) else None
        job_id = payload.get("last_job_id")
        self._last_job_id = str(job_id) if job_id else None

    def _persist_locked(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save({"version": 1, "last_plan": self._last_plan, "last_job_id": self._last_job_id})
        except JsonStateError as error:
            raise RuntimeError("history sync state cannot be saved") from error

    @staticmethod
    def _mode(value: str) -> str:
        mode = str(value).strip().lower()
        if mode not in SUPPORTED_MODES:
            raise HistorySyncError("mode must be incremental or backfill")
        return mode

    @staticmethod
    def _values(values: Iterable[str], name: str, *, allowed: Iterable[str] | None = None, maximum: int = 20) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(str(value).strip().lower() for value in values if str(value).strip()))
        if not normalized:
            raise HistorySyncError(f"{name} must contain at least one value")
        if len(normalized) > maximum:
            raise HistorySyncError(f"{name} contains too many values")
        if allowed is not None:
            invalid = sorted(set(normalized) - set(allowed))
            if invalid:
                raise HistorySyncError(f"unsupported {name}: {', '.join(invalid)}")
        return normalized

    def _lookback(self, value: int | None) -> int:
        if value is None:
            return self._default_lookback_days
        try:
            days = int(value)
        except (TypeError, ValueError) as error:
            raise HistorySyncError("lookback_days must be an integer") from error
        if days <= 0 or days > 3650:
            raise HistorySyncError("lookback_days must be between 1 and 3650")
        return days

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise HistorySyncError("now must contain timezone information")
        return value.astimezone(UTC)

    @staticmethod
    def _floor(value: datetime, interval: str) -> datetime:
        parsed = CandleInterval.parse(interval)
        milliseconds = parsed.milliseconds
        epoch = int(value.astimezone(UTC).timestamp() * 1000)
        return datetime.fromtimestamp((epoch // milliseconds) * milliseconds / 1000, tz=UTC)

    @staticmethod
    def _plan_id(mode: str, venues: tuple[str, ...], symbols: tuple[str, ...], intervals: tuple[str, ...], end_at: datetime) -> str:
        scope = ",".join((*venues, *symbols, *intervals))
        digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:12]
        return f"history-sync:{mode}:{end_at.isoformat()}:{digest}"
