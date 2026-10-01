"""Coverage reporting for the configured public-history baseline."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, ROUND_HALF_UP

from application.history_storage import HistoryStorage, StoredDataset


VENUE_IDS = ("binance", "okx", "bybit")
SYMBOLS = ("BTC/USDT", "ETH/USDT", "BNB/USDT")
INTERVALS = ("1d", "1h", "5m")


def build_history_coverage(
    storage: HistoryStorage,
    jobs: Iterable[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Return verified datasets plus every expected baseline series.

    A missing series is intentionally different from a quality-blocked series.
    The distinction is useful when an upstream exchange rejects a symbol or
    when a stored file fails its manifest/hash gate.
    """
    datasets = storage.list_datasets()
    by_key = {
        (item.manifest.venue_id, item.manifest.instrument_key, item.manifest.interval): item
        for item in datasets
    }
    by_venue: dict[str, dict[str, int]] = {}
    for item in datasets:
        venue = by_venue.setdefault(item.manifest.venue_id, {"datasets": 0, "rows": 0})
        venue["datasets"] += 1
        venue["rows"] += item.manifest.row_count

    attempts = _latest_attempts(jobs or ())
    matrix: list[dict[str, object]] = []
    for venue_id in VENUE_IDS:
        for symbol in SYMBOLS:
            instrument_key = f"{venue_id}:spot:{symbol}"
            for interval in INTERVALS:
                dataset = by_key.get((venue_id, instrument_key, interval))
                matrix.append(_matrix_item(storage, dataset, attempts, venue_id, symbol, interval))

    verified = sum(1 for item in matrix if item["status"] == "VERIFIED")
    blocked = sum(1 for item in matrix if item["status"] == "BLOCKED")
    failed = sum(1 for item in matrix if item["status"] == "FAILED")
    in_progress = sum(1 for item in matrix if item["status"] == "IN_PROGRESS")
    interrupted = sum(1 for item in matrix if item["status"] == "INTERRUPTED")
    expected = len(matrix)
    ratio = (Decimal(verified) / Decimal(expected) * Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return {
        "dataset_count": len(datasets),
        "row_count": sum(item.manifest.row_count for item in datasets),
        "by_venue": by_venue,
        "datasets": [storage.dataset_dict(item) for item in datasets],
        "expected_dataset_count": expected,
        "verified_dataset_count": verified,
        "blocked_dataset_count": blocked,
        "failed_dataset_count": failed,
        "in_progress_dataset_count": in_progress,
        "interrupted_dataset_count": interrupted,
        "missing_dataset_count": expected - verified - blocked - failed - in_progress - interrupted,
        "coverage_ratio_pct": str(ratio),
        "matrix": matrix,
    }


def _matrix_item(
    storage: HistoryStorage,
    dataset: StoredDataset | None,
    attempts: dict[tuple[str, str, str], dict[str, object]],
    venue_id: str,
    symbol: str,
    interval: str,
) -> dict[str, object]:
    base: dict[str, object] = {
        "venue_id": venue_id,
        "symbol": symbol,
        "interval": interval,
        "status": "MISSING",
        "dataset_id": None,
        "row_count": 0,
        "start_at": None,
        "end_at": None,
        "gap_count": 0,
        "duplicate_count": 0,
        "source": None,
        "reason": "未发现已校验数据集",
    }
    if dataset is None:
        attempt = attempts.get((venue_id, f"{venue_id}:spot:{symbol}", interval))
        if attempt is not None:
            _apply_attempt(base, attempt)
        return base

    manifest = dataset.manifest
    base.update(
        {
            "status": "BLOCKED" if manifest.gap_count or manifest.duplicate_count else "VERIFIED",
            "dataset_id": manifest.dataset_id,
            "row_count": manifest.row_count,
            "start_at": manifest.start_at.isoformat(),
            "end_at": manifest.end_at.isoformat(),
            "gap_count": manifest.gap_count,
            "duplicate_count": manifest.duplicate_count,
            "source": manifest.source,
            "reason": "数据质量门禁未通过"
            if manifest.gap_count or manifest.duplicate_count
            else "Manifest 与 SHA-256 校验通过",
        }
    )
    return base


def _latest_attempts(jobs: Iterable[dict[str, object]]) -> dict[tuple[str, str, str], dict[str, object]]:
    """Use the newest job item to explain a series without hiding missing data."""
    attempts: dict[tuple[str, str, str], dict[str, object]] = {}
    for job in jobs:
        items = job.get("items", []) if isinstance(job, dict) else []
        if not isinstance(items, list):
            continue
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            key = _attempt_key(raw_item)
            if key is not None and key not in attempts:
                attempts[key] = raw_item
    return attempts


def _attempt_key(item: dict[str, object]) -> tuple[str, str, str] | None:
    venue_id = str(item.get("venue_id", "")).strip().lower()
    instrument_key = str(item.get("instrument_key", "")).strip()
    interval = str(item.get("interval", "")).strip().lower()
    if not venue_id or not instrument_key or not interval:
        return None
    return venue_id, instrument_key, interval


def _apply_attempt(base: dict[str, object], attempt: dict[str, object]) -> None:
    raw_status = str(attempt.get("status", "")).strip().lower()
    status = {
        "blocked": "BLOCKED",
        "rejected": "BLOCKED",
        "failed": "FAILED",
        "queued": "IN_PROGRESS",
        "running": "IN_PROGRESS",
        "interrupted": "INTERRUPTED",
    }.get(raw_status)
    if status is None:
        return
    base["status"] = status
    error = attempt.get("error")
    if isinstance(error, dict):
        kind = str(error.get("kind", "")).strip()
        message = str(error.get("message", "")).strip()
        detail = ": ".join(value for value in (kind, message) if value)
        if detail:
            base["reason"] = detail
            return
    base["reason"] = {
        "BLOCKED": "公开数据源阻断",
        "FAILED": "历史下载失败",
        "IN_PROGRESS": "历史下载任务进行中",
        "INTERRUPTED": "历史下载任务被进程中断",
    }[status]
