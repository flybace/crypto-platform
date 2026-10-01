"""Authenticated market overview routes backed by the reusable domain core."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from ..auth.dependencies import require_user
from ..services.history_requests import normalize_symbol
from ..services.market_summary import build_market_rankings, build_market_summary
from application.history_storage import HistoryStorageError
from application.spread_research import HistoricalSpreadResearch, SpreadResearchError
from ports.market_archive import MarketArchiveError
from ports.market_state import MarketStateStoreError
from application.opportunity import OpportunityScanner
from domain.opportunity import CostModel, FeeSchedule


router = APIRouter(prefix="/api/v1/market", tags=["market"])
VENUE_IDS = ("binance", "okx", "bybit")


class OpportunityScanRequest(BaseModel):
    symbol: str = Field(default="BTC/USDT", min_length=2, max_length=32)
    buy_venue_id: str = Field(default="binance", min_length=1, max_length=32)
    sell_venue_id: str = Field(default="okx", min_length=1, max_length=32)
    taker_fee_bps: Decimal = Field(default=Decimal("10"), ge=0, le=1000)
    slippage_bps: Decimal = Field(default=Decimal("5"), ge=0, le=1000)
    inventory_cost_quote: Decimal = Field(default=Decimal("0"), ge=0)
    transfer_cost_quote: Decimal = Field(default=Decimal("0"), ge=0)
    latency_buffer_quote: Decimal = Field(default=Decimal("0"), ge=0)
    failure_risk_buffer_quote: Decimal = Field(default=Decimal("0"), ge=0)
    max_age_seconds: int = Field(default=2, ge=1, le=60)


def _view(request: Request):
    return request.app.state.market_status_view


@router.get("/overview")
def overview(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    venues = [_view(request).get(venue_id) for venue_id in VENUE_IDS]
    return {
        "execution_mode": request.app.state.settings.execution_mode,
        "venues": venues,
    }


@router.get("/summary")
def market_summary(
    request: Request,
    interval: str = Query(default="1h", pattern="^(1d|1h|5m)$"),
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Return the history-backed dashboard snapshot used by the overview."""
    try:
        result = build_market_summary(request.app.state.history_storage, interval=interval)
    except (HistoryStorageError, ValueError) as error:
        raise HTTPException(status_code=503, detail="verified market history is unavailable") from error
    result["execution_mode"] = request.app.state.settings.execution_mode
    result["market_mode"] = "24/7 spot research"
    return result


@router.get("/rankings")
def market_rankings(
    request: Request,
    interval: str = Query(default="1h", pattern="^(1d|1h|5m)$"),
    rank_by: str = Query(default="rise", pattern="^(rise|fall|spread)$"),
    limit: int = Query(default=20, ge=1, le=100),
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Return bounded data-driven rankings for the dashboard and future GACE App."""
    try:
        return build_market_rankings(
            request.app.state.history_storage,
            interval=interval,
            rank_by=rank_by,
            limit=limit,
        )
    except (HistoryStorageError, ValueError) as error:
        raise HTTPException(status_code=503, detail="verified market history is unavailable") from error


@router.get("/instruments")
def market_instruments(
    request: Request,
    venue_id: str = Query(default="binance", min_length=1, max_length=32),
    quote_asset: str = Query(default="USDT", min_length=1, max_length=20),
    search: str = Query(default="", max_length=40),
    limit: int = Query(default=80, ge=1, le=200),
    refresh: bool = False,
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Return the current public spot catalog for one configured venue."""
    try:
        return request.app.state.instrument_catalog.list(
            venue_id,
            quote_asset=quote_asset,
            search=search,
            limit=limit,
            refresh=refresh,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="instrument catalog is unavailable") from error


@router.get("/tickers")
def market_tickers(
    request: Request,
    quote_asset: str = Query(default="USDT", min_length=1, max_length=20),
    search: str = Query(default="", max_length=40),
    sort_by: str = Query(default="volume", pattern="^(volume|change|spread|symbol)$"),
    limit: int = Query(default=60, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10_000),
    refresh: bool = False,
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Return current public 24-hour tickers aligned across venues."""
    service = getattr(request.app.state, "public_tickers", None)
    if service is None:
        raise HTTPException(status_code=503, detail="public ticker service is not configured")
    try:
        return service.snapshot(
            quote_asset=quote_asset,
            search=search,
            sort_by=sort_by,
            limit=limit,
            offset=offset,
            refresh=refresh,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="public ticker service is unavailable") from error


@router.get("/status/{venue_id}")
def market_status(
    venue_id: str,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    return _view(request).get(venue_id)


@router.get("/snapshot")
def market_snapshot(
    instrument_key: str,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    state_store = request.app.state.market_state_store
    if state_store is None:
        raise HTTPException(status_code=404, detail="market state is not configured")
    try:
        snapshot = state_store.read_snapshot(instrument_key)
    except MarketStateStoreError as error:
        raise HTTPException(status_code=503, detail="market state is unavailable") from error
    if snapshot is None:
        raise HTTPException(status_code=404, detail="market snapshot was not found")
    return {
        "instrument_key": snapshot.instrument.key,
        "venue_id": snapshot.instrument.venue_id,
        "native_symbol": snapshot.instrument.native_symbol,
        "exchange_timestamp": snapshot.exchange_timestamp.isoformat(),
        "received_timestamp": snapshot.received_timestamp.isoformat(),
        "sequence": snapshot.sequence,
        "bids": [{"price": str(level.price), "quantity": str(level.quantity)} for level in snapshot.bids],
        "asks": [{"price": str(level.price), "quantity": str(level.quantity)} for level in snapshot.asks],
    }


@router.get("/archive/order-books")
def archived_order_books(
    request: Request,
    instrument_key: str = Query(..., min_length=3, max_length=160),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    depth: int = Query(default=20, ge=1, le=100),
    tail: bool = True,
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Read bounded, authenticated public L2 history; never an execution input."""
    normalized_key = instrument_key.strip()
    if not normalized_key or any(character.isspace() for character in normalized_key):
        raise HTTPException(status_code=422, detail="market archive instrument key is invalid")
    if start_at is not None and (start_at.tzinfo is None or start_at.utcoffset() is None):
        raise HTTPException(status_code=422, detail="start_at must include timezone information")
    if end_at is not None and (end_at.tzinfo is None or end_at.utcoffset() is None):
        raise HTTPException(status_code=422, detail="end_at must include timezone information")
    if start_at is not None and end_at is not None and end_at < start_at:
        raise HTTPException(status_code=422, detail="end_at must not be before start_at")
    archive = getattr(request.app.state, "market_archive", None)
    if archive is None:
        raise HTTPException(status_code=503, detail="public market archive is disabled")
    try:
        snapshots = archive.read_snapshots(
            normalized_key,
            start=start_at,
            end=end_at,
            limit=limit,
            tail=tail,
        )
    except MarketArchiveError as error:
        raise HTTPException(status_code=503, detail="public market archive is unavailable") from error
    items = [
        {
            "instrument_key": snapshot.instrument.key,
            "venue_id": snapshot.instrument.venue_id,
            "symbol": snapshot.instrument.canonical_symbol,
            "native_symbol": snapshot.instrument.native_symbol,
            "exchange_timestamp": snapshot.exchange_timestamp.isoformat(),
            "received_timestamp": snapshot.received_timestamp.isoformat(),
            "sequence": snapshot.sequence,
            "bids": [
                {"price": str(level.price), "quantity": str(level.quantity)}
                for level in snapshot.bids[:depth]
            ],
            "asks": [
                {"price": str(level.price), "quantity": str(level.quantity)}
                for level in snapshot.asks[:depth]
            ],
        }
        for snapshot in snapshots
    ]
    return {
        "instrument_key": normalized_key,
        "count": len(items),
        "limit": limit,
        "depth": depth,
        "tail": tail,
        "as_of": items[-1]["received_timestamp"] if items else None,
        "items": items,
    }


@router.get("/archive/status")
def market_archive_status(
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Return archive governance metadata without reading L2 payloads."""
    archive = getattr(request.app.state, "market_archive", None)
    if archive is None:
        return {"enabled": False, "status": "DISABLED"}
    try:
        details = archive.status()
    except MarketArchiveError as error:
        raise HTTPException(status_code=503, detail="public market archive is unavailable") from error
    return {"enabled": True, "status": "READY", **details}

@router.get("/compare")
def compare_markets(
    request: Request,
    symbol: str = Query(default="BTC/USDT", min_length=2, max_length=32),
    interval: str = Query(default="1h", pattern="^(1d|1h|5m)$"),
    fee_bps: Decimal = Query(default=Decimal("10"), ge=0, le=1000),
    slippage_bps: Decimal = Query(default=Decimal("5"), ge=0, le=1000),
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Compare the latest verified closes with an explicit cost estimate.

    This is a research indicator, not an executable arbitrage signal: closes
    are not synchronized L2 bids/asks and inventory or transfer costs remain
    outside this bounded comparison.
    """
    try:
        normalized = normalize_symbol("binance", symbol)
        canonical = f"{normalized[0]}/{normalized[1]}"
        storage = request.app.state.history_storage
        markets: list[dict[str, object]] = []
        for venue_id in VENUE_IDS:
            dataset = storage.find_dataset(
                venue_id=venue_id,
                instrument_key=f"{venue_id}:spot:{canonical}",
                interval=interval,
            )
            if dataset is None:
                markets.append({"venue_id": venue_id, "status": "MISSING", "close": None, "row_count": 0})
                continue
            if dataset.manifest.gap_count or dataset.manifest.duplicate_count:
                markets.append({"venue_id": venue_id, "status": "BLOCKED", "close": None, "row_count": dataset.manifest.row_count, "dataset_id": dataset.manifest.dataset_id})
                continue
            page = storage.read_page(dataset, limit=1, tail=True)
            if not page.items:
                markets.append({"venue_id": venue_id, "status": "EMPTY", "close": None, "row_count": dataset.manifest.row_count, "dataset_id": dataset.manifest.dataset_id})
                continue
            candle = page.items[-1]
            markets.append({
                "venue_id": venue_id,
                "status": "AVAILABLE",
                "close": str(candle.close),
                "open_time": candle.open_time.isoformat(),
                "close_time": candle.close_time.isoformat(),
                "row_count": dataset.manifest.row_count,
                "dataset_id": dataset.manifest.dataset_id,
            })
    except (HistoryStorageError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    available = [item for item in markets if item["status"] == "AVAILABLE"]
    if len(available) < 2:
        return {
            "symbol": canonical,
            "interval": interval,
            "markets": markets,
            "comparison": None,
            "note": "至少需要两个市场的同周期已校验数据",
        }
    buy = min(available, key=lambda item: Decimal(str(item["close"])))
    sell = max(available, key=lambda item: Decimal(str(item["close"])))
    buy_price = Decimal(str(buy["close"]))
    sell_price = Decimal(str(sell["close"]))
    gross_pct = (sell_price / buy_price - Decimal("1")) * Decimal("100")
    cost_pct = (fee_bps * Decimal("2") + slippage_bps * Decimal("2")) / Decimal("100")
    net_pct = gross_pct - cost_pct
    return {
        "symbol": canonical,
        "interval": interval,
        "markets": markets,
        "comparison": {
            "buy_venue_id": buy["venue_id"],
            "sell_venue_id": sell["venue_id"],
            "buy_close": str(buy_price),
            "sell_close": str(sell_price),
            "gross_spread_pct": str(gross_pct),
            "estimated_cost_pct": str(cost_pct),
            "net_spread_pct": str(net_pct),
            "research_positive": net_pct > 0,
        },
        "note": "使用各市场最后一根收盘价估算；不代表同时刻盘口可成交价差。",
    }


@router.get("/spread-history")
def historical_spread(
    request: Request,
    symbol: str = Query(default="BTC/USDT", min_length=2, max_length=32),
    buy_venue_id: str = Query(default="binance", min_length=1, max_length=32),
    sell_venue_id: str = Query(default="bybit", min_length=1, max_length=32),
    interval: str = Query(default="1h", pattern="^(1d|1h|5m)$"),
    fee_bps: Decimal = Query(default=Decimal("10"), ge=0, le=1000),
    slippage_bps: Decimal = Query(default=Decimal("5"), ge=0, le=1000),
    min_net_spread_bps: Decimal = Query(default=Decimal("0"), ge=0, le=10000),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=20, ge=1, le=200),
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Compare aligned historical closes after a declared two-leg cost model.

    This route is deliberately research-only. It never reads L2 state and the
    result cannot be used as an order or execution authorization.
    """
    try:
        service = request.app.state.spread_research
        if not isinstance(service, HistoricalSpreadResearch):
            raise SpreadResearchError("historical spread research is not configured")
        return service.run(
            symbol=symbol,
            buy_venue_id=buy_venue_id,
            sell_venue_id=sell_venue_id,
            interval=interval,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            min_net_spread_bps=min_net_spread_bps,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
        )
    except SpreadResearchError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except HistoryStorageError as error:
        raise HTTPException(status_code=503, detail="verified market history is unavailable") from error


@router.get("/opportunities/history")
def opportunity_history(
    request: Request,
    state: str | None = Query(default=None, pattern="^(VALIDATED|BLOCKED|EXPIRED)$"),
    limit: int = Query(default=30, ge=1, le=200),
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Return persisted scan evidence, never an execution queue."""
    service = request.app.state.opportunity_log
    items = service.items(state=state, limit=limit)
    return {"items": items, "count": len(items), "summary": service.summary(limit=limit)}


@router.get("/opportunities/summary")
def opportunity_summary(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    return request.app.state.opportunity_log.summary()


@router.get("/opportunities")
def opportunities(
    request: Request,
    symbol: str = Query(default="BTC/USDT", min_length=2, max_length=32),
    buy_venue_id: str = Query(default="binance", min_length=1, max_length=32),
    sell_venue_id: str = Query(default="okx", min_length=1, max_length=32),
    taker_fee_bps: Decimal = Query(default=Decimal("10"), ge=0, le=1000),
    slippage_bps: Decimal = Query(default=Decimal("5"), ge=0, le=1000),
    inventory_cost_quote: Decimal = Query(default=Decimal("0"), ge=0),
    transfer_cost_quote: Decimal = Query(default=Decimal("0"), ge=0),
    latency_buffer_quote: Decimal = Query(default=Decimal("0"), ge=0),
    failure_risk_buffer_quote: Decimal = Query(default=Decimal("0"), ge=0),
    max_age_seconds: int = Query(default=2, ge=1, le=60),
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Scan synchronized L2 snapshots; never falls back to K-line closes."""
    try:
        return _scan_opportunity(
            request,
            symbol=symbol,
            buy_venue_id=buy_venue_id,
            sell_venue_id=sell_venue_id,
            taker_fee_bps=taker_fee_bps,
            slippage_bps=slippage_bps,
            inventory_cost_quote=inventory_cost_quote,
            transfer_cost_quote=transfer_cost_quote,
            latency_buffer_quote=latency_buffer_quote,
            failure_risk_buffer_quote=failure_risk_buffer_quote,
            max_age_seconds=max_age_seconds,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (MarketStateStoreError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/opportunities/scan", status_code=status.HTTP_201_CREATED)
def record_opportunity_scan(
    payload: OpportunityScanRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Record one explicit scan and optionally emit a deduplicated notice."""
    try:
        result = _scan_opportunity(request, **payload.model_dump())
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (MarketStateStoreError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    record = request.app.state.opportunity_log.record(result)
    return {**result, "record_id": record["record_id"], "recorded_at": record["observed_at"], "alerted": record["alerted"]}


def _scan_opportunity(
    request: Request,
    *,
    symbol: str,
    buy_venue_id: str,
    sell_venue_id: str,
    taker_fee_bps: Decimal,
    slippage_bps: Decimal,
    inventory_cost_quote: Decimal,
    transfer_cost_quote: Decimal,
    latency_buffer_quote: Decimal,
    failure_risk_buffer_quote: Decimal,
    max_age_seconds: int,
) -> dict[str, object]:
    """Build the common L2 scan response used by read and record routes."""
    state_store = request.app.state.market_state_store
    if state_store is None:
        raise LookupError("market state is not configured")
    base, quote, _ = normalize_symbol(str(buy_venue_id), symbol)
    canonical = f"{base}/{quote}"
    buy_id = str(buy_venue_id).strip().lower()
    sell_id = str(sell_venue_id).strip().lower()
    buy_snapshot = state_store.read_snapshot(f"{buy_id}:spot:{canonical}")
    sell_snapshot = state_store.read_snapshot(f"{sell_id}:spot:{canonical}")
    now = datetime.now(UTC)
    if buy_snapshot is None or sell_snapshot is None:
        return {
            "state": "BLOCKED",
            "blocking_reason": "L2_SNAPSHOT_MISSING",
            "symbol": canonical,
            "buy_venue_id": buy_id,
            "sell_venue_id": sell_id,
            "instrument_key": f"{buy_id}:spot:{canonical}",
            "quantity": "0",
            "buy_price": "0",
            "sell_price": "0",
            "gross_edge_quote": "0",
            "fees_quote": "0",
            "slippage_quote": "0",
            "other_costs_quote": "0",
            "net_edge_quote": "0",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=1)).isoformat(),
            "note": "未读取到两个交易所的同步盘口；历史 K 线不会替代可执行盘口。",
        }
    scanner = OpportunityScanner(max_market_age_seconds=max_age_seconds)
    opportunity = scanner.scan(
        buy_snapshot,
        sell_snapshot,
        FeeSchedule(buy_snapshot.instrument.venue_id, taker_fee_bps, taker_fee_bps, now),
        FeeSchedule(sell_snapshot.instrument.venue_id, taker_fee_bps, taker_fee_bps, now),
        CostModel(
            expected_slippage_bps=slippage_bps,
            inventory_cost_quote=inventory_cost_quote,
            transfer_cost_quote=transfer_cost_quote,
            latency_buffer_quote=latency_buffer_quote,
            failure_risk_buffer_quote=failure_risk_buffer_quote,
        ),
        now,
        ttl=timedelta(seconds=1),
    )
    return {
        "state": opportunity.state.value,
        "blocking_reason": opportunity.blocking_reason,
        "symbol": canonical,
        "buy_venue_id": opportunity.buy_venue_id,
        "sell_venue_id": opportunity.sell_venue_id,
        "instrument_key": opportunity.instrument_key,
        "quantity": str(opportunity.quantity),
        "buy_price": str(opportunity.buy_price),
        "sell_price": str(opportunity.sell_price),
        "gross_edge_quote": str(opportunity.gross_edge_quote),
        "fees_quote": str(opportunity.fees_quote),
        "slippage_quote": str(opportunity.slippage_quote),
        "other_costs_quote": str(opportunity.other_costs_quote),
        "net_edge_quote": str(opportunity.net_edge_quote),
        "created_at": opportunity.created_at.isoformat(),
        "expires_at": opportunity.expires_at.isoformat(),
        "note": "仅表示当前 L2 快照通过成本门禁；两腿原子性、库存和真实执行仍需独立风控。",
    }
