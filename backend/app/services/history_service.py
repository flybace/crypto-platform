"""Runtime composition for public history gateways."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from adapters.venues.binance_history import BinanceSpotHistoryGateway
from adapters.venues.bybit_history import BybitSpotHistoryGateway
from adapters.venues.httpx_transport import HttpxJsonTransport
from adapters.venues.okx_history import OkxSpotHistoryGateway
from adapters.venues.api_routes import normalize_api_routes
from application.history_download import HistoryDownloadService
from application.history_response_archive import HistoryResponseArchive
from application.history_storage import HistoryStorage

from ..settings import Settings
from .history_metadata import HistoryMetadataRepository


def build_history_service(
    settings: Settings,
    *,
    data_root: str | Path | None = None,
    metadata_writer: HistoryMetadataRepository | None = None,
    api_routes: Mapping[str, Mapping[str, str | None]] | None = None,
) -> HistoryDownloadService:
    return build_public_history_service(
        data_root=data_root if data_root is not None else settings.history_data_path,
        timeout_seconds=settings.history_timeout_seconds,
        page_pause_seconds=settings.history_page_pause_seconds,
        max_pages=settings.history_max_pages,
        trust_env=settings.history_trust_env,
        binance_base_url=settings.binance_history_base_url,
        okx_base_url=settings.okx_history_base_url,
        bybit_base_url=settings.bybit_history_base_url,
        proxies={
            "binance": settings.binance_public_http_proxy,
            "okx": settings.okx_public_http_proxy,
            "bybit": settings.bybit_public_http_proxy,
        },
        api_routes=api_routes,
        metadata_writer=metadata_writer,
    )


def build_public_history_service(
    *,
    data_root: str | Path,
    timeout_seconds: float = 10.0,
    page_pause_seconds: float = 0.05,
    max_pages: int = 2000,
    trust_env: bool = False,
    binance_base_url: str = "https://data-api.binance.vision",
    okx_base_url: str = "https://www.okx.com",
    bybit_base_url: str = "https://api.bybit-tr.com",
    proxies: Mapping[str, str | None] | None = None,
    api_routes: Mapping[str, Mapping[str, str | None]] | None = None,
    metadata_writer: HistoryMetadataRepository | None = None,
) -> HistoryDownloadService:
    root = data_root
    configured_proxies = {str(key).strip().lower(): value for key, value in (proxies or {}).items()}
    configured_routes = normalize_api_routes(api_routes or {})
    def build_transport(base_url: str, venue_id: str) -> HttpxJsonTransport:
        return HttpxJsonTransport(
            base_url,
            timeout_seconds=timeout_seconds,
            trust_env=trust_env,
            proxy=configured_proxies.get(venue_id),
        )

    binance_transport = build_transport(binance_base_url, "binance")
    okx_transport = build_transport(okx_base_url, "okx")
    bybit_transport = build_transport(bybit_base_url, "bybit")
    gateways = {
        "binance": BinanceSpotHistoryGateway(
            binance_transport,
            max_pages=max_pages,
            pause_seconds=page_pause_seconds,
            api_routes=configured_routes["binance"],
        ),
        "okx": OkxSpotHistoryGateway(
            okx_transport,
            max_pages=max_pages,
            pause_seconds=page_pause_seconds,
            api_routes=configured_routes["okx"],
        ),
        "bybit": BybitSpotHistoryGateway(
            bybit_transport,
            max_pages=max_pages,
            pause_seconds=page_pause_seconds,
            api_routes=configured_routes["bybit"],
        ),
    }
    storage = HistoryStorage(root)
    return HistoryDownloadService(
        gateways,
        storage,
        raw_archive=HistoryResponseArchive(storage.root),
        metadata_writer=metadata_writer,
    )
