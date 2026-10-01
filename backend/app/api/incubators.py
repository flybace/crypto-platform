"""Strategy incubator endpoints for screening-to-research hand-off."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from application.strategy_incubator import StrategyIncubatorError, StrategyIncubatorService

from ..auth.dependencies import require_user


router = APIRouter(prefix="/api/v1/incubators", tags=["incubators"])


class IncubatorFromScreenRequest(BaseModel):
    pool_id: str | None = Field(default=None, max_length=120)
    name: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=240)
    mode: Literal["append", "replace"] = "append"
    limit: int = Field(default=120, ge=1, le=500)
    min_score: Decimal | None = None

    @field_validator("min_score")
    @classmethod
    def score_must_be_finite(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("min_score must be finite")
        return value


def _service(request: Request) -> StrategyIncubatorService:
    return request.app.state.strategy_incubator


@router.get("")
def incubators(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    _: object = Depends(require_user),
) -> dict[str, object]:
    return _service(request).snapshot(limit=limit)


@router.get("/{pool_id}")
def incubator(pool_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    item = _service(request).get(pool_id)
    if item is None:
        raise HTTPException(status_code=404, detail="strategy incubator was not found")
    return item


@router.post("/from-screen/{run_id}", status_code=status.HTTP_201_CREATED)
def add_screening_result(
    run_id: str,
    payload: IncubatorFromScreenRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        return _service(request).add_from_screening(
            run_id,
            pool_id=payload.pool_id,
            name=payload.name,
            description=payload.description,
            mode=payload.mode,
            limit=payload.limit,
            min_score=payload.min_score,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="screening run was not found") from error
    except StrategyIncubatorError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
