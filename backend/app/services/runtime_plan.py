"""Build the unified 24/7 runtime plan from current service state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .capability_catalog import build_capability_catalog
from .history_coverage import build_history_coverage


ACTIVE_STATUSES = frozenset({"queued", "running", "cancelling", "open", "accepted", "partially_filled"})
FAILED_STATUSES = frozenset({"failed", "blocked", "rejected", "interrupted"})

STEP_DEFINITIONS: tuple[dict[str, str], ...] = (
    {
        "id": "history",
        "title": "历史数据就绪",
        "window": "24/7 数据链",
        "owner": "history",
        "action": "只使用 Manifest、SHA-256 和连续性检查通过的数据驱动研究。",
        "implementation": "verified",
    },
    {
        "id": "strategies",
        "title": "策略配置",
        "window": "持续可用",
        "owner": "strategy",
        "action": "策略开关、参数和运行模式从服务端目录读取。",
        "implementation": "verified",
    },
    {
        "id": "research",
        "title": "研究与回测",
        "window": "按需运行",
        "owner": "research",
        "action": "使用固定数据和参数归档运行比较、筛选、调参与组合回测。",
        "implementation": "partial",
    },
    {
        "id": "paper",
        "title": "模拟盘回放",
        "window": "按需运行",
        "owner": "paper",
        "action": "模拟账户和模拟订单保持独立，自动回放必须显式开启。",
        "implementation": "partial",
    },
    {
        "id": "risk",
        "title": "风险门禁",
        "window": "每次动作前",
        "owner": "risk",
        "action": "真实执行默认安全暂停，限额、白名单和紧急停止由服务端判断。",
        "implementation": "verified",
    },
    {
        "id": "live",
        "title": "真实现货执行",
        "window": "后置门禁",
        "owner": "execution",
        "action": "真实账户、真实订单和跨市场两腿执行尚未开放。",
        "implementation": "blocked",
    },
    {
        "id": "gace",
        "title": "GACE 只读适配",
        "window": "候选合同",
        "owner": "gace",
        "action": "通过声明式 capability catalog 为未来 App 和 AI 调用保留只读入口。",
        "implementation": "partial",
    },
)


def build_runtime_plan(state: Any) -> dict[str, object]:
    """Aggregate current state without creating a scheduler or trade side effect."""
    coverage = build_history_coverage(state.history_storage, state.history_jobs.list(200))
    strategy = state.strategy_registry.summary()
    packages = state.strategy_packages.summary()
    matrices = state.strategy_matrices.summary()
    backtests = state.backtest_runs.summary()
    research_records = state.research_runs.list(200)
    paper_summary = state.paper_trading.summary()
    paper_orders = state.paper_trading.orders(200)
    paper_runs = state.paper_trading.strategy_runs(200)
    automation = state.paper_automation.get()
    risk = state.risk_policy.summary(state.settings.execution_mode)
    tasks = _task_items(state, 200)
    capabilities = build_capability_catalog(state.settings)

    verified = int(coverage.get("verified_dataset_count", 0) or 0)
    expected = int(coverage.get("expected_dataset_count", 0) or 0)
    enabled_strategies = int(strategy.get("enabled_count", 0) or 0)
    completed_backtests = int(backtests.get("completed_count", 0) or 0)
    completed_research = sum(1 for item in research_records if item.get("status") == "completed")
    data_status = "blocked" if verified == 0 else "ready" if verified == expected else "partial"
    strategy_status = "ready" if enabled_strategies else "blocked"
    research_status = "blocked" if data_status == "blocked" else "ready" if completed_backtests or completed_research else "waiting"
    paper_status = "running" if automation.get("enabled") and automation.get("last_run_id") else "ready" if paper_orders or paper_runs else "waiting"
    risk_status = "ready" if not risk.get("real_orders_allowed") else "blocked"
    live_status = "blocked" if str(state.settings.execution_mode).upper() == "DISABLED" else "waiting"
    gace_status = "partial"

    steps = [
        _step(STEP_DEFINITIONS[0], data_status, {
            "verified_datasets": verified,
            "expected_datasets": expected,
            "row_count": int(coverage.get("row_count", 0) or 0),
            "quality_blocked": int(coverage.get("blocked_dataset_count", 0) or 0),
        }),
        _step(STEP_DEFINITIONS[1], strategy_status, {
            "enabled": enabled_strategies,
            "total": int(strategy.get("strategy_count", 0) or 0),
            "backtest_enabled": int(strategy.get("backtest_enabled_count", 0) or 0),
            "paper_enabled": int(strategy.get("paper_enabled_count", 0) or 0),
            "strategy_packages": int(packages.get("package_count", 0) or 0),
            "strategy_matrices": int(matrices.get("matrix_count", 0) or 0),
        }),
        _step(STEP_DEFINITIONS[2], research_status, {
            "backtest_runs": int(backtests.get("run_count", 0) or 0),
            "completed_backtests": completed_backtests,
            "research_runs": len(research_records),
            "completed_research": completed_research,
        }),
        _step(STEP_DEFINITIONS[3], paper_status, {
            "orders": len(paper_orders),
            "strategy_runs": len(paper_runs),
            "automation_enabled": bool(automation.get("enabled")),
            "last_run_id": automation.get("last_run_id"),
        }),
        _step(STEP_DEFINITIONS[4], risk_status, {
            "state": risk.get("state"),
            "blocking_reasons": len(risk.get("blocking_reasons", [])),
            "real_orders_allowed": bool(risk.get("real_orders_allowed")),
        }),
        _step(STEP_DEFINITIONS[5], live_status, {
            "execution_mode": state.settings.execution_mode,
            "real_orders_allowed": bool(risk.get("real_orders_allowed")),
        }),
        _step(STEP_DEFINITIONS[6], gace_status, {
            "read_only_capabilities": len(capabilities.get("capabilities", [])),
            "write_capabilities": len(capabilities.get("write_capabilities", [])),
        }),
    ]

    phase, phase_label, activity = _phase(
        data_status=data_status,
        strategy_status=strategy_status,
        automation=automation,
        risk=risk,
    )
    current_step = next(
        (item for item in steps if item["status"] in {"partial", "running", "blocked"} and item["id"] not in {"live", "gace"}),
        None,
    )
    next_step = next((item for item in steps if item["status"] == "waiting"), None)
    task_summary = {
        "total": len(tasks),
        "active": sum(1 for item in tasks if item["status"] in ACTIVE_STATUSES),
        "failed": sum(1 for item in tasks if item["status"] in FAILED_STATUSES),
        "completed": sum(1 for item in tasks if item["status"] not in ACTIVE_STATUSES and item["status"] not in FAILED_STATUSES),
    }

    return {
        "module": "runtime_plan",
        "status": "safe_paused" if not risk.get("real_orders_allowed") else "controlled",
        "market_mode": "24/7 spot research",
        "phase": phase,
        "phase_label": phase_label,
        "now": datetime.now(UTC).isoformat(),
        "execution_mode": str(state.settings.execution_mode),
        "activity": activity,
        "steps": steps,
        "current_step": current_step,
        "next_step": next_step,
        "summary": {
            "verified_dataset_count": verified,
            "expected_dataset_count": expected,
            "row_count": int(coverage.get("row_count", 0) or 0),
            "coverage_ratio_pct": coverage.get("coverage_ratio_pct", "0.00"),
            "enabled_strategy_count": enabled_strategies,
            "strategy_package_count": int(packages.get("package_count", 0) or 0),
            "strategy_matrix_count": int(matrices.get("matrix_count", 0) or 0),
            "backtest_count": int(backtests.get("run_count", 0) or 0),
            "research_count": len(research_records),
            "paper_order_count": len(paper_orders),
            "paper_strategy_run_count": len(paper_runs),
        },
        "checkpoints": {
            "latest_backtest": _compact_run(backtests.get("latest_run")),
            "latest_research": _compact_run(research_records[0] if research_records else None),
            "paper_last_run_id": automation.get("last_run_id"),
            "paper_automation_enabled": bool(automation.get("enabled")),
            "risk_state": risk.get("state"),
            "risk_blocking_reasons": list(risk.get("blocking_reasons", [])),
        },
        "risk": risk,
        "paper": {
            "equity_quote": paper_summary.get("equity_quote", "0"),
            "order_count": paper_summary.get("order_count", 0),
            "strategy_run_count": paper_summary.get("strategy_run_count", 0),
            "execution_mode": paper_summary.get("execution_mode", "PAPER"),
            "real_execution_mode": paper_summary.get("real_execution_mode", "DISABLED"),
        },
        "tasks": task_summary,
        "recent_tasks": tasks[:12],
        "capabilities": {
            "read_only": bool(capabilities.get("read_only")),
            "read_only_count": len(capabilities.get("capabilities", [])),
            "write_count": len(capabilities.get("write_capabilities", [])),
            "blocked_action_count": len(capabilities.get("blocked_actions", [])),
            "catalog_path": "/api/v1/gace/capabilities",
        },
        "strategy_orchestration": {
            "packages": packages,
            "matrices": matrices,
            "custom_execution_enabled": False,
            "research_only": True,
        },
    }


def _step(definition: dict[str, str], status: str, evidence: dict[str, object]) -> dict[str, object]:
    return {**definition, "status": status, "evidence": evidence}


def _phase(*, data_status: str, strategy_status: str, automation: dict[str, Any], risk: dict[str, object]) -> tuple[str, str, dict[str, str]]:
    if data_status == "blocked":
        return "DATA_BLOCKED", "历史数据阻断", {
            "title": "等待已校验历史数据",
            "detail": "没有可用数据集时，研究、回测和模拟盘均不会启动。",
            "tone": "blocked",
        }
    if strategy_status == "blocked":
        return "STRATEGY_BLOCKED", "策略配置阻断", {
            "title": "等待启用策略",
            "detail": "至少启用一个策略后，才能进入回测或模拟盘链路。",
            "tone": "blocked",
        }
    if automation.get("enabled"):
        return "PAPER_CONFIGURED", "模拟盘已配置", {
            "title": "模拟盘回放已配置",
            "detail": "当前仍是按需回放，自动配置不会触发真实账户或真实订单。",
            "tone": "running",
        }
    return "RESEARCH_READY", "研究链路就绪", {
        "title": "研究与模拟盘可继续",
        "detail": "数据和策略基础已经具备，真实执行仍由服务端风险门禁阻断。",
        "tone": "ready" if not risk.get("real_orders_allowed") else "blocked",
    }


def _compact_run(item: object) -> dict[str, object] | None:
    if not isinstance(item, dict):
        return None
    keys = ("run_id", "kind", "strategy_id", "status", "created_at", "total_return_pct", "candidate_count", "trial_count")
    return {key: item[key] for key in keys if key in item}


def _task_items(state: Any, limit: int) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []

    for job in state.history_jobs.list(limit):
        records.append({
            "task_id": f"history:{job['job_id']}",
            "kind": "history_download",
            "status": str(job["status"]),
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "label": "历史数据下载",
            "progress": {
                "completed": job["completed"],
                "total": job["total"],
                "blocked": job["blocked"],
                "failed": job["failed"],
                "interrupted": job.get("interrupted", 0),
                "cancelled": job.get("cancelled", 0),
            },
        })
    for run in state.screening_service.list(limit):
        records.append({
            "task_id": f"screening:{run['run_id']}",
            "kind": "screening",
            "status": str(run["status"]),
            "created_at": run["created_at"],
            "updated_at": run["created_at"],
            "label": "历史指标筛选",
            "progress": {"completed": run["dataset_count"], "total": run["dataset_count"], "candidates": run["candidate_count"]},
        })
    for run in state.backtest_runs.list(limit):
        records.append({
            "task_id": f"backtest:{run['run_id']}",
            "kind": "backtest",
            "status": str(run["status"]),
            "created_at": run["created_at"],
            "updated_at": run["updated_at"],
            "label": "策略回测",
            "progress": {"completed": 1, "total": 1},
        })
    for run in state.research_runs.list(limit):
        records.append({
            "task_id": f"research:{run['run_id']}",
            "kind": str(run.get("kind", "research")),
            "status": str(run.get("status", "completed")),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "label": "研究任务",
            "progress": {"completed": 1, "total": 1, "dataset_count": run.get("dataset_count", 0)},
        })
    for order in state.paper_trading.orders(limit):
        records.append({
            "task_id": f"paper:{order['order_id']}",
            "kind": "paper_order",
            "status": str(order["status"]).lower(),
            "created_at": order["created_at"],
            "updated_at": order["created_at"],
            "label": "模拟盘订单",
            "progress": {"filled_quantity": order["filled_quantity"], "quantity": order["quantity"]},
        })
    for run in state.paper_trading.strategy_runs(limit):
        records.append({
            "task_id": f"paper-strategy:{run['run_id']}",
            "kind": "paper_strategy",
            "status": str(run.get("status", "completed")),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "label": "模拟策略回放",
            "progress": {"completed": 1, "total": 1, "orders": run.get("orders", 0)},
        })
    for matrix in state.strategy_matrices.list(limit):
        last_run = matrix.get("last_run") if isinstance(matrix.get("last_run"), dict) else None
        records.append({
            "task_id": f"strategy-matrix:{matrix['matrix_id']}",
            "kind": "strategy_matrix",
            "status": str(matrix.get("status", "draft")),
            "created_at": matrix.get("created_at"),
            "updated_at": matrix.get("updated_at"),
            "label": "策略矩阵研究",
            "progress": {
                "completed": last_run.get("completed_count", 0) if last_run else 0,
                "total": last_run.get("item_count", 0) if last_run else 0,
                "strategies": len(matrix.get("strategy_ids", [])),
                "datasets": len(matrix.get("dataset_ids", [])),
            },
        })

    records.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    return records[: max(1, min(int(limit), 200))]
