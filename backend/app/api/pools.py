"""Authenticated coin-pool endpoints backed by verified history."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from application.pool_catalog import PoolCatalog

from ..auth.dependencies import require_user


router = APIRouter(prefix="/api/v1/pools", tags=["pools"])


class PoolCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["research", "backtest", "paper"]
    venue_ids: list[str] = Field(min_length=1, max_length=3)
    symbols: list[str] = Field(min_length=1, max_length=100)
    interval: Literal["all", "1d", "1h", "5m"] = "all"
    description: str = Field(default="", max_length=240)


def _catalog(request: Request) -> PoolCatalog:
    return request.app.state.pool_catalog


@router.get("/summary")
def summary(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    try:
        return _catalog(request).summary()
    except Exception as error:
        raise HTTPException(status_code=503, detail="pool catalog is unavailable") from error


@router.get("")
def pools(
    request: Request,
    kind: Literal["research", "backtest", "paper"] | None = None,
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        items = _catalog(request).list(kind)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=503, detail="pool catalog is unavailable") from error
    return {"items": items, "count": len(items)}


@router.get("/{pool_id}")
def pool(pool_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    item = _catalog(request).get(pool_id)
    if item is None:
        raise HTTPException(status_code=404, detail="pool was not found")
    return item


@router.post("", status_code=status.HTTP_201_CREATED)
def create_pool(payload: PoolCreateRequest, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    try:
        return _catalog(request).create(
            name=payload.name,
            kind=payload.kind,
            venue_ids=payload.venue_ids,
            symbols=payload.symbols,
            interval=payload.interval,
            description=payload.description,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{pool_id}/refresh")
def refresh_pool(pool_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    try:
        return _catalog(request).refresh(pool_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="pool was not found") from error
    except Exception as error:
        raise HTTPException(status_code=503, detail="pool refresh is unavailable") from error

