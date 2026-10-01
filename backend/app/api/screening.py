"""Authenticated OHLCV screening endpoints."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from application.history_storage import HistoryStorageError

from ..auth.dependencies import require_user
from ..services.task_dispatcher import TaskDispatchError, TaskDispatcher


router = APIRouter(prefix="/api/v1/screening", tags=["screening"])


class ScreeningRequest(BaseModel):
    pool_id: str = Field(min_length=1, max_length=120)
    interval: Literal["all", "1d", "1h", "5m"] = "all"
    lookback: int = Field(default=90, ge=2, le=10000)
    min_return_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100000)
    max_volatility_pct: Decimal | None = Field(default=None, ge=0, le=100000)
    min_quote_volume: Decimal = Field(default=Decimal("0"), ge=0)
    min_momentum_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100000)
    limit: int = Field(default=50, ge=1, le=500)

    @field_validator("min_quote_volume", mode="before")
    @classmethod
    def finite_volume(cls, value):
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError) as error:
            raise ValueError("min_quote_volume must be numeric") from error
        if not parsed.is_finite():
            raise ValueError("min_quote_volume must be finite")
        return parsed


def _service(request: Request):
    return request.app.state.screening_service


@router.get("/runs")
def runs(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    _: object = Depends(require_user),
) -> dict[str, object]:
    items = _service(request).list(limit)
    return {"items": items, "count": len(items)}


@router.get("/runs/{run_id}")
def run(run_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    item = _service(request).get(run_id)
    if item is None:
        raise HTTPException(status_code=404, detail="screening run was not found")
    return item


@router.post("/run", status_code=status.HTTP_201_CREATED)
def screen(payload: ScreeningRequest, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    dispatcher = request.app.state.task_dispatcher
    if dispatcher.enabled:
        if request.app.state.pool_catalog.get(payload.pool_id) is None:
            raise HTTPException(status_code=404, detail="pool was not found")
        run_id = dispatcher.run_id()
        task_payload = {
            "run_id": run_id,
            "pool_id": payload.pool_id,
            "interval": payload.interval,
            "lookback": payload.lookback,
            "min_return_pct": str(payload.min_return_pct),
            "max_volatility_pct": None if payload.max_volatility_pct is None else str(payload.max_volatility_pct),
            "min_quote_volume": str(payload.min_quote_volume),
            "min_momentum_pct": str(payload.min_momentum_pct),
            "limit": payload.limit,
        }
        try:
            queued = dispatcher.dispatch(
                "screening",
                "历史指标筛选",
                task_payload,
                task_id=TaskDispatcher.task_id("screening", run_id),
            )
        except TaskDispatchError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=queued)
    try:
        return _service(request).run(
            pool_id=payload.pool_id,
            interval=payload.interval,
            lookback=payload.lookback,
            min_return_pct=payload.min_return_pct,
            max_volatility_pct=payload.max_volatility_pct,
            min_quote_volume=payload.min_quote_volume,
            min_momentum_pct=payload.min_momentum_pct,
            limit=payload.limit,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="pool was not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (HistoryStorageError, OSError) as error:
        raise HTTPException(status_code=503, detail="verified history is unavailable") from error
