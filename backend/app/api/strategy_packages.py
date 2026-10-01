"""Strategy package metadata endpoints for the standalone research plane."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth.dependencies import require_user
from ..services.strategy_packages import StrategyPackageError, StrategyPackageService


router = APIRouter(prefix="/api/v1/strategies/packages", tags=["strategy-packages"])


class StrategyPackageRegisterRequest(BaseModel):
    package_id: str = Field(min_length=2, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    strategy_ids: list[str] = Field(min_length=1, max_length=10)
    note: str = Field(default="", max_length=300)


def _service(request: Request) -> StrategyPackageService:
    return request.app.state.strategy_packages


@router.get("/summary")
def summary(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return _service(request).summary()


@router.get("")
def packages(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    items = _service(request).list()
    return {"items": items, "count": len(items)}


@router.get("/{package_id}")
def package(package_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    item = _service(request).get(package_id)
    if item is None:
        raise HTTPException(status_code=404, detail="strategy package was not found")
    return item


@router.post("", status_code=status.HTTP_201_CREATED)
def register_package(
    payload: StrategyPackageRegisterRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, Any]:
    try:
        return _service(request).register(
            package_id=payload.package_id,
            name=payload.name,
            version=payload.version,
            strategy_ids=payload.strategy_ids,
            note=payload.note,
        )
    except StrategyPackageError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
