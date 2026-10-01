"""Pull real public OHLCV history into the local ignored data directory."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from backend.app.services.history_requests import build_queries  # noqa: E402
from backend.app.services.history_service import build_public_history_service  # noqa: E402
from ports.rest import PublicRestError  # noqa: E402


def _date(value: str) -> datetime:
    raw = value.strip()
    if len(raw) == 10:
        raw = f"{raw}T00:00:00+00:00"
    else:
        raw = raw.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("date must include a timezone, or use YYYY-MM-DD")
    return parsed.astimezone(UTC)


def _defaults() -> tuple[datetime, datetime]:
    end_at = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return end_at - timedelta(days=365), end_at


def _parser() -> argparse.ArgumentParser:
    default_start, default_end = _defaults()
    parser = argparse.ArgumentParser(description="Download public crypto exchange K-lines")
    parser.add_argument("--venues", default="binance,okx,bybit", help="comma-separated venue ids")
    parser.add_argument("--symbols", default="BTC/USDT,ETH/USDT,BNB/USDT", help="comma-separated symbols")
    parser.add_argument("--interval", choices=("1d", "1h", "5m"), default="1d")
    parser.add_argument("--start", type=_date, default=default_start)
    parser.add_argument("--end", type=_date, default=default_end)
    parser.add_argument("--data-dir", default=str(ROOT / "data" / "history"))
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--pause", type=float, default=0.05)
    parser.add_argument("--max-pages", type=int, default=2000)
    parser.add_argument("--binance-base-url", default="https://data-api.binance.vision")
    parser.add_argument("--okx-base-url", default="https://www.okx.com")
    parser.add_argument("--bybit-base-url", default="https://api.bybit-tr.com")
    parser.add_argument("--no-env-proxy", action="store_true", help="ignore HTTP(S)_PROXY environment variables")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.end <= args.start:
        raise SystemExit("--end must be after --start")
    venues = [item.strip() for item in args.venues.split(",") if item.strip()]
    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
    try:
        queries = build_queries(venues, symbols, interval=args.interval, start_at=args.start, end_at=args.end)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    service = build_public_history_service(
        data_root=args.data_dir,
        timeout_seconds=args.timeout,
        page_pause_seconds=args.pause,
        max_pages=args.max_pages,
        trust_env=not args.no_env_proxy,
        binance_base_url=args.binance_base_url,
        okx_base_url=args.okx_base_url,
        bybit_base_url=args.bybit_base_url,
    )
    successes = failures = 0
    try:
        for query in queries:
            try:
                result = service.download(query)
            except PublicRestError as error:
                failures += 1
                print(json.dumps({"status": "blocked", "venue_id": query.venue_id, "symbol": query.native_symbol, "kind": error.kind, "message": str(error)}, ensure_ascii=False))
            except Exception as error:
                failures += 1
                print(json.dumps({"status": "failed", "venue_id": query.venue_id, "symbol": query.native_symbol, "kind": "DOWNLOAD_FAILED", "message": str(error)}, ensure_ascii=False))
            else:
                successes += 1
                manifest = result.dataset.manifest
                print(json.dumps({"status": "completed", "venue_id": query.venue_id, "symbol": query.native_symbol, "rows": manifest.row_count, "gaps": manifest.gap_count, "duplicates": manifest.duplicate_count, "sha256": manifest.content_sha256, "storage_key": result.dataset.storage_key}, ensure_ascii=False))
    finally:
        service.close()
    return 0 if failures == 0 else 2 if successes else 1


if __name__ == "__main__":
    raise SystemExit(main())
