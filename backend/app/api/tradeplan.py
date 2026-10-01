"""24/7 trade-plan alias backed by the runtime state projection."""

from fastapi import APIRouter, Depends, Request

from ..auth.dependencies import require_user
from ..services.runtime_plan import build_runtime_plan


router = APIRouter(prefix="/api/v1/tradeplan", tags=["tradeplan"])


@router.get("/summary")
def summary(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    result = build_runtime_plan(request.app.state)
    return {**result, "module": "tradeplan", "market_mode": "24/7 spot research"}
