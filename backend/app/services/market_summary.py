"""Build bounded market summaries from verified historical candles."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable

from application.history_storage import HistoryStorage, HistoryStorageError, StoredDataset


SUPPORTED_INTERVALS = ("1d", "1h", "5m")
VENUE_LABELS = {"binance": "Binance", "okx": "OKX", "bybit": "Bybit"}
LOOKBACK_BY_INTERVAL = {"1d": 2, "1h": 25, "5m": 289}


def build_market_summary(
    storage: HistoryStorage,
    *,
    interval: str = "1h",
    expected_dataset_count: int = 27,
) -> dict[str, object]:
    """Return the data-driven overview used by the standalone workspace.

    The summary intentionally reads only quality-passed datasets. It is a
    research snapshot, not an executable quote or an arbitrage signal.
    """
    normalized_interval = str(interval).strip().lower()
    if normalized_interval not in SUPPORTED_INTERVALS:
        raise ValueError("interval must be one of 1d, 1h, 5m")

    datasets = storage.list_datasets()
    verified = [item for item in datasets if not item.manifest.gap_count and not item.manifest.duplicate_count]
    rows = _latest_rows(storage, verified, normalized_interval)
    spreads = _spreads(rows)
    latest_at = max((str(row["close_time"]) for row in rows), default=None)
    verified_count = len(verified)
    row_count = sum(item.manifest.row_count for item in verified)
    symbols = sorted({str(row["symbol"]) for row in rows})
    venues = sorted({str(row["venue_id"]) for row in rows})

    return {
        "interval": normalized_interval,
        "as_of": latest_at,
        "research_only": True,
        "coverage": {
            "verified_dataset_count": verified_count,
            "expected_dataset_count": max(0, int(expected_dataset_count)),
            "row_count": row_count,
            "coverage_ratio_pct": _ratio(verified_count, expected_dataset_count),
            "quality_blocked_dataset_count": len(datasets) - verified_count,
        },
        "venue_count": len(venues),
        "venues": [_venue_summary(venue_id, rows) for venue_id in venues],
        "symbols": symbols,
        "quotes": rows,
        "top_gainers": sorted(rows, key=lambda row: _decimal(row["change_pct"]), reverse=True)[:8],
        "top_losers": sorted(rows, key=lambda row: _decimal(row["change_pct"]))[:8],
        "spreads": spreads,
    }


def build_market_rankings(
    storage: HistoryStorage,
    *,
    interval: str = "1h",
    rank_by: str = "rise",
    limit: int = 20,
) -> dict[str, object]:
    """Return a compact ranking projection without duplicating calculation rules."""
    summary = build_market_summary(storage, interval=interval)
    normalized_rank = str(rank_by).strip().lower()
    if normalized_rank not in {"rise", "fall", "spread"}:
        raise ValueError("rank_by must be rise, fall, or spread")
    if normalized_rank == "rise":
        items = summary["top_gainers"]
    elif normalized_rank == "fall":
        items = summary["top_losers"]
    else:
        items = sorted(summary["spreads"], key=lambda row: _decimal(row["spread_pct"]), reverse=True)
    bounded = list(items)[: max(1, min(int(limit), 100))]
    return {
        "interval": summary["interval"],
        "rank_by": normalized_rank,
        "as_of": summary["as_of"],
        "items": bounded,
        "count": len(bounded),
        "research_only": True,
    }


def _latest_rows(
    storage: HistoryStorage,
    datasets: Iterable[StoredDataset],
    interval: str,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    limit = LOOKBACK_BY_INTERVAL[interval]
    for dataset in datasets:
        if dataset.manifest.interval != interval:
            continue
        page = storage.read_page(dataset, limit=limit, tail=True)
        if not page.items:
            continue
        latest = page.items[-1]
        reference = page.items[0] if len(page.items) > 1 else latest
        if interval == "1h" and len(page.items) > 24:
            reference = page.items[-25]
        elif interval == "5m" and len(page.items) > 288:
            reference = page.items[-289]
        change_pct = _change_pct(reference.close, latest.close)
        symbol = dataset.manifest.instrument_key.rsplit(":", 1)[-1]
        result.append(
            {
                "venue_id": dataset.manifest.venue_id,
                "venue_name": VENUE_LABELS.get(dataset.manifest.venue_id, dataset.manifest.venue_id.upper()),
                "symbol": symbol,
                "interval": interval,
                "dataset_id": dataset.manifest.dataset_id,
                "close": str(latest.close),
                "change_pct": str(change_pct),
                "quote_volume": str(latest.quote_volume),
                "open_time": latest.open_time.isoformat(),
                "close_time": latest.close_time.isoformat(),
                "row_count": dataset.manifest.row_count,
                "data_start_at": dataset.manifest.start_at.isoformat(),
                "data_end_at": dataset.manifest.end_at.isoformat(),
            }
        )
    # Put the cheapest verified venue first for each symbol so the overview
    # reads in the same order as the cross-market spread calculation.
    return sorted(result, key=lambda row: (str(row["symbol"]), _decimal(row["close"]), str(row["venue_id"])))


def _spreads(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["symbol"])].append(row)
    result: list[dict[str, object]] = []
    for symbol, candidates in sorted(grouped.items()):
        if len(candidates) < 2:
            continue
        buy = min(candidates, key=lambda row: _decimal(row["close"]))
        sell = max(candidates, key=lambda row: _decimal(row["close"]))
        buy_price = _decimal(buy["close"])
        sell_price = _decimal(sell["close"])
        if buy_price <= 0 or sell_price <= 0:
            continue
        result.append(
            {
                "symbol": symbol,
                "interval": str(buy["interval"]),
                "buy_venue_id": buy["venue_id"],
                "buy_venue_name": buy["venue_name"],
                "sell_venue_id": sell["venue_id"],
                "sell_venue_name": sell["venue_name"],
                "buy_close": str(buy_price),
                "sell_close": str(sell_price),
                "spread_pct": str((sell_price / buy_price - Decimal("1")) * Decimal("100")),
                "venue_count": len(candidates),
                "research_only": True,
            }
        )
    return sorted(result, key=lambda row: _decimal(row["spread_pct"]), reverse=True)


def _venue_summary(venue_id: str, rows: list[dict[str, object]]) -> dict[str, object]:
    scoped = [row for row in rows if row["venue_id"] == venue_id]
    return {
        "venue_id": venue_id,
        "venue_name": VENUE_LABELS.get(venue_id, venue_id.upper()),
        "dataset_count": len(scoped),
        "symbols": sorted({str(row["symbol"]) for row in scoped}),
        "latest_at": max((str(row["close_time"]) for row in scoped), default=None),
    }


def _change_pct(reference: Decimal, latest: Decimal) -> Decimal:
    if reference <= 0:
        return Decimal("0")
    return (latest / reference - Decimal("1")) * Decimal("100")


def _decimal(value: object) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise HistoryStorageError("market summary encountered a non-numeric candle value") from error
    if not parsed.is_finite():
        raise HistoryStorageError("market summary encountered a non-finite candle value")
    return parsed


def _ratio(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0.00"
    return str((Decimal(numerator) / Decimal(denominator) * Decimal("100")).quantize(Decimal("0.01")))
