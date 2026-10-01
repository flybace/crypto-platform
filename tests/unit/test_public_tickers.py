from collections.abc import Mapping
from typing import Any

from backend.app.services.public_tickers import PublicTickerService, parse_public_tickers
from ports.rest import PublicRestError


class FakeTickerTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get_json_value(self, path: str, params: Mapping[str, str]) -> object:
        self.calls.append((path, dict(params)))
        return self.payload

    def close(self) -> None:
        return None


def test_public_tickers_normalize_binance_okx_and_bybit_payloads() -> None:
    binance = parse_public_tickers(
        "binance",
        [
            {
                "symbol": "BTCUSDT",
                "lastPrice": "101",
                "openPrice": "100",
                "highPrice": "105",
                "lowPrice": "95",
                "priceChangePercent": "1.0",
                "volume": "10",
                "quoteVolume": "1010",
                "closeTime": 1_767_268_800_000,
            }
        ],
    )
    okx = parse_public_tickers(
        "okx",
        {
            "code": "0",
            "data": [
                {
                    "instId": "BTC-USDT",
                    "last": "102",
                    "open24h": "100",
                    "high24h": "106",
                    "low24h": "94",
                    "vol24h": "11",
                    "volCcy24h": "1122",
                    "ts": "1767268800000",
                }
            ],
        },
    )
    bybit = parse_public_tickers(
        "bybit",
        {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "lastPrice": "103",
                        "prevPrice24h": "100",
                        "price24hPcnt": "0.03",
                        "highPrice24h": "107",
                        "lowPrice24h": "93",
                        "volume24h": "12",
                        "turnover24h": "1236",
                        "time": "1767268800000",
                    }
                ]
            },
        },
    )

    assert binance[0]["symbol"] == "BTC/USDT"
    assert binance[0]["change_pct"] == "1"
    assert okx[0]["change_pct"] == "2"
    assert bybit[0]["change_pct"] == "3"
    assert bybit[0]["quote_volume_24h"] == "1236"


def test_public_ticker_snapshot_aligns_prices_across_markets_and_caches() -> None:
    transports: dict[str, Any] = {
        "binance": FakeTickerTransport(
            [
                {
                    "symbol": "BTCUSDT",
                    "lastPrice": "101",
                    "openPrice": "100",
                    "highPrice": "105",
                    "lowPrice": "95",
                    "priceChangePercent": "1",
                    "volume": "10",
                    "quoteVolume": "1010",
                    "closeTime": 1_767_268_800_000,
                }
            ]
        ),
        "okx": FakeTickerTransport(
            {
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT",
                        "last": "102",
                        "open24h": "100",
                        "high24h": "106",
                        "low24h": "94",
                        "vol24h": "11",
                        "volCcy24h": "1122",
                        "ts": "1767268800000",
                    }
                ],
            }
        ),
        "bybit": FakeTickerTransport(
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "lastPrice": "103",
                            "prevPrice24h": "100",
                            "price24hPcnt": "0.03",
                            "highPrice24h": "107",
                            "lowPrice24h": "93",
                            "volume24h": "12",
                            "turnover24h": "1236",
                            "time": "1767268800000",
                        }
                    ]
                },
            }
        ),
    }
    service = PublicTickerService(
        base_urls={},
        proxies={},
        transports=transports,
    )

    first = service.snapshot(quote_asset="USDT", sort_by="spread", refresh=True)
    second = service.snapshot(quote_asset="USDT", sort_by="spread")

    item = first["items"][0]
    assert item["symbol"] == "BTC/USDT"
    assert item["available_venues"] == ["binance", "okx", "bybit"]
    assert item["best_venue_id"] == "binance"
    assert item["highest_venue_id"] == "bybit"
    assert item["spread_pct"] == "1.98019802"
    assert [venue["status"] for venue in second["venues"]] == ["CACHED", "CACHED", "CACHED"]
    assert len(transports["binance"].calls) == 1


def test_public_ticker_service_returns_blocked_venues_without_placeholder_prices() -> None:
    class FailingTransport:
        def get_json_value(self, path: str, params: Mapping[str, str]) -> object:
            raise PublicRestError("NETWORK_ERROR", "public endpoint unavailable")

    service = PublicTickerService(
        base_urls={},
        proxies={},
        transports={venue: FailingTransport() for venue in ("binance", "okx", "bybit")},
    )

    result = service.snapshot(quote_asset="USDT")

    assert result["items"] == []
    assert [venue["status"] for venue in result["venues"]] == ["BLOCKED", "BLOCKED", "BLOCKED"]


def test_public_ticker_snapshot_omits_missing_timestamps() -> None:
    payloads = {
        "binance": [
            {
                "symbol": "BTCUSDT",
                "lastPrice": "101",
                "openPrice": "100",
                "highPrice": "105",
                "lowPrice": "95",
                "priceChangePercent": "1",
                "volume": "10",
                "quoteVolume": "1010",
            }
        ],
        "okx": {"code": "0", "data": []},
        "bybit": {"retCode": 0, "result": {"list": []}},
    }
    service = PublicTickerService(
        base_urls={},
        proxies={},
        transports={
            venue: FakeTickerTransport(payload)
            for venue, payload in payloads.items()
        },
    )

    result = service.snapshot(quote_asset="USDT", refresh=True)

    assert result["as_of"] is None
    assert result["items"][0]["timestamp"] is None
