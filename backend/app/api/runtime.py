"""Unified runtime-plan endpoint for the standalone control plane."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth.dependencies import require_user
from ..services.runtime_plan import build_runtime_plan


router = APIRouter(prefix="/api/v1/runtime", tags=["runtime"])


@router.get("/plan")
def plan(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return build_runtime_plan(request.app.state)

