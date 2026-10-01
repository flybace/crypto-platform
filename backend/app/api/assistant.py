"""GACE AI-compatible read-only Action gateway."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from ..auth.dependencies import require_user
from ..services.assistant import AssistantService


router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])


class ActionRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=120)
    action: str = Field(min_length=1, max_length=120)
    actor: str = Field(min_length=1, max_length=120)
    risk_level: Literal["READ_ONLY", "CONTROLLED_WRITE", "HIGH_RISK"]
    payload: dict[str, Any] = Field(default_factory=dict, max_length=30)
    authorization_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=160)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must contain a timezone")
        return value


def _service(request: Request) -> AssistantService:
    return request.app.state.assistant


@router.get("/summary")
def summary(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return _service(request).summary()


@router.get("/actions")
def actions(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    items = _service(request).actions()
    return {"items": items, "count": len(items), "read_only": True}


@router.get("/invocations")
def invocations(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    _: object = Depends(require_user),
) -> dict[str, object]:
    items = _service(request).invocations(limit)
    return {"items": items, "count": len(items)}


@router.post("/invoke")
def invoke(payload: ActionRequest, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    try:
        return _service(request).invoke(payload.model_dump(), request.app.state)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"read-only action was not found: {error.args[0]}") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
