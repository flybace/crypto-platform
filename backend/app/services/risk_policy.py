"""Fail-closed risk policy registry for the standalone control plane."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore


DEFAULT_POLICY: dict[str, object] = {
    "enabled": False,
    "mode": "DISABLED",
    "venue_allowlist": [],
    "instrument_allowlist": [],
    "quote_asset_allowlist": ["USDT"],
    "max_order_notional_quote": "0",
    "max_daily_sell_notional_quote": "0",
    "max_daily_sell_ratio": "0",
    "min_base_reserve": None,
    "allow_market_order": False,
    "max_slippage_bps": "0",
    "max_market_age_seconds": 2,
    "max_open_orders": 1,
    "max_orders_per_minute": 1,
    "emergency_stop": False,
}


class RiskPolicyService:
    def __init__(
        self,
        *,
        state_path: str | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self._policy = deepcopy(DEFAULT_POLICY)
        self._events: list[dict[str, object]] = []
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._load()

    def policy(self) -> dict[str, object]:
        self._load()
        return deepcopy(self._policy)

    def update(self, values: dict[str, object]) -> dict[str, object]:
        self._load()
        next_policy = deepcopy(self._policy)
        next_policy.update(values)
        self._validate(next_policy)
        self._policy = next_policy
        self._record("POLICY_UPDATED", {"mode": next_policy["mode"], "enabled": next_policy["enabled"]})
        self._persist()
        return self.policy()

    def emergency_stop(self) -> dict[str, object]:
        self._load()
        self._policy["emergency_stop"] = True
        self._record("EMERGENCY_STOP", {"reason": "operator_requested"})
        self._persist()
        return self.summary("DISABLED")

    def release_emergency_stop(self, confirmation: bool) -> dict[str, object]:
        if not confirmation:
            raise ValueError("confirmation is required to release emergency stop")
        self._load()
        self._policy["emergency_stop"] = False
        self._record("EMERGENCY_RELEASE", {"reason": "operator_confirmed"})
        self._persist()
        return self.summary("DISABLED")

    def summary(self, execution_mode: str) -> dict[str, object]:
        self._load()
        reasons = []
        if execution_mode == "DISABLED":
            reasons.append("EXECUTION_MODE_DISABLED")
        if self._policy["emergency_stop"]:
            reasons.append("EMERGENCY_STOP_ACTIVE")
        if not self._policy["enabled"]:
            reasons.append("POLICY_DISABLED")
        if self._policy["mode"] == "DISABLED":
            reasons.append("POLICY_MODE_DISABLED")
        if not self._policy["venue_allowlist"]:
            reasons.append("VENUE_ALLOWLIST_EMPTY")
        if not self._policy["instrument_allowlist"]:
            reasons.append("INSTRUMENT_ALLOWLIST_EMPTY")
        if str(self._policy["max_order_notional_quote"]) in {"0", "0.0", "0.00"}:
            reasons.append("ORDER_LIMIT_UNCONFIGURED")
        if str(self._policy["max_daily_sell_notional_quote"]) in {"0", "0.0", "0.00"}:
            reasons.append("DAILY_LIMIT_UNCONFIGURED")
        if str(self._policy["max_slippage_bps"]) in {"0", "0.0", "0.00"}:
            reasons.append("SLIPPAGE_LIMIT_UNCONFIGURED")
        return {
            "execution_mode": execution_mode,
            "real_orders_allowed": False,
            "state": "SAFE_PAUSED" if reasons else "CANDIDATE_CONFIGURED",
            "policy": self.policy(),
            "blocking_reasons": reasons,
            "gates": [
                {"key": "execution", "label": "真实执行", "passed": execution_mode != "DISABLED", "detail": "服务端 DISABLED" if execution_mode == "DISABLED" else "运行模式允许"},
                {"key": "authorization", "label": "人工授权", "passed": False, "detail": "未接入真实授权"},
                {"key": "limits", "label": "额度完整", "passed": not any(reason.endswith("UNCONFIGURED") for reason in reasons), "detail": "服务端限额"},
                {"key": "emergency", "label": "安全暂停", "passed": not bool(self._policy["emergency_stop"]), "detail": "紧急停止" if self._policy["emergency_stop"] else "未触发"},
            ],
            "next_action": "仅可继续回测和模拟盘" if reasons else "仍需真实账户和独立验收",
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def events(self, limit: int = 50) -> list[dict[str, object]]:
        self._load()
        return [deepcopy(item) for item in self._events[-max(1, min(int(limit), 200)) :][::-1]]

    @staticmethod
    def _validate(policy: dict[str, object]) -> None:
        if policy.get("mode") not in {"DISABLED", "SELL_ONLY", "BUY_SELL"}:
            raise ValueError("risk mode must be DISABLED, SELL_ONLY, or BUY_SELL")
        if not isinstance(policy.get("enabled"), bool):
            raise ValueError("enabled must be boolean")
        for key in ("max_order_notional_quote", "max_daily_sell_notional_quote", "max_daily_sell_ratio", "max_slippage_bps"):
            try:
                value = float(str(policy.get(key, "0")))
            except ValueError as error:
                raise ValueError(f"{key} must be numeric") from error
            if value < 0:
                raise ValueError(f"{key} must not be negative")
        if float(str(policy.get("max_daily_sell_ratio", "0"))) > 1:
            raise ValueError("max_daily_sell_ratio must be at most 1")
        if not isinstance(policy.get("venue_allowlist"), list) or not isinstance(policy.get("instrument_allowlist"), list):
            raise ValueError("allowlists must be arrays")
        if not isinstance(policy.get("quote_asset_allowlist"), list):
            raise ValueError("quote_asset_allowlist must be an array")

    def _record(self, event_type: str, payload: dict[str, object]) -> None:
        self._events.append({"event_id": f"risk-{len(self._events) + 1:06d}", "event_type": event_type, "payload": payload, "created_at": datetime.now(UTC).isoformat()})

    def _load(self) -> None:
        if self._state is None:
            return
        self._events = []
        try:
            payload = self._state.load({"version": 1, "policy": DEFAULT_POLICY, "events": []})
        except JsonStateError as error:
            raise RuntimeError("risk policy state is unreadable") from error
        if not isinstance(payload, dict):
            raise RuntimeError("risk policy state must be an object")
        policy = payload.get("policy", DEFAULT_POLICY)
        events = payload.get("events", [])
        if not isinstance(policy, dict) or not isinstance(events, list):
            raise RuntimeError("risk policy state has invalid fields")
        next_policy = deepcopy(DEFAULT_POLICY)
        next_policy.update(policy)
        self._validate(next_policy)
        self._policy = next_policy
        self._events = [deepcopy(item) for item in events if isinstance(item, dict)]

    def _persist(self) -> None:
        if self._state is not None:
            try:
                self._state.save({"version": 1, "policy": self._policy, "events": self._events[-200:]})
            except JsonStateError as error:
                raise RuntimeError("risk policy state cannot be saved") from error
