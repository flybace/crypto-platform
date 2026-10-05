"""Authenticated runtime settings routes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..auth.dependencies import require_user
from ..services.public_network_settings import (
    PublicNetworkSettingsError,
    PublicNetworkSettingsStore,
    probe_public_http,
    probe_public_websocket,
    settings_with_public_network,
)


router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


class VenueProxyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_rest_base_url: str | None = Field(default=None, max_length=1000)
    public_ws_base_url: str | None = Field(default=None, max_length=1000)
    history_base_url: str | None = Field(default=None, max_length=1000)
    instruments_path: str | None = Field(default=None, max_length=500)
    tickers_path: str | None = Field(default=None, max_length=500)
    order_book_path: str | None = Field(default=None, max_length=500)
    candles_path: str | None = Field(default=None, max_length=500)
    time_path: str | None = Field(default=None, max_length=500)
    # Omitted fields keep their current value. The clear flags make it
    # possible to reset an endpoint or remove a credential-bearing proxy.
    http_proxy: str | None = Field(default=None, max_length=500)
    ws_proxy: str | None = Field(default=None, max_length=500)
    clear_public_rest_base_url: bool = False
    clear_public_ws_base_url: bool = False
    clear_history_base_url: bool = False
    clear_instruments_path: bool = False
    clear_tickers_path: bool = False
    clear_order_book_path: bool = False
    clear_candles_path: bool = False
    clear_time_path: bool = False
    clear_http_proxy: bool = False
    clear_ws_proxy: bool = False


class PublicNetworkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    venues: dict[str, VenueProxyUpdate] = Field(default_factory=dict)


class PublicNetworkProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    venue_id: Literal["binance", "okx", "bybit"]
    channel: Literal["HTTP", "WebSocket"] = "HTTP"


def _store(request: Request) -> PublicNetworkSettingsStore:
    return request.app.state.public_network_settings


def _apply(request: Request, snapshot) -> list[str]:
    current = request.app.state.settings
    request.app.state.settings = settings_with_public_network(current, snapshot)
    errors: list[str] = []
    history_service = getattr(request.app.state, "history_service", None)
    update_history_network = getattr(history_service, "update_network", None)
    if callable(update_history_network):
        try:
            update_history_network(
                snapshot.history_base_urls,
                snapshot.http_proxies,
                snapshot.api_routes,
            )
        except Exception:
            errors.append("history_service")
    else:
        update_history_endpoints = getattr(history_service, "update_endpoints", None)
        update_history_proxies = getattr(history_service, "update_proxies", None)
        try:
            if callable(update_history_endpoints):
                update_history_endpoints(snapshot.history_base_urls)
            if callable(update_history_proxies):
                update_history_proxies(snapshot.http_proxies)
        except Exception:
            errors.append("history_service")
    catalog = getattr(request.app.state, "instrument_catalog", None)
    update_catalog_network = getattr(catalog, "update_network", None)
    if callable(update_catalog_network):
        try:
            update_catalog_network(
                snapshot.public_rest_base_urls,
                snapshot.http_proxies,
                snapshot.api_routes,
            )
        except Exception:
            errors.append("instrument_catalog")
    else:
        update_catalog_endpoints = getattr(catalog, "update_endpoints", None)
        update_catalog_proxies = getattr(catalog, "update_proxies", None)
        try:
            if callable(update_catalog_endpoints):
                update_catalog_endpoints(snapshot.public_rest_base_urls)
            if callable(update_catalog_proxies):
                update_catalog_proxies(snapshot.http_proxies)
        except Exception:
            errors.append("instrument_catalog")
    ticker_service = getattr(request.app.state, "public_tickers", None)
    update_ticker_network = getattr(ticker_service, "update_network", None)
    if callable(update_ticker_network):
        try:
            update_ticker_network(
                snapshot.public_rest_base_urls,
                snapshot.http_proxies,
                snapshot.api_routes,
            )
        except Exception:
            errors.append("public_tickers")
    return errors


@router.get("/network")
def network_settings(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    store = _store(request)
    return store.public_view()


@router.put("/network")
def update_network_settings(
    payload: PublicNetworkUpdate,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        updates = {
            venue: values.model_dump(exclude_none=True)
            for venue, values in payload.venues.items()
        }
        snapshot = _store(request).save(updates)
    except (PublicNetworkSettingsError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    apply_errors = _apply(request, snapshot)
    result = _store(request).public_view(snapshot)
    result["applied"] = not apply_errors
    result["apply_errors"] = apply_errors
    return result


@router.post("/network/probe")
def probe_network(
    payload: PublicNetworkProbeRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        snapshot = _store(request).current()
        runtime_settings = settings_with_public_network(request.app.state.settings, snapshot)
        if payload.channel == "WebSocket":
            return probe_public_websocket(payload.venue_id, runtime_settings, network_snapshot=snapshot)
        return probe_public_http(payload.venue_id, runtime_settings, network_snapshot=snapshot)
    except (ValueError, PublicNetworkSettingsError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


class TradingToggleRequest(BaseModel):
    enabled: bool = Field(description="Explicitly enable or disable auto-trading")


def _trading_store(request: Request) -> "TradingSettingsStore":
    from ..services.trading_settings import TradingSettingsStore

    store = getattr(request.app.state, "trading_settings_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="trading settings store is not configured")
    return store


@router.get("/trading")
def trading_settings(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    """Current auto-trading toggle state. Defaults to disabled."""
    store = _trading_store(request)
    return {
        "auto_trading_enabled": store.is_enabled(),
        "note": "Global auto-trading switch, default off. Simulated orders are placed "
        "only when this is on AND the paper-live instance's own auto_trading flag is on. "
        "Real order execution is not implemented (M6).",
    }


@router.put("/trading")
def update_trading_settings(
    payload: TradingToggleRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Explicitly enable or disable auto-trading. Requires authentication."""
    store = _trading_store(request)
    enabled = store.set_enabled(payload.enabled)
    return {"auto_trading_enabled": enabled}
