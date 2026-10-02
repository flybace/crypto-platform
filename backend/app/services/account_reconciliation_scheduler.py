"""Scheduled account reconciliation: fetch, persist, diff, anomaly-check, pause on anomaly.

Read-only semantics: our "local projection" is the previously persisted
snapshot. Each tick:
  1. Fetch the account snapshot via the gateway (skip when unconfigured).
  2. Validate: no negative balances, snapshot state is not ERROR.
  3. Diff against the previous snapshot -> ledger entries (audit trail).
  4. Persist the new snapshot.
  5. Anomaly -> safety pause (fail closed): fetch failure, ERROR state,
     negative balance, or an unexplained single-tick drop above threshold.
"""

from __future__ import annotations

import threading
import time
import traceback
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

# Single-tick drop that counts as an anomaly (50% of an asset's previous total).
ANOMALY_DROP_RATIO = Decimal("0.5")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AccountReconciliationScheduler:
    def __init__(
        self,
        *,
        ledger_store: Any,
        gateway: Any | None,
        interval_seconds: int = 300,
        account_id: str = "default",
        venue_id: str = "binance",
    ) -> None:
        if int(interval_seconds) <= 0:
            raise ValueError("interval_seconds must be positive")
        self._ledger = ledger_store
        self._gateway = gateway
        self._interval = int(interval_seconds)
        self._account_id = str(account_id)
        self._venue_id = str(venue_id)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_run: dict[str, Any] = {"status": "not_started", "at": None, "error": None}

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="account-recon-scheduler", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread and thread.is_alive():
            thread.join(timeout=10)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "interval_seconds": self._interval,
                "account_id": self._account_id,
                "venue_id": self._venue_id,
                "gateway_configured": self._gateway is not None,
                "last_run": dict(self._last_run),
                "pause": self._ledger.get_pause(),
            }

    def _pause(self, reason: str) -> None:
        try:
            self._ledger.set_paused(reason)
        except Exception:
            pass

    def run_once(self) -> dict[str, Any]:
        """Execute a single reconciliation tick. Used by the loop and the API."""
        started = _utcnow()
        if self._gateway is None:
            result = {"status": "skipped", "reason": "account gateway not configured", "at": started.isoformat()}
            with self._lock:
                self._last_run = result
            return result
        try:
            snapshot = self._gateway.fetch_account(self._account_id)
            balances = [
                {"asset": b.asset, "available": str(b.available), "total": str(b.total)}
                for b in snapshot.balances
            ]
            # Validate: negative balances are impossible -> data anomaly.
            for b in balances:
                if Decimal(b["available"]) < 0 or Decimal(b["total"]) < 0:
                    reason = f"negative balance for {b['asset']}: {b}"
                    self._pause(reason)
                    return self._finish({"status": "anomaly", "reason": reason, "at": started.isoformat()})
            state = snapshot.state.value if hasattr(snapshot.state, "value") else str(snapshot.state)
            if state == "ERROR":
                reason = "exchange snapshot reported ERROR state"
                self._pause(reason)
                return self._finish({"status": "anomaly", "reason": reason, "at": started.isoformat()})

            # Diff vs previous snapshot -> ledger entries + anomaly check.
            previous = self._ledger.latest_snapshot(self._account_id, self._venue_id)
            prev_map = {b["asset"]: Decimal(b["total"]) for b in (previous["balances"] if previous else [])}
            new_entries = 0
            anomalies: list[str] = []
            for b in balances:
                asset = b["asset"]
                total = Decimal(b["total"])
                prev_total = prev_map.get(asset)
                if prev_total is None:
                    continue  # first sighting, no baseline
                delta = total - prev_total
                if delta != 0:
                    self._ledger.append_entry(
                        self._account_id, self._venue_id, asset, str(delta),
                        "snapshot_delta", snapshot.fetched_at,
                    )
                    new_entries += 1
                    if prev_total > 0 and delta < 0 and (-delta / prev_total) >= ANOMALY_DROP_RATIO:
                        anomalies.append(f"{asset} dropped {(-delta/prev_total)*100:.1f}% in one tick")

            self._ledger.save_snapshot(
                self._account_id, self._venue_id, snapshot.fetched_at,
                balances, list(snapshot.open_order_ids), state,
            )
            # Record a reconciliation run (balanced = no anomalies).
            self._ledger.save_reconciliation_run(
                self._account_id, self._venue_id, _utcnow(),
                not anomalies,
                [{"asset": a, "local": "", "external": a} for a in anomalies],
                "RECONCILED" if not anomalies else "RECONCILIATION_REQUIRED",
            )
            if anomalies:
                reason = "anomalous balance change: " + "; ".join(anomalies)
                self._pause(reason)
                return self._finish({"status": "anomaly", "reason": reason, "at": started.isoformat()})
            return self._finish({
                "status": "completed", "balanced": True,
                "new_ledger_entries": new_entries,
                "at": started.isoformat(), "error": None,
            })
        except Exception as error:
            reason = f"reconciliation tick failed: {error}"
            self._pause(reason)
            return self._finish({
                "status": "failed", "at": started.isoformat(),
                "error": reason, "trace": traceback.format_exc(limit=5),
            })

    def _finish(self, result: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._last_run = result
        return result

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.run_once()
            except Exception:
                continue
