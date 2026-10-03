"""Strategy admission funnel endpoints.

Stages: scored → retested → cross_validated → paper_approved.
Paper trading requires paper_approved + rating A or above.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth.dependencies import require_user

router = APIRouter(prefix="/api/v1/strategy/funnel", tags=["strategy-funnel"])


def _service(request: Request):
    service = getattr(request.app.state, "strategy_funnel", None)
    if service is None:
        raise HTTPException(status_code=503, detail="strategy funnel is unavailable")
    return service


class BacktestSubmitRequest(BaseModel):
    strategy_id: str = Field(min_length=1)
    strategy_parameters: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    venue_id: str = ""
    symbol: str = ""
    pool_id: str = ""


class AdvanceRequest(BaseModel):
    strategy_id: str = Field(min_length=1)
    strategy_parameters: dict = Field(default_factory=dict)
    to_stage: str = Field(min_length=1)
    note: str = ""


class EligibilityRequest(BaseModel):
    strategy_id: str = Field(min_length=1)
    strategy_parameters: dict = Field(default_factory=dict)


@router.get("")
def list_funnels(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    items = _service(request).list_funnels()
    return {"items": items, "count": len(items)}


@router.post("/submit", status_code=status.HTTP_201_CREATED)
def submit_backtest(
    payload: BacktestSubmitRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        return _service(request).submit_backtest(
            payload.strategy_id,
            payload.strategy_parameters,
            payload.metrics,
            venue_id=payload.venue_id,
            symbol=payload.symbol,
            pool_id=payload.pool_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/advance")
def advance_stage(
    payload: AdvanceRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        return _service(request).advance_stage(
            payload.strategy_id,
            payload.strategy_parameters,
            payload.to_stage,
            note=payload.note,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/eligibility")
def check_eligibility(
    payload: EligibilityRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    eligible, reason = _service(request).check_paper_eligible(
        payload.strategy_id, payload.strategy_parameters
    )
    return {"eligible": eligible, "reason": reason}
