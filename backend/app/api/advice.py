"""Research advice endpoints with no execution side effects."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from ..auth.dependencies import require_user
from ..services.advice import AdviceError, AdviceService


router = APIRouter(prefix="/api/v1/advice", tags=["advice"])


class AdviceSnapshotRequest(BaseModel):
    title: str = Field(default="研究建议快照", max_length=160)
    source: str = Field(default="manual", max_length=80)
    interval: Literal["1d", "1h", "5m"] = "1h"
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


def _service(request: Request) -> AdviceService:
    return request.app.state.advice_service


@router.get("/summary")
def summary(
    request: Request,
    interval: Literal["1d", "1h", "5m"] = "1h",
    compact: bool = Query(default=False),
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        return _service(request).summary(interval=interval, compact=compact)
    except AdviceError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/candidates")
def candidates(
    request: Request,
    interval: Literal["1d", "1h", "5m"] = "1h",
    grade: Literal["all", "focus", "watch", "avoid"] = "all",
    risk: Literal["all", "low", "medium", "high"] = "all",
    compact: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        return _service(request).candidates(interval=interval, grade=grade, risk=risk, limit=limit, compact=compact)
    except AdviceError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/snapshots")
def snapshots(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    _: object = Depends(require_user),
) -> dict[str, object]:
    items = _service(request).snapshots(limit)
    return {"items": items, "count": len(items)}


@router.get("/snapshots/{snapshot_id}")
def snapshot(snapshot_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    item = _service(request).get(snapshot_id)
    if item is None:
        raise HTTPException(status_code=404, detail="advice snapshot was not found")
    return item


@router.post("/snapshots", status_code=status.HTTP_201_CREATED)
def create_snapshot(
    payload: AdviceSnapshotRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        return _service(request).create(
            title=payload.title,
            source=payload.source,
            interval=payload.interval,
            items=payload.items,
        )
    except AdviceError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/generate", status_code=status.HTTP_201_CREATED)
def generate(
    request: Request,
    interval: Literal["1d", "1h", "5m"] = "1h",
    limit: int = Query(default=100, ge=1, le=500),
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        return _service(request).generate(interval=interval, limit=limit)
    except AdviceError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.post("/from-screen/{screen_run_id}", status_code=status.HTTP_201_CREATED)
def from_screen(screen_run_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    try:
        return _service(request).from_screen(screen_run_id)
    except AdviceError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
