"""Strategy catalog for research and backtest selection."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth.dependencies import require_user
from ..services.strategy_registry import StrategyRegistry


router = APIRouter(prefix="/api/v1/strategies", tags=["strategies"])


@router.get("/catalog")
def catalog(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    registry: StrategyRegistry = request.app.state.strategy_registry
    items = [
        {**item, "status": "available" if item["enabled"] else "disabled"}
        for item in registry.catalog(request.app.state.settings.execution_mode)
    ]
    return {"items": items, "count": len(items)}


class StrategyManagementRequest(BaseModel):
    enabled: bool | None = None
    modes: list[str] | None = Field(default=None, max_length=4)
    default_parameters: dict[str, Any] | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=240)


def _registry(request: Request) -> StrategyRegistry:
    return request.app.state.strategy_registry


@router.get("/summary")
def summary(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return _registry(request).summary()


@router.get("/management")
def management(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    items = _registry(request).management()
    return {"items": items, "count": len(items)}


@router.patch("/management/{strategy_id}")
def update_management(
    strategy_id: str,
    payload: StrategyManagementRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        return _registry(request).update(
            strategy_id,
            enabled=payload.enabled,
            modes=payload.modes,
            default_parameters=payload.default_parameters,
            note=payload.note,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="strategy was not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/management/{strategy_id}/reset")
def reset_management(strategy_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    try:
        return _registry(request).reset(strategy_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="strategy was not found") from error
