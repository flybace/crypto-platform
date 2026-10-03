"""Market regime endpoints: current state + config."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth.dependencies import require_user

router = APIRouter(prefix="/api/v1/market-regime", tags=["market-regime"])


def _service(request: Request):
    service = getattr(request.app.state, "market_regime", None)
    if service is None:
        raise HTTPException(status_code=503, detail="market regime service unavailable")
    return service


class RegimeConfigRequest(BaseModel):
    enabled: bool | None = None
    benchmark_venue: str | None = None
    benchmark_symbol: str | None = None
    benchmark_interval: str | None = None
    lookback_days: int | None = None
    thresholds: dict[str, float] | None = None
    factors: dict[str, float] | None = None


@router.get("")
def get_regime(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    result = _service(request).evaluate()
    return {
        "regime": result.regime,
        "score": result.score,
        "factor": result.factor,
        "blocks_new_positions": result.blocks_new_positions,
        "reason": result.reason,
        "details": result.details,
        "evaluated_at": result.evaluated_at,
        "config": _service(request).get_config(),
    }


@router.put("/config")
def update_config(
    payload: RegimeConfigRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    values = {k: v for k, v in payload.model_dump().items() if v is not None}
    return _service(request).update_config(values)
