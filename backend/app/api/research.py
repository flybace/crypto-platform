"""Higher-level research routes: portfolio runs, comparisons, tuning, and signal screens."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from application.candle_backtest import CandleBacktestConfig, strategy_catalog
from backend.app.services.research_runs import ResearchRunError, ResearchRunService

from ..auth.dependencies import require_user
from ..services.task_dispatcher import TaskDispatchError, TaskDispatcher


router = APIRouter(prefix="/api/v1/research", tags=["research"])


class ResearchConfig(BaseModel):
    initial_quote: Decimal = Field(default=Decimal("10000"), gt=0)
    initial_base: Decimal = Field(default=Decimal("0"), ge=0)
    fee_bps: Decimal = Field(default=Decimal("10"), ge=0, le=1000)
    slippage_bps: Decimal = Field(default=Decimal("5"), ge=0, le=1000)
    fast_window: int = Field(default=10, ge=2, le=500)
    slow_window: int = Field(default=30, ge=3, le=1000)
    allocation_ratio: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    momentum_threshold_pct: Decimal = Field(default=Decimal("0.02"), ge=0, le=10)
    strategy_parameters: dict[str, Any] = Field(default_factory=dict, max_length=40)

    @model_validator(mode="after")
    def validate_windows(self) -> "ResearchConfig":
        if self.slow_window <= self.fast_window:
            raise ValueError("slow_window must be greater than fast_window")
        return self


class PortfolioRequest(ResearchConfig):
    pool_id: str = Field(min_length=1, max_length=120)
    screening_run_id: str | None = Field(default=None, max_length=120)
    interval: Literal["1d", "1h", "5m"] = "1d"
    strategy_id: str = Field(default="sma_cross", min_length=1, max_length=64)
    max_datasets: int = Field(default=20, ge=1, le=50)


class CompareRequest(ResearchConfig):
    venue_id: str = Field(min_length=1, max_length=32)
    symbol: str = Field(min_length=2, max_length=32)
    interval: Literal["1d", "1h", "5m"] = "1d"
    strategy_ids: list[str] = Field(min_length=1, max_length=10)
    strategy_parameters: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=10)


class TuneRequest(ResearchConfig):
    venue_id: str = Field(min_length=1, max_length=32)
    symbol: str = Field(min_length=2, max_length=32)
    interval: Literal["1d", "1h", "5m"] = "1d"
    strategy_id: str = Field(default="sma_cross", min_length=1, max_length=64)
    parameter_grid: dict[str, list[Any]] = Field(min_length=1, max_length=8)
    max_runs: int = Field(default=30, ge=1, le=100)


class StrategyScreenRequest(ResearchConfig):
    pool_id: str = Field(min_length=1, max_length=120)
    interval: Literal["1d", "1h", "5m"] = "1d"
    strategy_id: str = Field(default="sma_cross", min_length=1, max_length=64)
    lookback: int = Field(default=200, ge=2, le=10000)
    limit: int = Field(default=50, ge=1, le=500)


def _service(request: Request) -> ResearchRunService:
    return request.app.state.research_runs


def _assert_enabled(request: Request, strategy_ids: list[str]) -> None:
    try:
        for strategy_id in strategy_ids:
            request.app.state.strategy_registry.assert_enabled(strategy_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _config(payload: ResearchConfig, strategy_id: str | None = None) -> CandleBacktestConfig:
    return CandleBacktestConfig(
        strategy_id=strategy_id or "sma_cross",
        initial_quote=payload.initial_quote,
        initial_base=payload.initial_base,
        fee_bps=payload.fee_bps,
        slippage_bps=payload.slippage_bps,
        fast_window=payload.fast_window,
        slow_window=payload.slow_window,
        allocation_ratio=payload.allocation_ratio,
        momentum_threshold_pct=payload.momentum_threshold_pct,
        parameters=payload.strategy_parameters,
    )


def _queue_research(
    request: Request,
    mode: str,
    payload: ResearchConfig,
    *,
    strategy_id: str | None = None,
) -> JSONResponse | None:
    dispatcher = request.app.state.task_dispatcher
    if not dispatcher.enabled:
        return None
    run_id = dispatcher.run_id()
    task_payload = payload.model_dump(mode="json")
    task_payload.update(
        {
            "run_id": run_id,
            "mode": mode,
            "config": dispatcher.config_payload(_config(payload, strategy_id)),
        }
    )
    try:
        queued = dispatcher.dispatch(
            "research",
            "研究任务",
            task_payload,
            task_id=TaskDispatcher.task_id("research", run_id),
        )
    except TaskDispatchError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=queued)


@router.get("/summary")
def summary(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    items = _service(request).list(100)
    return {
        "run_count": len(items),
        "portfolio_count": sum(1 for item in items if item.get("kind") == "portfolio"),
        "comparison_count": sum(1 for item in items if item.get("kind") == "strategy_compare"),
        "tune_count": sum(1 for item in items if item.get("kind") == "parameter_tune"),
        "screen_count": sum(1 for item in items if item.get("kind") == "strategy_screen"),
        "strategy_count": len(strategy_catalog()),
    }


@router.get("/runs")
def runs(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    _: object = Depends(require_user),
) -> dict[str, object]:
    items = _service(request).list(limit)
    return {"items": items, "count": len(items)}


@router.get("/runs/{run_id}")
def run_detail(run_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    item = _service(request).get(run_id)
    if item is None:
        raise HTTPException(status_code=404, detail="research run was not found")
    return item


@router.post("/portfolio", status_code=status.HTTP_201_CREATED)
def portfolio(payload: PortfolioRequest, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    _assert_enabled(request, [payload.strategy_id])
    if request.app.state.task_dispatcher.enabled and request.app.state.pool_catalog.get(payload.pool_id) is None:
        raise HTTPException(status_code=404, detail="pool was not found")
    queued = _queue_research(request, "portfolio", payload, strategy_id=payload.strategy_id)
    if queued is not None:
        return queued
    try:
        return _service(request).portfolio(
            pool_id=payload.pool_id,
            interval=payload.interval,
            strategy_id=payload.strategy_id,
            config=_config(payload, payload.strategy_id),
            screening_run_id=payload.screening_run_id,
            max_datasets=payload.max_datasets,
        )
    except ResearchRunError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/compare", status_code=status.HTTP_201_CREATED)
def compare(payload: CompareRequest, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    _assert_enabled(request, payload.strategy_ids)
    queued = _queue_research(request, "compare", payload, strategy_id=payload.strategy_ids[0])
    if queued is not None:
        return queued
    try:
        return _service(request).compare(
            venue_id=payload.venue_id,
            symbol=payload.symbol,
            interval=payload.interval,
            strategy_ids=payload.strategy_ids,
            config=_config(payload),
            parameters=payload.strategy_parameters,
        )
    except ResearchRunError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/tune", status_code=status.HTTP_201_CREATED)
def tune(payload: TuneRequest, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    _assert_enabled(request, [payload.strategy_id])
    queued = _queue_research(request, "tune", payload, strategy_id=payload.strategy_id)
    if queued is not None:
        return queued
    try:
        return _service(request).tune(
            venue_id=payload.venue_id,
            symbol=payload.symbol,
            interval=payload.interval,
            strategy_id=payload.strategy_id,
            config=_config(payload, payload.strategy_id),
            parameter_grid=payload.parameter_grid,
            max_runs=payload.max_runs,
        )
    except ResearchRunError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/screen", status_code=status.HTTP_201_CREATED)
def screen(payload: StrategyScreenRequest, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    _assert_enabled(request, [payload.strategy_id])
    if request.app.state.task_dispatcher.enabled and request.app.state.pool_catalog.get(payload.pool_id) is None:
        raise HTTPException(status_code=404, detail="pool was not found")
    queued = _queue_research(request, "screen", payload, strategy_id=payload.strategy_id)
    if queued is not None:
        return queued
    try:
        return _service(request).strategy_screen(
            pool_id=payload.pool_id,
            interval=payload.interval,
            strategy_id=payload.strategy_id,
            config=_config(payload, payload.strategy_id),
            lookback=payload.lookback,
            limit=payload.limit,
        )
    except ResearchRunError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
