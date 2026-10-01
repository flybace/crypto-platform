"""Durable, bounded records for explicit L2 opportunity scans."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from threading import RLock
from uuid import uuid4

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore
from backend.app.services.notifications import NotificationService


OPPORTUNITY_STATES = frozenset({"VALIDATED", "BLOCKED", "EXPIRED"})


class OpportunityLogService:
    """Store scan evidence without treating a scan as an order authorization."""

    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        state_store: StateStore | None = None,
        notifications: NotificationService | None = None,
        alert_cooldown_seconds: int = 300,
    ) -> None:
        if alert_cooldown_seconds < 0:
            raise ValueError("alert cooldown must not be negative")
        self._state = state_store or (JsonStateStore(state_path) if state_path is not None else None)
        self._notifications = notifications
        self._alert_cooldown = timedelta(seconds=alert_cooldown_seconds)
        self._records: list[dict[str, object]] = []
        self._lock = RLock()
        self._load()

    def record(self, opportunity: dict[str, object]) -> dict[str, object]:
        """Persist one normalized scan and optionally emit a deduplicated notice."""
        record = self._normalize(opportunity)
        with self._lock:
            self._load()
            fingerprint = self._fingerprint(record)
            record["alert_fingerprint"] = fingerprint
            record["alerted"] = False
            record["notification_id"] = None
            if self._should_alert(record, fingerprint):
                notification = self._notifications.create(
                    title="盘口机会通过成本门禁",
                    message=(
                        f"{record['symbol']} · {record['buy_venue_id']} → {record['sell_venue_id']} · "
                        f"净边际 {record['net_edge_quote']} USDT；仅研究记录，未提交订单。"
                    ),
                    event_type="opportunity_validated",
                    severity="warning",
                    action_link="/market",
                )
                record["alerted"] = True
                record["notification_id"] = notification["id"]
            self._records.insert(0, record)
            self._records = self._records[:200]
            self._persist_locked()
            return deepcopy(record)

    def items(self, *, state: str | None = None, limit: int = 50) -> list[dict[str, object]]:
        wanted = None if state is None else str(state).strip().upper()
        if wanted and wanted not in OPPORTUNITY_STATES:
            raise ValueError("opportunity state is invalid")
        with self._lock:
            self._load()
            records = [item for item in self._records if wanted is None or item.get("state") == wanted]
            return deepcopy(records[: max(1, min(int(limit), 200))])

    def summary(self, *, limit: int = 20) -> dict[str, object]:
        records = self.items(limit=limit)
        with self._lock:
            validated = [item for item in self._records if item.get("state") == "VALIDATED"]
            return {
                "module": "opportunities",
                "record_count": len(self._records),
                "validated_count": len(validated),
                "blocked_count": sum(1 for item in self._records if item.get("state") == "BLOCKED"),
                "alert_count": sum(1 for item in self._records if item.get("alerted")),
                "latest_validated": deepcopy(validated[0]) if validated else None,
                "items": records,
                "research_only": True,
                "execution_eligible": False,
            }

    @staticmethod
    def _normalize(opportunity: dict[str, object]) -> dict[str, object]:
        state = str(opportunity.get("state", "BLOCKED")).strip().upper()
        if state not in OPPORTUNITY_STATES:
            raise ValueError("opportunity state is invalid")
        now = datetime.now(UTC)
        created_at = _timestamp(opportunity.get("created_at"), now)
        expires_at = _timestamp(opportunity.get("expires_at"), created_at + timedelta(seconds=1))
        return {
            "record_id": f"opportunity-{uuid4().hex}",
            "state": state,
            "blocking_reason": _text(opportunity.get("blocking_reason")),
            "symbol": _text(opportunity.get("symbol"), "—"),
            "buy_venue_id": _text(opportunity.get("buy_venue_id"), "—"),
            "sell_venue_id": _text(opportunity.get("sell_venue_id"), "—"),
            "instrument_key": _text(opportunity.get("instrument_key")),
            "quantity": _text(opportunity.get("quantity"), "0"),
            "buy_price": _text(opportunity.get("buy_price"), "0"),
            "sell_price": _text(opportunity.get("sell_price"), "0"),
            "gross_edge_quote": _text(opportunity.get("gross_edge_quote"), "0"),
            "fees_quote": _text(opportunity.get("fees_quote"), "0"),
            "slippage_quote": _text(opportunity.get("slippage_quote"), "0"),
            "other_costs_quote": _text(opportunity.get("other_costs_quote"), "0"),
            "net_edge_quote": _text(opportunity.get("net_edge_quote"), "0"),
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "observed_at": now.isoformat(),
            "note": _text(opportunity.get("note")),
        }

    def _should_alert(self, record: dict[str, object], fingerprint: str) -> bool:
        if self._notifications is None or record.get("state") != "VALIDATED":
            return False
        config = self._notifications.config()
        if not (bool(config.get("enabled")) and bool(config.get("in_app_enabled"))):
            return False
        preferences = config.get("event_preferences")
        if isinstance(preferences, dict):
            for key in ("opportunity", "opportunity_validated"):
                preference = preferences.get(key)
                if isinstance(preference, dict) and preference.get("enabled") is False:
                    return False
        cutoff = datetime.now(UTC) - self._alert_cooldown
        return not any(
            item.get("alerted")
            and item.get("alert_fingerprint") == fingerprint
            and _timestamp(item.get("observed_at"), datetime.min.replace(tzinfo=UTC)) >= cutoff
            for item in self._records
        )

    @staticmethod
    def _fingerprint(record: dict[str, object]) -> str:
        values = {
            key: record.get(key)
            for key in ("state", "instrument_key", "buy_venue_id", "sell_venue_id", "buy_price", "sell_price", "net_edge_quote")
        }
        return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def _load(self) -> None:
        if self._state is None:
            return
        self._records = []
        try:
            payload = self._state.load({"version": 1, "records": []})
        except JsonStateError as error:
            raise RuntimeError("opportunity log state is unreadable") from error
        records = payload.get("records", []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            raise RuntimeError("opportunity log state must contain an array")
        self._records = [deepcopy(item) for item in records if isinstance(item, dict)][:200]

    def _persist_locked(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save({"version": 1, "records": self._records[:200]})
        except JsonStateError as error:
            raise RuntimeError("opportunity log state cannot be saved") from error


def _text(value: object, default: str = "") -> str:
    return str(value).strip()[:500] if value is not None else default


def _timestamp(value: object, default: datetime) -> datetime:
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return default
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return default
    return parsed.astimezone(UTC)
