"""Server-side risk configuration and hard-stop endpoints."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..auth.dependencies import require_user


router = APIRouter(prefix="/api/v1/risk", tags=["risk"])


class RiskPolicyRequest(BaseModel):
    enabled: bool = False
    mode: Literal["DISABLED", "SELL_ONLY", "BUY_SELL"] = "DISABLED"
    venue_allowlist: list[str] = Field(default_factory=list, max_length=3)
    instrument_allowlist: list[str] = Field(default_factory=list, max_length=100)
    quote_asset_allowlist: list[str] = Field(default_factory=lambda: ["USDT"], max_length=10)
    max_order_notional_quote: Decimal = Field(default=Decimal("0"), ge=0)
    max_daily_sell_notional_quote: Decimal = Field(default=Decimal("0"), ge=0)
    max_daily_sell_ratio: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    min_base_reserve: Decimal | None = Field(default=None, ge=0)
    allow_market_order: bool = False
    max_slippage_bps: Decimal = Field(default=Decimal("0"), ge=0)
    max_market_age_seconds: int = Field(default=2, ge=1, le=3600)
    max_open_orders: int = Field(default=1, ge=0, le=100)
    max_orders_per_minute: int = Field(default=1, ge=1, le=1000)


class EmergencyReleaseRequest(BaseModel):
    confirmation: bool = False


def _service(request: Request):
    return request.app.state.risk_policy


@router.get("/summary")
def summary(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return _service(request).summary(request.app.state.settings.execution_mode)


@router.get("/policy")
def policy(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return _service(request).policy()


@router.put("/policy")
def update_policy(payload: RiskPolicyRequest, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    values = payload.model_dump(mode="json")
    for key in ("max_order_notional_quote", "max_daily_sell_notional_quote", "max_daily_sell_ratio", "min_base_reserve", "max_slippage_bps"):
        if values[key] is not None:
            values[key] = str(values[key])
    try:
        _service(request).update(values)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _service(request).summary(request.app.state.settings.execution_mode)


@router.post("/emergency-stop")
def emergency_stop(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return _service(request).emergency_stop()


@router.post("/emergency-stop/release")
def release_emergency_stop(payload: EmergencyReleaseRequest, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    try:
        return _service(request).release_emergency_stop(payload.confirmation)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/events")
def events(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    _: object = Depends(require_user),
) -> dict[str, object]:
    items = _service(request).events(limit)
    return {"items": items, "count": len(items)}

