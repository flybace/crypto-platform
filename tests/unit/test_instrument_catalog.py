from collections.abc import Mapping
from typing import Any

from backend.app.services.instrument_catalog import InstrumentCatalogService, parse_okx_instruments
from ports.rest import PublicRestError


class FakeTransport:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get_json(self, path: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        self.calls.append((path, dict(params)))
        return self.responses.pop(0)


def test_binance_catalog_filters_spot_trading_usdt_and_reuses_cache(tmp_path) -> None:
    transport = FakeTransport(
        [
            {
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "status": "TRADING",
                        "isSpotTradingAllowed": True,
                        "baseAsset": "BTC",
                        "quoteAsset": "USDT",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                            {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                            {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
                        ],
                    },
                    {
                        "symbol": "ETHUSDT",
                        "status": "BREAK",
                        "baseAsset": "ETH",
                        "quoteAsset": "USDT",
                    },
                    {
                        "symbol": "BTCBUSD",
                        "status": "TRADING",
                        "baseAsset": "BTC",
                        "quoteAsset": "BUSD",
                    },
                ]
            }
        ]
    )
    service = InstrumentCatalogService(
        transports={"binance": transport},
        state_path=tmp_path / "catalog.json",
    )

    live = service.list("binance", search="btc")
    cached = service.list("binance", search="BTC")

    assert live["status"] == "LIVE"
    assert cached["status"] == "CACHED"
    assert live["total_count"] == 1
    item = live["items"][0]
    assert item["instrument_key"] == "binance:spot:BTC/USDT"
    assert item["canonical_symbol"] == "BTC/USDT"
    assert item["price_tick"] == "0.01"
    assert item["quantity_step"] == "0.001"
    assert item["min_notional"] == "10"
    assert len(transport.calls) == 1


def test_bybit_catalog_follows_cursor_until_the_last_page() -> None:
    transport = FakeTransport(
        [
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "status": "Trading",
                            "baseCoin": "BTC",
                            "quoteCoin": "USDT",
                            "priceFilter": {"tickSize": "0.1"},
                            "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "minOrderAmt": "5"},
                        }
                    ],
                    "nextPageCursor": "cursor-2",
                },
            },
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "symbol": "ETHUSDT",
                            "status": "Trading",
                            "baseCoin": "ETH",
                            "quoteCoin": "USDT",
                            "priceFilter": {"tickSize": "0.01"},
                            "lotSizeFilter": {"qtyStep": "0.01", "minOrderQty": "0.01", "minOrderAmt": "5"},
                        }
                    ],
                    "nextPageCursor": "",
                },
            },
        ]
    )
    service = InstrumentCatalogService(transports={"bybit": transport})

    result = service.list("bybit", limit=1)

    assert result["status"] == "LIVE"
    assert result["total_count"] == 2
    assert result["returned_count"] == 1
    assert result["truncated"] is True
    assert transport.calls[0][1] == {"category": "spot", "limit": "1000"}
    assert transport.calls[1][1] == {"category": "spot", "limit": "1000", "cursor": "cursor-2"}


def test_okx_catalog_excludes_non_spot_and_non_live_rows() -> None:
    parsed = parse_okx_instruments(
        {
            "code": "0",
            "data": [
                {"instType": "SPOT", "state": "live", "instId": "BTC-USDT", "baseCcy": "BTC", "quoteCcy": "USDT", "tickSz": "0.1", "lotSz": "0.001", "minSz": "0.001"},
                {"instType": "SWAP", "state": "live", "instId": "BTC-USDT-SWAP", "baseCcy": "BTC", "quoteCcy": "USDT"},
                {"instType": "SPOT", "state": "suspend", "instId": "ETH-USDT", "baseCcy": "ETH", "quoteCcy": "USDT"},
            ],
        }
    )

    assert len(parsed) == 1
    assert parsed[0]["native_symbol"] == "BTC-USDT"
    assert parsed[0]["canonical_symbol"] == "BTC/USDT"


def test_catalog_returns_blocked_without_fabricating_items() -> None:
    class FailingTransport:
        def get_json(self, path: str, params: Mapping[str, str]) -> Mapping[str, Any]:
            raise PublicRestError("NETWORK_ERROR", "public endpoint unavailable")

    service = InstrumentCatalogService(transports={"okx": FailingTransport()})

    result = service.list("okx")

    assert result["status"] == "BLOCKED"
    assert result["items"] == []
    assert result["last_error"] == {
        "kind": "NETWORK_ERROR",
        "status_code": None,
        "retry_after_seconds": None,
    }
