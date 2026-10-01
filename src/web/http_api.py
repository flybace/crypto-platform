"""Minimal read-only HTTP surface for the standalone development runtime."""

import os

from fastapi import FastAPI, HTTPException

from adapters.standalone.file_market_state import FileMarketStateStore
from application.market_data import MarketDataService
from ports.market_state import MarketStateStoreError

from .market_status import MarketStatusView


def create_app(market_data: MarketDataService | None = None) -> FastAPI:
    service = market_data or MarketDataService()
    state_path = os.getenv("CRYPTO_MARKET_STATE_PATH")
    state_store = None if not state_path else FileMarketStateStore(state_path, create=False)
    status_view = MarketStatusView(service, state_store)
    app = FastAPI(title="Crypto Multi-Market Quant Platform", version="0.1.0")
    app.state.market_data = service

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "execution_mode": "DISABLED"}

    @app.get("/v1/market/status/{venue_id}")
    def market_status(venue_id: str) -> dict[str, object]:
        return status_view.get(venue_id)

    @app.get("/v1/market/snapshot")
    def market_snapshot(instrument_key: str) -> dict[str, object]:
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

    return app
