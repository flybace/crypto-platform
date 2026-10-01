from datetime import UTC, datetime, timedelta

import pytest

from backend.app.services.history_requests import build_queries, normalize_symbol


def test_normalize_symbol_uses_venue_native_formats() -> None:
    assert normalize_symbol("binance", "btc/usdt") == ("BTC", "USDT", "BTCUSDT")
    assert normalize_symbol("okx", "BTCUSDT") == ("BTC", "USDT", "BTC-USDT")
    assert normalize_symbol("bybit", "ETH-USDC") == ("ETH", "USDC", "ETHUSDC")


def test_build_queries_rejects_unknown_venue() -> None:
    with pytest.raises(ValueError, match="unsupported venue"):
        build_queries(
            ["coinbase"],
            ["BTC/USDT"],
            interval="1d",
            start_at=datetime(2026, 1, 1, tzinfo=UTC),
            end_at=datetime(2026, 1, 2, tzinfo=UTC),
        )


def test_build_queries_creates_one_query_per_venue_and_symbol() -> None:
    queries = build_queries(
        ["binance", "okx"],
        ["BTC/USDT", "ETH/USDT"],
        interval="1h",
        start_at=datetime(2026, 1, 1, tzinfo=UTC),
        end_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert len(queries) == 4
    assert queries[3].native_symbol == "ETH-USDT"
    assert queries[3].interval.value == "1h"
