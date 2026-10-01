"""Truthful readiness projection for the standalone crypto deployment."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.app.services.history_coverage import build_history_coverage
from backend.app.services.market_summary import build_market_summary
from backend.app.services.redis_runtime import dependency_ready
from backend.app.services.task_dispatch_consistency import TaskDispatchConsistency


def build_system_status(state: Any) -> dict[str, object]:
    coverage = build_history_coverage(state.history_storage, state.history_jobs.list(200))
    strategy = state.strategy_registry.summary()
    packages = state.strategy_packages.summary()
    matrices = state.strategy_matrices.summary()
    paper = state.paper_trading.summary()
    risk = state.risk_policy.summary(state.settings.execution_mode)
    persistence = state.task_store.status()
    domain_snapshots = state.task_store.domain_snapshot_status()
    redis = state.redis_runtime.status()
    queue = (
        state.task_queue.status()
        if state.task_queue is not None
        else {
            "enabled": False,
            "ok": str(state.settings.task_queue_mode) == "local",
            "status": "local",
            "message": "任务在 API 进程内执行",
        }
    )
    queue_ready = str(state.settings.task_queue_mode) == "local" or bool(queue.get("ok"))
    consistency = _dispatch_consistency_status(state)
    consistency_ready = bool(consistency.get("ok"))
    infrastructure_ready = dependency_ready(persistence) and dependency_ready(domain_snapshots) and dependency_ready(redis) and consistency_ready and (
        not bool(state.settings.persistence_required) or queue_ready
    )
    verified = int(coverage.get("verified_dataset_count", 0) or 0)
    expected = int(coverage.get("expected_dataset_count", 0) or 0)
    data_status = "blocked" if verified == 0 else "ready" if verified == expected else "partial"
    strategy_status = "ready" if int(strategy.get("enabled_count", 0) or 0) else "blocked"
    research_ready = verified > 0 and strategy_status == "ready"
    try:
        market = build_market_summary(state.history_storage, interval="1h")
        market_status = "ready" if market.get("quotes") else "waiting"
    except Exception:
        market = {"interval": "1h", "quotes": [], "spreads": [], "research_only": True}
        market_status = "blocked"
    services = [
        _service("API", "online", True, "认证 API 已启动"),
        _service("历史数据", data_status, data_status != "blocked", f"{verified}/{expected} 个数据集通过质量门禁"),
        _service("策略目录", strategy_status, strategy_status == "ready", f"{strategy.get('enabled_count', 0)} 个策略已启用；{packages.get('package_count', 0)} 个策略包"),
        _service("行情摘要", market_status, market_status == "ready", f"{len(market.get('quotes', []))} 条已校验报价"),
        _service("模拟盘", "ready", True, "独立模拟账户可用"),
        _service("真实执行", "blocked", False, "execution_mode=DISABLED"),
        _service("GACE 适配", "partial", True, "只读能力目录已提供"),
        _service("通知", str(state.notifications.summary()["status"]), True, "仅站内通知，外部通道未接入"),
        _service(
            "任务账本",
            str(persistence.get("status", "unknown")),
            bool(persistence.get("ok")) or not bool(persistence.get("required")),
            str(persistence.get("message", "")),
        ),
        _service(
            "领域快照",
            str(domain_snapshots.get("status", "unknown")),
            bool(domain_snapshots.get("ok")) or not bool(domain_snapshots.get("required")),
            f"PostgreSQL 共享快照 {domain_snapshots.get('snapshot_count', 0)} 条",
        ),
        _service(
            "Redis 协调",
            str(redis.get("status", "unknown")),
            bool(redis.get("ok")) or not bool(redis.get("required")),
            str(redis.get("message", "")),
        ),
        _service(
            "任务队列",
            str(queue.get("status", "unknown")),
            queue_ready,
            str(queue.get("message", "")),
        ),
        _service(
            "任务双写一致性",
            str(consistency.get("status", "unknown")),
            consistency_ready,
            _consistency_message(consistency),
        ),
    ]
    checks = [
        {"key": "history", "label": "历史数据质量", "passed": verified > 0, "detail": f"{verified}/{expected} verified"},
        {"key": "strategy", "label": "策略可运行", "passed": strategy_status == "ready", "detail": f"{strategy.get('enabled_count', 0)} enabled"},
        {"key": "research", "label": "研究链路", "passed": research_ready, "detail": "可进入回测和模拟盘" if research_ready else "等待数据或策略"},
        {"key": "paper", "label": "模拟交易隔离", "passed": True, "detail": "PAPER only"},
        {"key": "live", "label": "真实交易", "passed": False, "detail": "服务端安全阻断"},
        {
            "key": "infrastructure",
            "label": "任务基础设施",
            "passed": infrastructure_ready,
            "detail": "任务账本、领域快照与协调服务可用" if infrastructure_ready else "必需的任务基础设施不可用",
        },
        {
            "key": "task_dispatch_consistency",
            "label": "任务双写一致性",
            "passed": consistency_ready,
            "detail": _consistency_message(consistency),
        },
    ]
    if data_status == "blocked":
        overall = "blocked"
    elif not infrastructure_ready:
        overall = "blocked"
    elif data_status == "partial" or not research_ready:
        overall = "partial"
    else:
        overall = "safe_paused" if not risk.get("real_orders_allowed") else "ready"
    return {
        "module": "system",
        "status": overall,
        "checked_at": datetime.now(UTC).isoformat(),
        "runtime": {
            "mode": "standalone",
            "market_mode": "24/7 spot research",
            "version": str(state.settings.version),
            "execution_mode": str(state.settings.execution_mode),
        },
        "infrastructure": {
            "ready": infrastructure_ready,
            "task_store": persistence,
            "domain_snapshots": domain_snapshots,
            "redis": redis,
            "queue": queue,
            "dispatch_consistency": consistency,
        },
        "services": services,
        "data": {
            "status": data_status,
            "coverage": coverage,
            "market": market,
        },
        "strategy": {**strategy, "packages": packages, "matrices": matrices},
        "paper": paper,
        "risk": risk,
        "readiness": {
            "overall": {"status": overall, "research_ready": research_ready, "real_execution_allowed": False},
            "checks": checks,
            "next_actions": _next_actions(data_status, strategy_status),
        },
    }


def _service(name: str, status: str, ok: bool, message: str) -> dict[str, object]:
    return {"name": name, "status": status, "ok": ok, "message": message}


def _next_actions(data_status: str, strategy_status: str) -> list[str]:
    actions: list[str] = []
    if data_status != "ready":
        actions.append("补齐缺失历史数据并通过 Manifest、连续性和重复检查")
    if strategy_status != "ready":
        actions.append("至少启用一个策略后再运行回测")
    actions.append("保持真实执行关闭，先完成回测、模拟盘和风险演练")
    return actions


def _dispatch_consistency_status(state: Any) -> dict[str, object]:
    queue = getattr(state, "task_queue", None)
    if queue is None:
        return {
            "contract_version": "task-dispatch-consistency-v1",
            "enabled": False,
            "ok": str(getattr(state.settings, "task_queue_mode", "local")) == "local",
            "status": "local",
            "checked": 0,
            "counts": {},
            "repair": {"requested": False, "attempted": 0, "repaired": 0, "skipped": 0, "failed": 0},
            "items": [],
        }
    try:
        auditor = getattr(state, "task_dispatch_consistency", None)
        if not isinstance(auditor, TaskDispatchConsistency):
            auditor = TaskDispatchConsistency(state.task_store, queue)
        return auditor.audit(limit=50, repair=False)
    except Exception as error:
        return {
            "contract_version": "task-dispatch-consistency-v1",
            "enabled": True,
            "ok": False,
            "status": "blocked",
            "checked": 0,
            "counts": {"UNKNOWN": 1},
            "repair": {"requested": False, "attempted": 0, "repaired": 0, "skipped": 0, "failed": 0},
            "error_kind": str(error.__class__.__name__).upper()[:80],
            "items": [],
        }


def _consistency_message(consistency: dict[str, object]) -> str:
    if not bool(consistency.get("enabled")):
        return "本地模式不使用 Redis/SQL 双写"
    counts = consistency.get("counts")
    if not isinstance(counts, dict):
        return "一致性状态不可用"
    checked = int(consistency.get("checked", 0) or 0)
    anomalies = sum(
        int(counts.get(state, 0) or 0)
        for state in ("PENDING", "MISSING", "DUPLICATE", "MISMATCHED", "UNKNOWN", "DLQ")
    )
    receipt_counts = consistency.get("receipt_counts")
    receipt_anomalies = 0
    if isinstance(receipt_counts, dict):
        receipt_anomalies = sum(
            int(receipt_counts.get(state, 0) or 0)
            for state in ("UNVERIFIED", "MISMATCHED")
        )
    return f"已检查 {checked} 个活动任务；投递异常 {anomalies + receipt_anomalies} 个"
