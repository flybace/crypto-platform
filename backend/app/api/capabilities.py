"""Read-only GACE capability discovery endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth.dependencies import require_user
from ..services.capability_catalog import build_capability_catalog


router = APIRouter(prefix="/api/v1/gace", tags=["gace"])


@router.get("/capabilities")
def capabilities(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return build_capability_catalog(request.app.state.settings)

