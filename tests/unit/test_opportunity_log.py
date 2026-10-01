from datetime import UTC, datetime, timedelta

import pytest

from backend.app.services.notifications import NotificationService
from backend.app.services.opportunity_log import OpportunityLogService


def _payload(state: str = "VALIDATED") -> dict[str, object]:
    created = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    return {
        "state": state,
        "blocking_reason": None if state == "VALIDATED" else "L2_SNAPSHOT_MISSING",
        "symbol": "BTC/USDT",
        "buy_venue_id": "binance",
        "sell_venue_id": "bybit",
        "instrument_key": "binance:spot:BTC/USDT",
        "quantity": "0.5",
        "buy_price": "100",
        "sell_price": "101",
        "gross_edge_quote": "0.5",
        "fees_quote": "0.1",
        "slippage_quote": "0.05",
        "other_costs_quote": "0",
        "net_edge_quote": "0.35",
        "created_at": created.isoformat(),
        "expires_at": (created + timedelta(seconds=1)).isoformat(),
        "note": "research only",
    }


def test_opportunity_log_persists_scans_and_deduplicates_alerts(tmp_path) -> None:
    notifications = NotificationService(state_path=tmp_path / "notifications.json")
    notifications.update_config({"enabled": True})
    path = tmp_path / "opportunities.json"
    service = OpportunityLogService(state_path=path, notifications=notifications)

    first = service.record(_payload())
    second = service.record(_payload())

    assert first["alerted"] is True
    assert second["alerted"] is False
    assert notifications.summary()["unread_count"] == 1
    assert service.summary()["validated_count"] == 2

    restored = OpportunityLogService(state_path=path, notifications=notifications)
    assert len(restored.items()) == 2
    assert restored.items()[0]["record_id"] == second["record_id"]


def test_opportunity_log_keeps_blocked_scans_without_alerts(tmp_path) -> None:
    notifications = NotificationService(state_path=tmp_path / "notifications.json")
    notifications.update_config({"enabled": True})
    service = OpportunityLogService(state_path=tmp_path / "opportunities.json", notifications=notifications)

    record = service.record(_payload("BLOCKED"))

    assert record["alerted"] is False
    assert service.items(state="BLOCKED")[0]["blocking_reason"] == "L2_SNAPSHOT_MISSING"
    assert notifications.summary()["unread_count"] == 0


def test_opportunity_log_rejects_unknown_state(tmp_path) -> None:
    service = OpportunityLogService(state_path=tmp_path / "opportunities.json")

    with pytest.raises(ValueError, match="state"):
        service.record(_payload("UNKNOWN"))
