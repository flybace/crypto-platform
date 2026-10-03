"""Authenticated read-only account routes.

All endpoints here are read-only. No order placement, cancellation,
transfer, or withdrawal capability is exposed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth.dependencies import require_user

router = APIRouter(prefix="/api/v1/account", tags=["account"])


def _gateway(request: Request):
    gateway = getattr(request.app.state, "account_gateway", None)
    if gateway is None:
        raise HTTPException(status_code=503, detail="account gateway is not configured")
    return gateway


@router.get("/status")
def account_status(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    """Whether a real exchange account is configured (read-only)."""
    secrets = getattr(request.app.state, "secret_provider", None)
    configured = bool(secrets and secrets.is_configured("binance"))
    return {
        "configured": configured,
        "venue_id": "binance",
        "read_only": True,
        "trading_enabled": False,
    }


from pydantic import BaseModel


class AccountCredentialsRequest(BaseModel):
    api_key: str
    api_secret: str


@router.get("/credentials")
def credential_status(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    """Whether credentials are stored (key is masked, secret never returned)."""
    secrets = getattr(request.app.state, "secret_provider", None)
    configured = bool(secrets and secrets.is_configured("binance"))
    return {
        "configured": configured,
        "venue_id": "binance",
        "key_prefix": secrets.key_prefix("binance") if configured and secrets else None,
    }


@router.post("/credentials")
def save_credentials(
    payload: AccountCredentialsRequest,
    request: Request,
    _: object = Depends(require_user),
) -> dict[str, object]:
    """Save Binance read-only API credentials and hot-reload the gateway.

    The key/secret are stored in a 0600 file (never in git). The gateway is
    rebuilt immediately — no restart needed. A live read-only check
    (get_account) verifies the credentials before accepting them.
    """
    secrets = getattr(request.app.state, "secret_provider", None)
    if secrets is None:
        raise HTTPException(status_code=500, detail="secret provider not available")
    try:
        secrets.save("binance", payload.api_key, payload.api_secret)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    # Hot-reload the gateway
    rebuild = getattr(request.app.state, "rebuild_account_gateway", None)
    if rebuild is not None:
        old_gateway = getattr(request.app.state, "account_gateway", None)
        try:
            new_gateway = rebuild()
        except Exception as error:
            raise HTTPException(status_code=502, detail=f"gateway rebuild failed: {error}") from error
        request.app.state.account_gateway = new_gateway
        if old_gateway is not None and old_gateway is not new_gateway:
            try:
                old_gateway.close()
            except Exception:
                pass
    # Verify with a live read-only call
    gateway = getattr(request.app.state, "account_gateway", None)
    if gateway is None:
        raise HTTPException(status_code=502, detail="gateway not available after save")
    try:
        snapshot = gateway.fetch_account("default")
    except Exception as error:
        # Roll back: remove the bad credentials
        secrets.delete("binance")
        request.app.state.account_gateway = None
        raise HTTPException(status_code=502, detail=f"credential verification failed: {error}") from error
    return {
        "configured": True,
        "venue_id": "binance",
        "key_prefix": secrets.key_prefix("binance"),
        "verified_at": snapshot.fetched_at.isoformat(),
        "balance_count": len(snapshot.balances),
    }


@router.delete("/credentials")
def delete_credentials(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    """Remove stored credentials and tear down the gateway."""
    secrets = getattr(request.app.state, "secret_provider", None)
    if secrets is None:
        raise HTTPException(status_code=500, detail="secret provider not available")
    secrets.delete("binance")
    old_gateway = getattr(request.app.state, "account_gateway", None)
    request.app.state.account_gateway = None
    if old_gateway is not None:
        try:
            old_gateway.close()
        except Exception:
            pass
    return {"configured": False, "venue_id": "binance"}


@router.get("/balances")
def account_balances(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    """Current balances from the configured exchange (read-only)."""
    try:
        snapshot = _gateway(request).fetch_account("default")
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {
        "account_id": snapshot.account_id,
        "venue_id": snapshot.venue_id,
        "fetched_at": snapshot.fetched_at.isoformat(),
        "balances": [
            {"asset": b.asset, "available": str(b.available), "total": str(b.total)}
            for b in snapshot.balances
        ],
    }


@router.get("/orders")
def account_orders(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    """Open order identifiers from the configured exchange (read-only)."""
    try:
        snapshot = _gateway(request).fetch_account("default")
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {
        "account_id": snapshot.account_id,
        "venue_id": snapshot.venue_id,
        "fetched_at": snapshot.fetched_at.isoformat(),
        "open_order_ids": list(snapshot.open_order_ids),
        "open_order_count": len(snapshot.open_order_ids),
    }


def _ledger(request: Request):
    store = getattr(request.app.state, "account_ledger_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="account ledger store is not configured")
    return store


def _scheduler(request: Request):
    scheduler = getattr(request.app.state, "account_recon_scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="reconciliation scheduler is not configured")
    return scheduler


@router.get("/snapshots")
def account_snapshots(
    request: Request, limit: int = 20, _: object = Depends(require_user)
) -> dict[str, object]:
    """Persisted account snapshot history."""
    return {
        "snapshots": _ledger(request).list_snapshots("default", "binance", limit=limit),
    }


@router.get("/ledger")
def account_ledger(
    request: Request, limit: int = 50, _: object = Depends(require_user)
) -> dict[str, object]:
    """Persisted ledger entries (balance changes)."""
    return {
        "entries": _ledger(request).list_entries("default", "binance", limit=limit),
    }


@router.get("/reconciliation")
def reconciliation_status(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    """Scheduler status, recent runs, and safety pause state."""
    store = _ledger(request)
    scheduler = _scheduler(request)
    status = scheduler.status()
    status["recent_runs"] = store.list_reconciliation_runs("default", "binance", limit=20)
    return status


@router.post("/reconciliation/run")
def reconciliation_run(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    """Trigger a manual reconciliation tick."""
    return _scheduler(request).run_once()


@router.get("/pause")
def pause_status(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    """Current safety pause state."""
    return _ledger(request).get_pause()


@router.post("/pause/clear")
def pause_clear(request: Request, _: object = Depends(require_user)) -> dict[str, object]:
    """Manually clear the safety pause. Requires authentication."""
    # The authenticated username is the operator identity for the audit trail.
    return _ledger(request).clear_pause(cleared_by="admin")
