"""Bounded strategy matrix research endpoints."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from application.candle_backtest import CandleBacktestConfig

from ..auth.dependencies import require_user
from ..services.strategy_matrix import StrategyMatrixError, StrategyMatrixService
from ..services.task_dispatcher import TaskDispatchError, TaskDispatcher


router = APIRouter(prefix="/api/v1/strategies/matrices", tags=["strategy-matrices"])


class StrategyMatrixRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    strategy_ids: list[str] = Field(min_length=1, max_length=10)
    dataset_ids: list[str] = Field(min_length=1, max_length=20)
    interval: Literal["1d", "1h", "5m"] = "1d"
    initial_quote: Decimal = Field(default=Decimal("10000"), gt=0)
    initial_base: Decimal = Field(default=Decimal("0"), ge=0)
    fee_bps: Decimal = Field(default=Decimal("10"), ge=0, le=1000)
    slippage_bps: Decimal = Field(default=Decimal("5"), ge=0, le=1000)
    fast_window: int = Field(default=10, ge=2, le=500)
    slow_window: int = Field(default=30, ge=3, le=1000)
    allocation_ratio: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    momentum_threshold_pct: Decimal = Field(default=Decimal("0.02"), ge=0, le=10)
    parameter_sets: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=10)

    @model_validator(mode="after")
    def validate_windows(self) -> "StrategyMatrixRequest":
        if self.slow_window <= self.fast_window:
            raise ValueError("slow_window must be greater than fast_window")
        return self


def _service(request: Request) -> StrategyMatrixService:
    return request.app.state.strategy_matrices


def _config(payload: StrategyMatrixRequest) -> CandleBacktestConfig:
    return CandleBacktestConfig(
        strategy_id=payload.strategy_ids[0],
        initial_quote=payload.initial_quote,
        initial_base=payload.initial_base,
        fee_bps=payload.fee_bps,
        slippage_bps=payload.slippage_bps,
        fast_window=payload.fast_window,
        slow_window=payload.slow_window,
        allocation_ratio=payload.allocation_ratio,
        momentum_threshold_pct=payload.momentum_threshold_pct,
    )


@router.get("/summary")
def summary(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return _service(request).summary()


@router.get("")
def matrices(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    _: object = Depends(require_user),
) -> dict[str, object]:
    items = _service(request).list(limit)
    return {"items": items, "count": len(items)}


@router.get("/{matrix_id}")
def matrix(matrix_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    item = _service(request).get(matrix_id)
    if item is None:
        raise HTTPException(status_code=404, detail="strategy matrix was not found")
    return item


@router.post("", status_code=status.HTTP_201_CREATED)
def create_matrix(
    payload: StrategyMatrixRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        return _service(request).create(
            name=payload.name,
            strategy_ids=payload.strategy_ids,
            dataset_ids=payload.dataset_ids,
            interval=payload.interval,
            config=_config(payload),
            parameter_sets=payload.parameter_sets,
        )
    except StrategyMatrixError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{matrix_id}/run", status_code=status.HTTP_201_CREATED)
def run_matrix(matrix_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    dispatcher = request.app.state.task_dispatcher
    if dispatcher.enabled:
        if _service(request).get(matrix_id) is None:
            raise HTTPException(status_code=404, detail="strategy matrix was not found")
        run_id = dispatcher.run_id()
        try:
            queued = dispatcher.dispatch(
                "strategy_matrix",
                "策略矩阵研究",
                {"matrix_id": matrix_id, "run_id": run_id},
                task_id=TaskDispatcher.task_id("strategy_matrix", run_id),
            )
        except TaskDispatchError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=queued)
    try:
        return _service(request).run(matrix_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="strategy matrix was not found") from error
    except StrategyMatrixError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
