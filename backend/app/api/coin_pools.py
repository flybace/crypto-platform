"""动态币池 API：创建 / 刷新 / 人工确认 / 时间点成分。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from ..auth.dependencies import require_user
from ..services.coin_pools import CoinPoolError, CoinPoolService


router = APIRouter(prefix="/api/v1/coin-pools", tags=["coin-pools"])


class CoinPoolCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    pool_type: Literal["static", "top_volume", "low_volatility"]
    venue_id: str = Field(min_length=1, max_length=32)
    rule: dict = Field(default_factory=dict)
    symbols: list[str] = Field(default_factory=list, max_length=100)
    refresh_interval_seconds: int = Field(default=3600, ge=300, le=86400)


def _service(request: Request) -> CoinPoolService:
    service = getattr(request.app.state, "coin_pools", None)
    if service is None:
        raise HTTPException(status_code=503, detail="coin pool service is unavailable")
    return service


@router.get("")
def list_pools(
    request: Request,
    role: Literal["candidate", "trading"] | None = None,
    pool_type: Literal["static", "top_volume", "low_volatility"] | None = None,
    _: object = Depends(require_user),
) -> dict:
    return {"items": _service(request).list(role=role, pool_type=pool_type)}


@router.get("/summary")
def summary(request: Request, _: object = Depends(require_user)) -> dict:
    return _service(request).summary()


@router.post("", status_code=status.HTTP_201_CREATED)
def create_pool(payload: CoinPoolCreateRequest, request: Request, _: object = Depends(require_user)) -> dict:
    try:
        return _service(request).create(
            name=payload.name,
            pool_type=payload.pool_type,
            venue_id=payload.venue_id,
            rule=payload.rule,
            symbols=payload.symbols,
            refresh_interval_seconds=payload.refresh_interval_seconds,
        )
    except CoinPoolError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/{pool_id}")
def get_pool(pool_id: str, request: Request, _: object = Depends(require_user)) -> dict:
    pool = _service(request).get(pool_id)
    if pool is None:
        raise HTTPException(status_code=404, detail="coin pool was not found")
    return pool


@router.post("/{pool_id}/refresh")
def refresh_pool(pool_id: str, request: Request, _: object = Depends(require_user)) -> dict:
    try:
        return _service(request).refresh(pool_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except CoinPoolError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{pool_id}/confirm")
def confirm_pool(pool_id: str, request: Request, _: object = Depends(require_user)) -> dict:
    """人工确认：候选池 → 交易池。"""
    try:
        return _service(request).confirm(pool_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except CoinPoolError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{pool_id}/demote")
def demote_pool(pool_id: str, request: Request, _: object = Depends(require_user)) -> dict:
    try:
        return _service(request).demote(pool_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/{pool_id}/composition")
def composition_at(
    pool_id: str,
    request: Request,
    as_of: str = Query(..., description="ISO datetime, e.g. 2026-10-01T00:00:00+00:00"),
    _: object = Depends(require_user),
) -> dict:
    try:
        return _service(request).composition_at(pool_id, as_of)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except CoinPoolError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.delete("/{pool_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pool(pool_id: str, request: Request, _: object = Depends(require_user)) -> None:
    try:
        _service(request).delete(pool_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
