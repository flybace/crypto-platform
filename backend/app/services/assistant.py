"""Allowlisted, read-only Action gateway for a future GACE AI adapter."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore
from backend.app.services.advice import AdviceError
from backend.app.services.history_coverage import build_history_coverage
from backend.app.services.market_summary import build_market_summary
from backend.app.services.runtime_plan import build_runtime_plan
from backend.app.services.system_status import build_system_status


ASSISTANT_ACTIONS: tuple[dict[str, object], ...] = (
    {"id": "runtime.plan.read", "path": "/api/v1/runtime/plan", "scope": "runtime:read", "description": "读取 24/7 运行计划和安全状态。"},
    {"id": "tradeplan.summary.read", "path": "/api/v1/tradeplan/summary", "scope": "runtime:read", "description": "读取交易计划投影。"},
    {"id": "system.readiness.read", "path": "/api/v1/system/readiness", "scope": "system:read", "description": "读取服务、数据和研究就绪度。"},
    {"id": "market.summary.read", "path": "/api/v1/market/summary", "scope": "market:read", "description": "读取已校验 K 线市场摘要。"},
    {"id": "market.opportunities.history.read", "path": "/api/v1/market/opportunities/history", "scope": "market:read", "description": "读取 L2 机会扫描记录，不包含交易授权。"},
    {"id": "news.summary.read", "path": "/api/v1/news/summary", "scope": "news:read", "description": "读取已接入新闻事件摘要。"},
    {"id": "news.events.read", "path": "/api/v1/news/events", "scope": "news:read", "description": "读取带来源的新闻事件。"},
    {"id": "news.resonance.read", "path": "/api/v1/news/resonance", "scope": "news:read", "description": "读取消息与历史行情的研究共振候选。"},
    {"id": "history.coverage.read", "path": "/api/v1/history/coverage", "scope": "history:read", "description": "读取历史数据覆盖率和质量状态。"},
    {"id": "advice.candidates.read", "path": "/api/v1/advice/candidates", "scope": "advice:read", "description": "读取研究候选和价差观察字段。"},
    {"id": "strategies.catalog.read", "path": "/api/v1/strategies/catalog", "scope": "strategy:read", "description": "读取策略目录和可用模式。"},
    {"id": "backtests.runs.read", "path": "/api/v1/backtests/runs", "scope": "backtest:read", "description": "读取回测归档。"},
    {"id": "paper.summary.read", "path": "/api/v1/paper/summary", "scope": "paper:read", "description": "读取独立模拟账户摘要。"},
    {"id": "risk.summary.read", "path": "/api/v1/risk/summary", "scope": "risk:read", "description": "读取风控门禁和阻断原因。"},
    {"id": "notifications.summary.read", "path": "/api/v1/notifications/summary", "scope": "notifications:read", "description": "读取站内通知摘要。"},
    {"id": "assistant.actions.read", "path": "/api/v1/assistant/actions", "scope": "assistant:read", "description": "读取 AI 可调用的只读 Action 清单。"},
)


class AssistantService:
    """Dispatch only explicit read actions and retain a small audit trail."""

    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._invocations: dict[str, dict[str, object]] = {}
        self._lock = RLock()
        self._load()

    def actions(self) -> list[dict[str, object]]:
        return [
            {
                **deepcopy(item),
                "risk_level": "READ_ONLY",
                "method": "GET",
                "enabled": True,
            }
            for item in ASSISTANT_ACTIONS
        ]

    def summary(self) -> dict[str, object]:
        with self._lock:
            self._load()
            records = sorted(self._invocations.values(), key=lambda item: str(item.get("created_at", "")), reverse=True)
            return {
                "module": "assistant",
                "status": "read_only_ready",
                "mode": "gace-declarative-read-only",
                "actions": len(ASSISTANT_ACTIONS),
                "invocations": len(records),
                "last_invocations": [deepcopy(item) for item in records[:10]],
                "write_actions": [],
                "blocked_actions": [
                    {"id": "execution.live_order.submit", "risk_level": "HIGH_RISK", "reason": "真实执行已关闭。"},
                    {"id": "execution.withdraw.submit", "risk_level": "HIGH_RISK", "reason": "系统永不提供提币。"},
                ],
            }

    def invocations(self, limit: int = 50) -> list[dict[str, object]]:
        with self._lock:
            self._load()
            records = sorted(self._invocations.values(), key=lambda item: str(item.get("created_at", "")), reverse=True)
            return [deepcopy(item) for item in records[: max(1, min(int(limit), 200))]]

    def invoke(self, request: dict[str, object], state: object) -> dict[str, object]:
        action = str(request.get("action", "")).strip()
        if action not in {str(item["id"]) for item in ASSISTANT_ACTIONS}:
            raise KeyError(action)
        if str(request.get("risk_level", "")) != "READ_ONLY":
            raise ValueError("only READ_ONLY actions are enabled")
        for key in ("request_id", "actor", "authorization_id", "idempotency_key"):
            if not str(request.get(key, "")).strip():
                raise ValueError(f"{key} must not be empty")
        expires_at = request.get("expires_at")
        if not isinstance(expires_at, datetime) or expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("expires_at must contain a timezone")
        now = datetime.now(UTC)
        if expires_at <= now:
            raise ValueError("action request has expired")
        key = str(request["idempotency_key"])
        with self._lock:
            self._load()
            previous = self._invocations.get(key)
            if previous is not None:
                if previous.get("action") != action:
                    raise ValueError("idempotency key is already bound to another action")
                return {**deepcopy(previous), "replayed": True}
        result = self._dispatch(action, request.get("payload"), state)
        record = {
            "request_id": str(request["request_id"]),
            "action": action,
            "actor": str(request["actor"]),
            "risk_level": "READ_ONLY",
            "idempotency_key": key,
            "status": "completed",
            "replayed": False,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "result": result,
            "audit": {"payload_keys": sorted((request.get("payload") or {}).keys()) if isinstance(request.get("payload"), dict) else []},
        }
        with self._lock:
            self._invocations[key] = deepcopy(record)
            self._invocations = dict(list(self._invocations.items())[-200:])
            self._persist_locked()
        return deepcopy(record)

    @staticmethod
    def _dispatch(action: str, payload: object, state: object) -> object:
        values = payload if isinstance(payload, dict) else {}
        interval = str(values.get("interval", "1h")).strip().lower()
        if interval not in {"1d", "1h", "5m"}:
            raise ValueError("interval must be 1d, 1h, or 5m")
        if action == "runtime.plan.read" or action == "tradeplan.summary.read":
            return build_runtime_plan(state)
        if action == "system.readiness.read":
            return build_system_status(state)["readiness"]
        if action == "market.summary.read":
            return build_market_summary(state.history_storage, interval=interval)
        if action == "market.opportunities.history.read":
            limit = int(values.get("limit", 50) or 50)
            return state.opportunity_log.summary(limit=max(1, min(limit, 200)))
        if action == "news.summary.read":
            return state.news_service.summary()
        if action == "news.events.read":
            limit = int(values.get("limit", 50) or 50)
            return {"items": state.news_service.events(limit=max(1, min(limit, 200)))}
        if action == "news.resonance.read":
            limit = int(values.get("limit", 100) or 100)
            return state.news_service.resonance(interval=interval, limit=max(1, min(limit, 200)))
        if action == "history.coverage.read":
            return build_history_coverage(state.history_storage, state.history_jobs.list(200))
        if action == "advice.candidates.read":
            limit = int(values.get("limit", 50) or 50)
            return state.advice_service.candidates(interval=interval, limit=max(1, min(limit, 200)), compact=True)
        if action == "strategies.catalog.read":
            return {"items": state.strategy_registry.catalog(state.settings.execution_mode)}
        if action == "backtests.runs.read":
            limit = int(values.get("limit", 30) or 30)
            return {"items": state.backtest_runs.list(max(1, min(limit, 100)))}
        if action == "paper.summary.read":
            return state.paper_trading.summary()
        if action == "risk.summary.read":
            return state.risk_policy.summary(state.settings.execution_mode)
        if action == "notifications.summary.read":
            return state.notifications.summary()
        if action == "assistant.actions.read":
            return {"items": AssistantService().actions()}
        raise KeyError(action)

    def _load(self) -> None:
        if self._state is None:
            return
        self._invocations.clear()
        try:
            payload = self._state.load({"version": 1, "invocations": []})
        except JsonStateError as error:
            raise RuntimeError("assistant state is unreadable") from error
        records = payload.get("invocations", []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            raise RuntimeError("assistant state must contain an array")
        for record in records:
            if isinstance(record, dict) and str(record.get("idempotency_key", "")).strip():
                self._invocations[str(record["idempotency_key"])] = deepcopy(record)

    def _persist_locked(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save({"version": 1, "invocations": list(self._invocations.values())[-200:]})
        except JsonStateError as error:
            raise RuntimeError("assistant state cannot be saved") from error
