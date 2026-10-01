"""System status and readiness projections."""

from fastapi import APIRouter, Depends, Request

from ..auth.dependencies import require_user
from ..services.system_status import build_system_status


router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/status")
def status(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return build_system_status(request.app.state)


@router.get("/summary")
def summary(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return build_system_status(request.app.state)


@router.get("/readiness")
def readiness(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return build_system_status(request.app.state)["readiness"]
