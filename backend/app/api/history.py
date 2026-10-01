"""Authenticated public-history dataset and job endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator

from domain.candle import CandleInterval

from ..auth.dependencies import require_user
from application.history_archive import HistoryArchiveError
from ..services.history_requests import build_queries, normalize_symbol
from ..services.history_coverage import build_history_coverage
from ..services.history_sync import (
    DEFAULT_INTERVALS,
    DEFAULT_SYMBOLS,
    DEFAULT_VENUES,
    HistorySyncError,
)
from application.history_storage import HistoryStorageError


router = APIRouter(prefix="/api/v1/history", tags=["history"])


class HistoryJobCreate(BaseModel):
    venue_ids: list[str] = Field(default_factory=lambda: ["binance", "okx", "bybit"], min_length=1, max_length=3)
    symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT"], min_length=1, max_length=20)
    interval: Literal["1d", "1h", "5m"] = "1d"
    start_at: datetime
    end_at: datetime

    @field_validator("venue_ids", "symbols")
    @classmethod
    def values_must_not_be_blank(cls, values: list[str]) -> list[str]:
        if any(not str(value).strip() for value in values):
            raise ValueError("list values must not be blank")
        return values

    @model_validator(mode="after")
    def range_must_be_aware_and_forward(self) -> "HistoryJobCreate":
        if self.start_at.tzinfo is None or self.start_at.utcoffset() is None:
            raise ValueError("start_at must include timezone information")
        if self.end_at.tzinfo is None or self.end_at.utcoffset() is None:
            raise ValueError("end_at must include timezone information")
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class HistorySyncRequest(BaseModel):
    mode: Literal["incremental", "backfill"] = "incremental"
    venue_ids: list[str] = Field(default_factory=lambda: list(DEFAULT_VENUES), min_length=1, max_length=3)
    symbols: list[str] = Field(default_factory=lambda: list(DEFAULT_SYMBOLS), min_length=1, max_length=20)
    intervals: list[Literal["1d", "1h", "5m"]] = Field(default_factory=lambda: list(DEFAULT_INTERVALS), min_length=1, max_length=3)
    lookback_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("venue_ids", "symbols", "intervals")
    @classmethod
    def sync_values_must_not_be_blank(cls, values: list[str]) -> list[str]:
        if any(not str(value).strip() for value in values):
            raise ValueError("sync list values must not be blank")
        return values


def _jobs(request: Request):
    return request.app.state.history_jobs


@router.get("/datasets")
def datasets(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    try:
        storage = request.app.state.history_storage
        items = [storage.dataset_dict(item) for item in storage.list_datasets()]
    except HistoryStorageError as error:
        raise HTTPException(status_code=503, detail="verified history is unavailable") from error
    return {"items": items, "count": len(items)}


@router.get("/coverage")
def coverage(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    try:
        result = build_history_coverage(
            request.app.state.history_storage,
            request.app.state.history_jobs.list(100),
        )
        result["metadata"] = request.app.state.task_store.history_metadata_status()
        return result
    except HistoryStorageError as error:
        raise HTTPException(status_code=503, detail="verified history is unavailable") from error


@router.get("/archive")
def archive_status(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    """Report the Parquet mirror without treating it as the source of truth."""
    try:
        storage = request.app.state.history_storage
        with storage.locked():
            return request.app.state.history_archive.status(storage.list_datasets())
    except HistoryStorageError as error:
        raise HTTPException(status_code=503, detail="verified history is unavailable") from error


@router.post("/archive")
def archive_history(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    """Create or refresh Parquet mirrors for all verified history datasets."""
    try:
        return request.app.state.history_archive.archive_all(request.app.state.history_storage)
    except HistoryArchiveError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except HistoryStorageError as error:
        raise HTTPException(status_code=503, detail="verified history is unavailable") from error


@router.get("/raw-responses")
def raw_response_status(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    """Report the credential-free archive of successful public history pages."""
    archive = getattr(request.app.state, "history_response_archive", None)
    if archive is None:
        return {
            "status": "NOT_CONFIGURED",
            "contract_version": "history-raw-response-v1",
            "response_count": 0,
            "invalid_count": 0,
            "items": [],
        }
    return archive.status()


@router.get("/sync/plan")
def history_sync_plan(
    request: Request,
    mode: Literal["incremental", "backfill"] = "incremental",
    venue_ids: str = Query(default=",".join(DEFAULT_VENUES), max_length=200),
    symbols: str = Query(default=",".join(DEFAULT_SYMBOLS), max_length=800),
    intervals: str = Query(default=",".join(DEFAULT_INTERVALS), max_length=40),
    lookback_days: int | None = Query(default=None, ge=1, le=3650),
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Explain the exact windows an incremental or backfill run would fetch."""
    try:
        return request.app.state.history_sync.plan(
            mode=mode,
            venues=_csv(venue_ids),
            symbols=_csv(symbols),
            intervals=_csv(intervals),
            lookback_days=lookback_days,
        )
    except HistorySyncError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/sync/status")
def history_sync_status(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    """Expose the independent scheduler heartbeat and retry state."""
    try:
        return request.app.state.history_scheduler_state.snapshot()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="history scheduler state is unavailable") from error


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
def submit_history_sync(
    payload: HistorySyncRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Submit only missing/stale windows and retain upstream failures in the job ledger."""
    try:
        return request.app.state.history_sync.submit(
            request.app.state.history_jobs,
            mode=payload.mode,
            venues=payload.venue_ids,
            symbols=payload.symbols,
            intervals=payload.intervals,
            lookback_days=payload.lookback_days,
        )
    except HistorySyncError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except HistoryStorageError as error:
        raise HTTPException(status_code=503, detail="verified history is unavailable") from error


@router.get("/candles")
def candles(
    request: Request,
    venue_id: str = Query(..., min_length=1, max_length=32),
    symbol: str = Query(..., min_length=2, max_length=32),
    interval: Literal["1d", "1h", "5m"] = "1d",
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
    tail: bool = False,
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Read verified candles for charting, screening, and backtest preflight."""
    try:
        venue = str(venue_id).strip().lower()
        base, quote, _ = normalize_symbol(venue, symbol)
        instrument_key = f"{venue}:spot:{base}/{quote}"
        storage = request.app.state.history_storage
        dataset = storage.find_dataset(
            venue_id=venue,
            instrument_key=instrument_key,
            interval=interval,
        )
        if dataset is None:
            raise HTTPException(status_code=404, detail="verified history dataset was not found")
        page = storage.read_page(dataset, start_at=start_at, end_at=end_at, limit=limit, tail=tail)
    except HTTPException:
        raise
    except HistoryStorageError as error:
        raise HTTPException(status_code=503, detail="verified history is unavailable") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "dataset": storage.dataset_dict(dataset),
        "items": [_candle_dict(item) for item in page.items],
        "count": len(page.items),
        "total_count": page.total_count,
        "truncated": page.truncated,
    }


@router.get("/jobs")
def history_jobs(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    items = _jobs(request).list()
    return {"items": items, "count": len(items)}


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
def create_history_job(
    payload: HistoryJobCreate,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    try:
        queries = build_queries(
            payload.venue_ids,
            payload.symbols,
            interval=CandleInterval.parse(payload.interval),
            start_at=payload.start_at,
            end_at=payload.end_at,
        )
        return _jobs(request).submit(queries)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/jobs/{job_id}")
def history_job(job_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    job = _jobs(request).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="history job was not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_history_job(job_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    try:
        return _jobs(request).cancel(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="history job was not found") from error


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_history_job(job_id: str, request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    try:
        return _jobs(request).retry(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="history job was not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _candle_dict(candle) -> dict[str, object]:
    return {
        "open_time": candle.open_time.isoformat(),
        "close_time": candle.close_time.isoformat(),
        "open": str(candle.open),
        "high": str(candle.high),
        "low": str(candle.low),
        "close": str(candle.close),
        "volume": str(candle.volume),
        "quote_volume": str(candle.quote_volume),
        "trade_count": candle.trade_count,
    }


def _csv(value: str) -> list[str]:
    return [item.strip() for item in str(value).split(",") if item.strip()]
