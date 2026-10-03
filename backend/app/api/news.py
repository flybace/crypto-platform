"""Authenticated news ingestion and research resonance endpoints."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from ..auth.dependencies import require_user
from ..services.news import NewsError, NewsService


router = APIRouter(prefix="/api/v1/news", tags=["news"])


class NewsEventRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1000)
    source: str = Field(min_length=1, max_length=120)
    source_url: str | None = Field(default=None, max_length=500)
    published_at: datetime
    symbols: list[str] = Field(default_factory=list, max_length=20)
    topics: list[str] = Field(default_factory=list, max_length=12)
    sentiment: Literal["positive", "neutral", "risk"] = "neutral"
    risk_level: Literal["low", "medium", "high"] = "low"
    impact_score: Decimal = Field(default=Decimal("50"), ge=0, le=100)

    @field_validator("published_at")
    @classmethod
    def published_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must contain timezone information")
        return value


def _service(request: Request) -> NewsService:
    return request.app.state.news_service


@router.get("/summary")
def summary(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return _service(request).summary()


@router.get("/events")
def events(
    request: Request,
    symbol: str | None = Query(default=None, max_length=32),
    topic: str | None = Query(default=None, max_length=80),
    sentiment: Literal["all", "positive", "neutral", "risk"] = "all",
    limit: int = Query(default=50, ge=1, le=200),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        items = _service(request).events(
            symbol=symbol, topic=topic, sentiment=sentiment, limit=limit,
            start_at=start_at, end_at=end_at,
        )
    except NewsError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"items": items, "count": len(items), "research_only": True}


@router.get("/events/{event_id}")
def event(event_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    item = _service(request).get(event_id)
    if item is None:
        raise HTTPException(status_code=404, detail="news event was not found")
    return item


@router.post("/events", status_code=status.HTTP_201_CREATED)
def create_event(payload: NewsEventRequest, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    try:
        return _service(request).create(
            title=payload.title,
            summary=payload.summary,
            source=payload.source,
            source_url=payload.source_url,
            published_at=payload.published_at,
            symbols=payload.symbols,
            topics=payload.topics,
            sentiment=payload.sentiment,
            risk_level=payload.risk_level,
            impact_score=payload.impact_score,
        )
    except NewsError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/resonance")
def resonance(
    request: Request,
    interval: Literal["1d", "1h", "5m"] = "1h",
    limit: int = Query(default=100, ge=1, le=200),
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        return _service(request).resonance(interval=interval, limit=limit)
    except NewsError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/advice")
def advice(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    hours: int = Query(default=72, ge=1, le=720),
    _: object = Depends(require_user),
) -> dict[str, object]:
    """机器可读的策略建议:重大新闻事件 -> 按品种的行动建议。"""
    return {"items": _service(request).advice(limit=limit, hours=hours)}


@router.get("/ranking")
def ranking(
    request: Request,
    hours: int = Query(default=72, ge=1, le=720),
    _: object = Depends(require_user),
) -> dict[str, object]:
    """消息面选币榜:按品种聚合近期新闻关注度与情绪方向。"""
    return {"items": _service(request).ranking(hours=hours)}


@router.post("/ingest/run", status_code=status.HTTP_201_CREATED)
def run_ingest(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    """手动触发一次 RSS 抓取(分析+入库+重大事件识别)。"""
    from backend.app.services.news_ingest import NewsIngestor

    try:
        return NewsIngestor(_service(request)).ingest_once()
    except NewsError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
