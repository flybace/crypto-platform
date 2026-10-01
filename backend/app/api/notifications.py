"""Authenticated in-app notification endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..auth.dependencies import require_user
from ..services.notifications import NotificationService


router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class NotificationConfigRequest(BaseModel):
    enabled: bool = False
    in_app_enabled: bool = True
    system_sound_enabled: bool = True
    event_preferences: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ReadAllRequest(BaseModel):
    all: bool = False


def _service(request: Request) -> NotificationService:
    return request.app.state.notifications


@router.get("/summary")
def summary(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    _: object = Depends(require_user),
) -> dict[str, object]:
    return _service(request).summary(limit=limit)


@router.get("/unread")
def unread(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    _: object = Depends(require_user),
) -> dict[str, object]:
    items = _service(request).items(unread_only=True, limit=limit)
    return {"items": items, "count": len(items), "unread_count": _service(request).summary(limit=1)["unread_count"]}


@router.put("/config")
def update_config(
    payload: NotificationConfigRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        return _service(request).update_config(payload.model_dump(mode="json"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/read/{item_id}")
def mark_read(item_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    try:
        return _service(request).mark_read(item_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="notification was not found") from error


@router.post("/read")
def mark_all_read(
    payload: ReadAllRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    if not payload.all:
        raise HTTPException(status_code=422, detail="all must be true")
    return {"marked": _service(request).mark_all_read()}


@router.post("/test")
def test_notification(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return _service(request).create(
        title="Crypto 平台测试通知",
        message="站内通知链路可用；这条消息不会触发交易或外部发送。",
        event_type="system_test",
        severity="info",
        action_link="/notifications",
    )
