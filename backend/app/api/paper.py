"""Paper-account and simulated-order endpoints."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from application.candle_backtest import CandleBacktestConfig
from application.history_storage import HistoryStorageError

from ..auth.dependencies import require_user
from ..services.task_dispatcher import TaskDispatchError, TaskDispatcher


router = APIRouter(prefix="/api/v1/paper", tags=["paper"])


def _decimal(value):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("value must be numeric") from error
    if not parsed.is_finite():
        raise ValueError("value must be finite")
    return parsed


class PaperOrderRequest(BaseModel):
    venue_id: str = Field(min_length=1, max_length=32)
    symbol: str = Field(min_length=2, max_length=32)
    interval: Literal["1d", "1h", "5m"] = "1h"
    side: Literal["BUY", "SELL"]
    quantity: Decimal = Field(gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    request_id: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("quantity", "limit_price", mode="before")
    @classmethod
    def finite_decimal(cls, value):
        if value is None:
            return value
        return _decimal(value)


class PaperResetRequest(BaseModel):
    balances: dict[str, Decimal] | None = None
    venue_id: str | None = Field(default=None, min_length=1, max_length=32)


class PaperStrategyRunRequest(BaseModel):
    venue_id: str = Field(min_length=1, max_length=32)
    symbol: str = Field(min_length=2, max_length=32)
    interval: Literal["1d", "1h", "5m"] = "1h"
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

    @model_validator(mode="after")
    def validate_windows(self) -> "PaperStrategyRunRequest":
        if self.slow_window <= self.fast_window:
            raise ValueError("slow_window must be greater than fast_window")
        return self


class PaperAutomationRequest(BaseModel):
    enabled: bool = False
    venue_id: str = Field(default="binance", min_length=1, max_length=32)
    symbol: str = Field(default="BTC/USDT", min_length=2, max_length=32)
    interval: Literal["1d", "1h", "5m"] = "1h"
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

    @model_validator(mode="after")
    def validate_windows(self) -> "PaperAutomationRequest":
        if self.slow_window <= self.fast_window:
            raise ValueError("slow_window must be greater than fast_window")
        return self


def _service(request: Request):
    return request.app.state.paper_trading


@router.get("/summary")
def summary(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return _service(request).summary()


@router.get("/orders")
def orders(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    _: object = Depends(require_user),
) -> dict[str, object]:
    items = _service(request).orders(limit)
    return {"items": items, "count": len(items)}


@router.get("/strategy-runs")
def strategy_runs(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    _: object = Depends(require_user),
) -> dict[str, object]:
    items = _service(request).strategy_runs(limit)
    return {"items": items, "count": len(items)}


@router.get("/automation")
def automation(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return request.app.state.paper_automation.get()


@router.put("/automation")
def update_automation(
    payload: PaperAutomationRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        request.app.state.strategy_registry.assert_enabled(payload.strategy_id, "paper")
        return request.app.state.paper_automation.update(payload.model_dump(mode="json"))
    except (ValueError, KeyError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/automation/run", status_code=status.HTTP_201_CREATED)
def run_automation(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    dispatcher = request.app.state.task_dispatcher
    if dispatcher.enabled:
        config = request.app.state.paper_automation.get()
        if not config.get("enabled"):
            raise HTTPException(status_code=422, detail="paper automation is disabled")
        try:
            request.app.state.strategy_registry.assert_enabled(str(config.get("strategy_id", "")), "paper")
            run_id = dispatcher.run_id()
            queued = dispatcher.dispatch(
                "paper_automation",
                "模拟自动回放",
                {"run_id": run_id, "config": config},
                task_id=TaskDispatcher.task_id("paper_automation", run_id),
            )
        except (ValueError, KeyError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except TaskDispatchError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=queued)
    try:
        return request.app.state.paper_automation.run_now()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/strategy-runs", status_code=status.HTTP_201_CREATED)
def run_strategy(payload: PaperStrategyRunRequest, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    try:
        request.app.state.strategy_registry.assert_enabled(payload.strategy_id, "paper")
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
            parameters=payload.strategy_parameters,
        )
        dispatcher = request.app.state.task_dispatcher
        if dispatcher.enabled:
            run_id = dispatcher.run_id()
            queued = dispatcher.dispatch(
                "paper_strategy",
                "模拟策略回放",
                {
                    "run_id": run_id,
                    "venue_id": payload.venue_id,
                    "symbol": payload.symbol,
                    "interval": payload.interval,
                    "strategy_id": payload.strategy_id,
                    "config": dispatcher.config_payload(config),
                },
                task_id=TaskDispatcher.task_id("paper_strategy", run_id),
            )
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=queued)
        return _service(request).run_strategy(
            venue_id=payload.venue_id,
            symbol=payload.symbol,
            interval=payload.interval,
            config=config,
        )
    except TaskDispatchError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (ValueError, HistoryStorageError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/orders", status_code=status.HTTP_201_CREATED)
def submit_order(payload: PaperOrderRequest, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    try:
        return _service(request).submit(
            venue_id=payload.venue_id,
            symbol=payload.symbol,
            interval=payload.interval,
            side=payload.side,
            quantity=payload.quantity,
            limit_price=payload.limit_price,
            request_id=payload.request_id,
        )
    except (KeyError, ValueError, OSError) as error:
        code = 404 if "not found" in str(error) else 422
        raise HTTPException(status_code=code, detail=str(error)) from error


@router.post("/reset")
def reset(payload: PaperResetRequest | None, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    try:
        balances = None if payload is None else payload.balances
        venue_id = None if payload is None else payload.venue_id
        return _service(request).reset(balances, venue_id=venue_id)
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
