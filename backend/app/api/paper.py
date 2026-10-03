"""Paper-account and simulated-order endpoints."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from application.candle_backtest import CandleBacktestConfig
from application.history_storage import HistoryStorageError

from ..auth.dependencies import require_user
from ..services.paper_follow import PaperFollowService, execute_paper_follow
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
    news_gate: bool = Field(default=False, description="风险新闻门控:风险事件发布后按建议时长拦截新开仓(只拦截多头开仓,不拦截离场)")

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


class PaperFollowRequest(BaseModel):
    enabled: bool = False
    interval_seconds: int = Field(default=86400, ge=3600, le=604800)
    alert_min_return_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    alert_max_drawdown_pct: Decimal = Field(default=Decimal("20"), ge=0, le=100)
    alert_underperform_pct: Decimal = Field(default=Decimal("5"), ge=0, le=100)

    @field_validator("alert_min_return_pct", "alert_max_drawdown_pct", "alert_underperform_pct", mode="before")
    @classmethod
    def finite_decimal(cls, value):
        return _decimal(value)


class PaperLiveRequest(BaseModel):
    enabled: bool = False
    venue_id: str = Field(default="binance", min_length=1, max_length=32)
    symbol: str = Field(default="BTC/USDT", min_length=2, max_length=32)
    interval: Literal["1d", "1h", "5m"] = "1h"
    strategy_id: str = Field(default="macd_reversal", min_length=1, max_length=64)
    strategy_parameters: dict[str, Any] = Field(default_factory=dict, max_length=40)
    allocation_ratio: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    max_position_ratio: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    stop_loss_pct: Decimal = Field(default=Decimal("0"), ge=0, lt=1)
    daily_max_loss_pct: Decimal = Field(default=Decimal("0"), ge=0, lt=1)

    @field_validator("allocation_ratio", "max_position_ratio", "stop_loss_pct", "daily_max_loss_pct", mode="before")
    @classmethod
    def finite_decimal(cls, value):
        return _decimal(value)


def _service(request: Request):
    return request.app.state.paper_trading


def _risk_news_events(request: Request, *, symbol: str) -> tuple[dict[str, object], ...]:
    """Fetch risk-level news events relevant to a symbol for the news gate.

    Returns a point-in-time snapshot; the replay engine additionally filters
    by each candle's time so the replay never peeks into the future.
    """
    normalized = str(symbol or "").strip().upper()
    try:
        events = request.app.state.news_service.events(sentiment="risk", limit=200)
    except Exception:
        return ()
    selected = []
    for event in events:
        symbols = {str(value).upper() for value in event.get("symbols", [])}
        if symbols and normalized not in symbols:
            continue
        topics = [str(value) for value in event.get("topics", [])]
        selected.append({
            "published_at": event.get("published_at"),
            "symbols": sorted(symbols),
            "sentiment": event.get("sentiment"),
            "risk_level": event.get("risk_level"),
            "title": event.get("title"),
            "suggested_duration_hours": _suggested_duration(topics),
        })
    return tuple(selected)


def _suggested_duration(topics: list[str]) -> int:
    from backend.app.services import news_intel

    return news_intel.suggested_duration_hours(topics)


@router.get("/follow")
def follow(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    service: PaperFollowService = request.app.state.paper_follow
    return service.get()


@router.put("/follow")
def update_follow(
    payload: PaperFollowRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    service: PaperFollowService = request.app.state.paper_follow
    try:
        return service.update(payload.model_dump(mode="json"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/follow/run", status_code=status.HTTP_201_CREATED)
def run_follow(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    follow_service: PaperFollowService = request.app.state.paper_follow
    config = follow_service.get()["config"]
    automation = request.app.state.paper_automation.get()
    if not config.get("enabled"):
        raise HTTPException(status_code=422, detail="paper follow is disabled")
    if not automation.get("enabled"):
        raise HTTPException(status_code=422, detail="paper automation is disabled")
    dispatcher = request.app.state.task_dispatcher
    if dispatcher.enabled:
        try:
            request.app.state.strategy_registry.assert_enabled(
                str(automation.get("strategy_id", "")), "paper"
            )
            run_id = dispatcher.run_id()
            queued = dispatcher.dispatch(
                "paper_follow",
                "模拟盘自动跟盯",
                {"run_id": run_id},
                task_id=TaskDispatcher.task_id("paper_follow", run_id),
            )
        except (ValueError, KeyError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except TaskDispatchError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=queued)
    try:
        snapshot = execute_paper_follow(
            paper_automation=request.app.state.paper_automation,
            paper_trading=request.app.state.paper_trading,
            strategy_registry=request.app.state.strategy_registry,
            follow_service=follow_service,
            run_id=f"paper-follow-{uuid4().hex}",
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"snapshot": snapshot}


@router.get("/live")
def live(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    """返回所有策略实例列表。"""
    manager = request.app.state.paper_live_manager
    return {"instances": manager.list_instances()}


@router.post("/live/instances", status_code=status.HTTP_201_CREATED)
def create_live_instance(
    payload: PaperLiveRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    manager = request.app.state.paper_live_manager
    try:
        # model_dump 会把 Decimal 转成字符串（mode="json"）
        data = payload.model_dump(mode="json")
        # 策略准入漏斗：模拟盘需要 A 级及以上评级
        funnel = getattr(request.app.state, "strategy_funnel", None)
        if funnel is not None:
            eligible, reason = funnel.check_paper_eligible(
                data.get("strategy_id", ""), data.get("strategy_parameters", {})
            )
            if not eligible:
                raise HTTPException(
                    status_code=422,
                    detail=f"策略未通过准入漏斗: {reason}。"
                    "请先在回测页提交回测获取评级，按 评分→复测→跨池验证→准入 流程晋级。",
                )
        # enabled 默认为 False，用户在前端点"启动策略"
        return manager.create_instance(data)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.put("/live/instances/{instance_id}")
def update_live_instance(
    instance_id: str,
    payload: PaperLiveRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    manager = request.app.state.paper_live_manager
    try:
        # 只更新提供的字段：用 exclude_unset 避免把未传字段重置为默认值
        data = payload.model_dump(mode="json", exclude_unset=True)
        return manager.update_instance(instance_id, data)
    except KeyError:
        raise HTTPException(status_code=404, detail="instance not found")
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.delete("/live/instances/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_live_instance(
    instance_id: str,
    request: Request,
    _: object = Depends(require_user),
):
    manager = request.app.state.paper_live_manager
    try:
        manager.delete_instance(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="instance not found")


@router.post("/live/instances/{instance_id}/reset-account", status_code=status.HTTP_200_OK)
def reset_instance_account(
    instance_id: str,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    """重置指定策略实例的独立模拟账户（默认纯 USDT 10000）。"""
    manager = request.app.state.paper_live_manager
    try:
        return manager.reset_instance_account(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="instance not found")
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/live/instances/{instance_id}/run", status_code=status.HTTP_201_CREATED)
def run_live_instance(
    instance_id: str,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    manager = request.app.state.paper_live_manager
    svc = manager.get_instance(instance_id)
    if svc is None:
        raise HTTPException(status_code=404, detail="instance not found")
    if not svc.get().get("enabled"):
        raise HTTPException(status_code=422, detail="paper live instance is disabled")
    try:
        return svc.tick()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


# 兼容旧单实例 API（已废弃，前端不再使用）
@router.put("/live")
def update_live(
    payload: PaperLiveRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    manager = request.app.state.paper_live_manager
    instances = manager.list_instances()
    if not instances:
        raise HTTPException(status_code=404, detail="no live instances")
    # 默认操作第一个实例
    return update_live_instance(instances[0]["instance_id"], payload, request, _)


@router.post("/live/run", status_code=status.HTTP_201_CREATED)
def run_live(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    manager = request.app.state.paper_live_manager
    instances = manager.list_instances()
    if not instances:
        raise HTTPException(status_code=404, detail="no live instances")
    return run_live_instance(instances[0]["instance_id"], request, _)


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
            news_gate=payload.news_gate,
        )
        news_events: tuple[dict[str, object], ...] = ()
        if payload.news_gate:
            news_events = _risk_news_events(request, symbol=payload.symbol)
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
                    "news_events": [dict(event) for event in news_events],
                },
                task_id=TaskDispatcher.task_id("paper_strategy", run_id),
            )
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=queued)
        return _service(request).run_strategy(
            venue_id=payload.venue_id,
            symbol=payload.symbol,
            interval=payload.interval,
            config=config,
            news_events=news_events,
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
