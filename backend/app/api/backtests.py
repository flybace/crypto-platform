"""Authenticated OHLCV backtest routes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from application.candle_backtest import CandleBacktestConfig

from ..auth.dependencies import require_user
from ..services.backtest_runs import BacktestDataError
from ..services.task_dispatcher import TaskDispatchError, TaskDispatcher


router = APIRouter(prefix="/api/v1/backtests", tags=["backtests"])


class BacktestRunRequest(BaseModel):
    venue_id: str = Field(min_length=1, max_length=32)
    symbol: str = Field(min_length=2, max_length=32)
    interval: Literal["1d", "1h", "5m"] = "1d"
    strategy_id: str = Field(default="sma_cross", min_length=1, max_length=64)
    start_at: datetime | None = None
    end_at: datetime | None = None
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
    def validate_range_and_windows(self) -> "BacktestRunRequest":
        for field in ("start_at", "end_at"):
            value = getattr(self, field)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{field} must include timezone information")
        if self.start_at is not None and self.end_at is not None and self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        if self.slow_window <= self.fast_window:
            raise ValueError("slow_window must be greater than fast_window")
        return self


class PoolBacktestRequest(BaseModel):
    pool_id: str = Field(min_length=1, max_length=120)
    screening_run_id: str | None = Field(default=None, max_length=120)
    interval: Literal["1d", "1h", "5m"] = "1d"
    strategy_id: str = Field(default="sma_cross", min_length=1, max_length=64)
    initial_quote: Decimal = Field(default=Decimal("10000"), gt=0)
    initial_base: Decimal = Field(default=Decimal("0"), ge=0)
    fee_bps: Decimal = Field(default=Decimal("10"), ge=0, le=1000)
    slippage_bps: Decimal = Field(default=Decimal("5"), ge=0, le=1000)
    fast_window: int = Field(default=10, ge=2, le=500)
    slow_window: int = Field(default=30, ge=3, le=1000)
    allocation_ratio: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    momentum_threshold_pct: Decimal = Field(default=Decimal("0.02"), ge=0, le=10)
    strategy_parameters: dict[str, Any] = Field(default_factory=dict, max_length=40)
    max_datasets: int = Field(default=10, ge=1, le=20)

    @model_validator(mode="after")
    def validate_windows(self) -> "PoolBacktestRequest":
        if self.slow_window <= self.fast_window:
            raise ValueError("slow_window must be greater than fast_window")
        return self


def _manager(request: Request):
    return request.app.state.backtest_runs


def _config(payload: BacktestRunRequest | PoolBacktestRequest) -> CandleBacktestConfig:
    return CandleBacktestConfig(
        strategy_id=payload.strategy_id,
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


@router.get("/strategies")
def strategies(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    # Kept as a compatibility endpoint for the backtest form. Disabled
    # strategies are hidden from new runs but remain visible in management.
    items = [
        item
        for item in request.app.state.strategy_registry.catalog(request.app.state.settings.execution_mode)
        if item["enabled"] and "backtest" in item["modes"]
    ]
    return {"items": items, "count": len(items)}


@router.get("/runs")
def runs(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    _: object = Depends(require_user),
) -> dict[str, object]:
    items = _manager(request).list(limit)
    return {"items": items, "count": len(items)}


@router.get("/summary")
def summary(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return _manager(request).summary()


@router.get("/best")
def best(
    request: Request,
    limit: int = Query(default=10, ge=1, le=100),
    _: object = Depends(require_user),
) -> dict[str, object]:
    items = _manager(request).best(limit)
    return {"items": items, "count": len(items)}


@router.post("/run", status_code=status.HTTP_201_CREATED)
def run_backtest(
    payload: BacktestRunRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        request.app.state.strategy_registry.assert_enabled(payload.strategy_id, "backtest")
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    config = _config(payload)
    dispatcher = request.app.state.task_dispatcher
    if dispatcher.enabled:
        run_id = dispatcher.run_id()
        task_payload = {
            "run_id": run_id,
            "venue_id": payload.venue_id,
            "symbol": payload.symbol,
            "interval": payload.interval,
            "strategy_id": payload.strategy_id,
            "start_at": None if payload.start_at is None else payload.start_at.isoformat(),
            "end_at": None if payload.end_at is None else payload.end_at.isoformat(),
            "config": dispatcher.config_payload(config),
        }
        try:
            queued = dispatcher.dispatch(
                "backtest",
                "策略回测",
                task_payload,
                task_id=TaskDispatcher.task_id("backtest", run_id),
            )
        except TaskDispatchError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=queued)
    try:
        return _manager(request).run(
            venue_id=payload.venue_id,
            symbol=payload.symbol,
            interval=payload.interval,
            start_at=payload.start_at,
            end_at=payload.end_at,
            config=config,
        )
    except BacktestDataError as error:
        message = str(error)
        code = status.HTTP_404_NOT_FOUND if "not found" in message else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=message) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/pool", status_code=status.HTTP_201_CREATED)
def run_pool_backtest(
    payload: PoolBacktestRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Run the same deterministic strategy over a pool or screening result."""
    try:
        request.app.state.strategy_registry.assert_enabled(payload.strategy_id, "backtest")
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    pool = request.app.state.pool_catalog.get(payload.pool_id)
    if pool is None:
        raise HTTPException(status_code=404, detail="pool was not found")
    candidate_ids: set[str] | None = None
    if payload.screening_run_id:
        screening = request.app.state.screening_service.get(payload.screening_run_id)
        if screening is None:
            raise HTTPException(status_code=404, detail="screening run was not found")
        candidate_ids = {str(item["dataset_id"]) for item in screening["items"] if item.get("passed")}
    datasets = [
        dataset
        for dataset in request.app.state.pool_catalog.datasets_for(payload.pool_id)
        if dataset.manifest.interval == payload.interval
        and not dataset.manifest.gap_count
        and not dataset.manifest.duplicate_count
        and (candidate_ids is None or dataset.manifest.dataset_id in candidate_ids)
    ][: payload.max_datasets]
    if not datasets:
        raise HTTPException(status_code=422, detail="pool contains no matching quality-passed datasets")

    config = _config(payload)
    dispatcher = request.app.state.task_dispatcher
    if dispatcher.enabled:
        run_id = dispatcher.run_id()
        task_payload = {
            "run_id": run_id,
            "pool_id": payload.pool_id,
            "screening_run_id": payload.screening_run_id,
            "interval": payload.interval,
            "strategy_id": payload.strategy_id,
            "max_datasets": payload.max_datasets,
            "config": dispatcher.config_payload(config),
        }
        try:
            queued = dispatcher.dispatch(
                "pool_backtest",
                "币池策略回测",
                task_payload,
                task_id=TaskDispatcher.task_id("pool_backtest", run_id),
            )
        except TaskDispatchError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=queued)
    items: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    for dataset in datasets:
        symbol = dataset.manifest.instrument_key.rsplit(":", 1)[-1]
        try:
            items.append(_manager(request).run(
                venue_id=dataset.manifest.venue_id,
                symbol=symbol,
                interval=payload.interval,
                config=config,
            ))
        except BacktestDataError as error:
            failures.append({"dataset_id": dataset.manifest.dataset_id, "reason": str(error)})
    if not items:
        raise HTTPException(status_code=422, detail="no pool dataset could produce a backtest")
    returns = [Decimal(str(item["total_return_pct"])) for item in items]
    return {
        "batch_id": f"batch-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        "pool_id": payload.pool_id,
        "screening_run_id": payload.screening_run_id,
        "strategy_id": payload.strategy_id,
        "interval": payload.interval,
        "status": "completed" if not failures else "partial",
        "count": len(items),
        "failed_count": len(failures),
        "average_return_pct": str(sum(returns, Decimal("0")) / Decimal(len(returns))),
        "best_return_pct": str(max(returns)),
        "worst_return_pct": str(min(returns)),
        "items": items,
        "failures": failures,
    }


@router.get("/runs/{run_id}")
def run_detail(run_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    record = _manager(request).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="backtest run was not found")
    return record


@router.delete("/runs/{run_id}")
def delete_run(run_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    if not _manager(request).delete(run_id):
        raise HTTPException(status_code=404, detail="backtest run was not found")
    return {"status": "deleted", "run_id": run_id}


@router.delete("/runs")
def delete_runs(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return {"status": "deleted", "deleted": _manager(request).clear()}


class ParameterTuneRequest(BaseModel):
    venue_id: str = Field(min_length=1, max_length=32)
    symbol: str = Field(min_length=2, max_length=32)
    interval: Literal["1d", "1h", "5m"] = "1d"
    strategy_id: str = Field(min_length=1, max_length=64)
    param_grids: dict[str, list[Any]] = Field(
        description="Parameter name -> list of values to try",
        min_length=1, max_length=10,
    )
    metric: str = Field(
        default="total_return_pct",
        description="Optimization metric: total_return_pct, max_drawdown_pct, win_rate_pct",
    )
    max_combinations: int = Field(default=50, ge=1, le=200)
    initial_quote: Decimal = Field(default=Decimal("10000"), gt=0)
    initial_base: Decimal = Field(default=Decimal("0"), ge=0)
    fee_bps: Decimal = Field(default=Decimal("10"), ge=0, le=1000)
    slippage_bps: Decimal = Field(default=Decimal("5"), ge=0, le=1000)
    fast_window: int = Field(default=10, ge=2, le=500)
    slow_window: int = Field(default=30, ge=3, le=1000)
    allocation_ratio: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    momentum_threshold_pct: Decimal = Field(default=Decimal("0.02"), ge=0, le=10)

    @model_validator(mode="after")
    def validate_grids(self) -> "ParameterTuneRequest":
        total = 1
        for key, values in self.param_grids.items():
            if not isinstance(values, list) or not values:
                raise ValueError(f"param_grids[{key}] must be a non-empty list")
            if len(values) > 20:
                raise ValueError(f"param_grids[{key}] has too many values (max 20)")
            total *= len(values)
        if total > self.max_combinations:
            raise ValueError(
                f"parameter grid has {total} combinations, exceeds max_combinations={self.max_combinations}"
            )
        return self


@router.post("/tune", status_code=status.HTTP_201_CREATED)
def tune_parameters(
    payload: ParameterTuneRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Grid-search parameter tuning for a strategy.

    Runs a backtest for each parameter combination and ranks by the
    optimization metric. Synchronous; bounded by max_combinations.
    """
    try:
        request.app.state.strategy_registry.assert_enabled(payload.strategy_id, "backtest")
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    from ..services.parameter_tuning import ParameterTuner

    config = CandleBacktestConfig(
        strategy_id=payload.strategy_id,
        initial_quote=payload.initial_quote,
        initial_base=payload.initial_base,
        fee_bps=payload.fee_bps,
        slippage_bps=payload.slippage_bps,
        fast_window=payload.fast_window,
        slow_window=payload.slow_window,
        allocation_ratio=payload.allocation_ratio,
        momentum_threshold_pct=payload.momentum_threshold_pct,
        parameters={},
    )
    tuner = ParameterTuner(
        request.app.state.backtest_runs,
        max_combinations=payload.max_combinations,
    )
    try:
        result = tuner.tune(
            strategy_id=payload.strategy_id,
            venue_id=payload.venue_id,
            symbol=payload.symbol,
            interval=payload.interval,
            param_grids=payload.param_grids,
            base_config=config,
            metric=payload.metric,
            max_combinations=payload.max_combinations,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except BacktestDataError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return result.as_dict()
