"""Durable strategy-candidate pools fed by verified screening runs."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock
from typing import Any

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore
from application.screening import ScreeningService


STAGE_THRESHOLDS = {"quick_validate": 3, "retest": 6}


class StrategyIncubatorError(ValueError):
    """A screening result cannot be added to a strategy incubator."""


class StrategyIncubatorService:
    """Keep screening candidates separate from executable coin pools.

    A candidate is keyed by ``dataset_id``. This preserves venue and interval
    identity when the same symbol appears on several markets and makes the
    hand-off to the existing research APIs auditable.
    """

    def __init__(
        self,
        screening: ScreeningService,
        *,
        state_path: str | Path | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self._screening = screening
        self._pools: dict[str, dict[str, object]] = {}
        self._lock = RLock()
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._load()

    def list(self, limit: int = 30) -> list[dict[str, object]]:
        with self._lock:
            self._load()
            records = sorted(
                self._pools.values(),
                key=lambda item: str(item.get("updated_at", "")),
                reverse=True,
            )
            return [self._decorate(item) for item in records[: max(1, min(int(limit), 100))]]

    def get(self, pool_id: str) -> dict[str, object] | None:
        with self._lock:
            self._load()
            pool = self._pools.get(str(pool_id).strip())
            return None if pool is None else self._decorate(pool)

    def snapshot(self, *, limit: int = 30, screen_limit: int = 20) -> dict[str, object]:
        pools = self.list(limit)
        recent_screens = self._recent_screens(screen_limit, pools)
        candidate_count = sum(int(pool["candidate_count"]) for pool in pools)
        ready_count = sum(1 for pool in pools if pool["stage"]["key"] == "ready_for_retest")
        return {
            "items": pools,
            "count": len(pools),
            "recent_screens": recent_screens,
            "summary": {
                "incubator_total": len(pools),
                "incubator_items": candidate_count,
                "ready_for_retest": ready_count,
                "collecting": len(pools) - ready_count,
                "stage_thresholds": dict(STAGE_THRESHOLDS),
            },
        }

    def add_from_screening(
        self,
        run_id: str,
        *,
        pool_id: str | None = None,
        name: str = "",
        description: str = "",
        mode: str = "append",
        limit: int = 120,
        min_score: Decimal | None = None,
    ) -> dict[str, object]:
        normalized_mode = str(mode).strip().lower()
        if normalized_mode not in {"append", "replace"}:
            raise StrategyIncubatorError("mode must be append or replace")
        if limit < 1 or limit > 500:
            raise StrategyIncubatorError("limit must be between 1 and 500")
        if min_score is not None and not min_score.is_finite():
            raise StrategyIncubatorError("min_score must be finite")

        screening_run = self._screening.get(run_id)
        if screening_run is None:
            raise KeyError(run_id)
        candidates = self._candidates(screening_run, limit=limit, min_score=min_score)
        if not candidates:
            raise StrategyIncubatorError("screening run has no passed candidates")

        source_pool_id = str(screening_run.get("pool_id", "")).strip()
        target_id = str(pool_id or self._default_pool_id(source_pool_id)).strip()
        if not target_id or len(target_id) > 120:
            raise StrategyIncubatorError("pool_id must be between 1 and 120 characters")
        now = datetime.now(UTC).isoformat()

        with self._lock:
            # API and Worker instances share this snapshot through SQL. Reload
            # immediately before merging so a long-lived process cannot write
            # an older in-memory pool over a newer process update.
            self._load()
            pool = self._pools.get(target_id)
            if pool is None:
                pool = {
                    "pool_id": target_id,
                    "name": str(name).strip() or f"{source_pool_id or '指标筛选'} 策略孵化池",
                    "description": str(description).strip()
                    or "由已校验历史筛选结果沉淀的研究候选，不直接产生交易指令。",
                    "kind": "strategy_incubator",
                    "source_type": "screening",
                    "source_pool_id": source_pool_id,
                    "source_ref": f"screening:{run_id}",
                    "strategy_id": str(screening_run.get("strategy_id") or "indicator_screening"),
                    "created_at": now,
                    "updated_at": now,
                    "latest_screen_run_id": str(run_id),
                    "source_screen_run_ids": [],
                    "items": [],
                }
                self._pools[target_id] = pool
            else:
                if name.strip():
                    pool["name"] = name.strip()
                if description.strip():
                    pool["description"] = description.strip()
                pool["source_pool_id"] = pool.get("source_pool_id") or source_pool_id
                pool["strategy_id"] = pool.get("strategy_id") or str(
                    screening_run.get("strategy_id") or "indicator_screening"
                )

            old_items = {
                str(item.get("candidate_id")): item
                for item in pool.get("items", [])
                if isinstance(item, dict) and str(item.get("candidate_id", "")).strip()
            }
            next_items = {} if normalized_mode == "replace" else dict(old_items)
            added = updated = 0
            for candidate in candidates:
                key = str(candidate["candidate_id"])
                previous = next_items.get(key)
                if previous is None:
                    next_items[key] = {**candidate, "first_seen_at": now}
                    added += 1
                else:
                    first_seen_at = previous.get("first_seen_at", now)
                    next_items[key] = {**previous, **candidate, "first_seen_at": first_seen_at}
                    updated += 1
            removed = len(set(old_items) - set(next_items)) if normalized_mode == "replace" else 0
            pool["items"] = list(next_items.values())
            pool["latest_screen_run_id"] = str(run_id)
            source_runs = [
                str(run_id),
                *[
                    str(value)
                    for value in pool.get("source_screen_run_ids", [])
                    if str(value).strip() and str(value) != str(run_id)
                ],
            ]
            pool["source_screen_run_ids"] = source_runs[:20]
            pool["source_ref"] = f"screening:{run_id}"
            pool["updated_at"] = now
            self._persist_locked()
            decorated = self._decorate(pool)

        return {
            "ok": True,
            "pool": decorated,
            "stats": {"added": added, "updated": updated, "removed": removed},
            "stage": decorated["stage"],
            "source_run_id": str(run_id),
            "message": f"已写入 {decorated['name']}：新增 {added} 条，更新 {updated} 条，移除 {removed} 条。",
        }

    @staticmethod
    def _default_pool_id(source_pool_id: str) -> str:
        clean = str(source_pool_id).strip().replace(":", ".") or "screening"
        return f"incubator.{clean}"[:120]

    @classmethod
    def _candidates(
        cls,
        screening_run: dict[str, object],
        *,
        limit: int,
        min_score: Decimal | None,
    ) -> list[dict[str, object]]:
        raw_items = screening_run.get("items", [])
        if not isinstance(raw_items, list):
            return []
        candidates: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw in raw_items:
            if not isinstance(raw, dict) or raw.get("passed") is not True:
                continue
            dataset_id = str(raw.get("dataset_id", "")).strip()
            if not dataset_id or dataset_id in seen:
                continue
            score = cls._decimal(raw.get("score", "0"))
            if min_score is not None and score < min_score:
                continue
            seen.add(dataset_id)
            reasons = raw.get("reasons")
            reason = "；".join(str(item) for item in reasons[:3] if item) if isinstance(reasons, list) else ""
            candidates.append(
                {
                    "candidate_id": dataset_id,
                    "dataset_id": dataset_id,
                    "venue_id": str(raw.get("venue_id", "")).strip().lower(),
                    "symbol": str(raw.get("symbol", "")).strip().upper(),
                    "interval": str(raw.get("interval", "")).strip().lower(),
                    "score": str(score),
                    "period_return_pct": str(raw.get("period_return_pct", "0")),
                    "momentum_pct": str(raw.get("momentum_pct", "0")),
                    "volatility_pct": str(raw.get("volatility_pct", "0")),
                    "max_drawdown_pct": str(raw.get("max_drawdown_pct", "0")),
                    "average_quote_volume": str(raw.get("average_quote_volume", "0")),
                    "reason": reason[:240],
                    "source_run_id": str(screening_run.get("run_id", "")),
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
        candidates.sort(key=lambda item: cls._decimal(item.get("score", "0")), reverse=True)
        return candidates[: max(1, min(int(limit), 500))]

    def _recent_screens(self, limit: int, pools: list[dict[str, object]]) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for run in self._screening.list(max(1, min(int(limit), 100))):
            candidates = self._candidates(run, limit=500, min_score=None)
            candidate_ids = {str(item["candidate_id"]) for item in candidates}
            pool = next(
                (
                    item
                    for item in pools
                    if str(run.get("run_id", "")) in item.get("source_screen_run_ids", [])
                    or (
                        item.get("source_pool_id") == run.get("pool_id")
                        and item.get("pool_id") == self._default_pool_id(str(run.get("pool_id", "")))
                    )
                ),
                None,
            )
            existing_ids = {
                str(item.get("candidate_id"))
                for item in (pool or {}).get("items", [])
                if isinstance(item, dict)
            }
            existing_count = len(candidate_ids & existing_ids)
            new_count = max(0, len(candidate_ids) - existing_count)
            processed = str(run.get("run_id", "")) in (pool or {}).get("source_screen_run_ids", [])
            result.append(
                {
                    "run_id": run.get("run_id"),
                    "pool_id": run.get("pool_id"),
                    "interval": run.get("interval"),
                    "lookback": run.get("lookback"),
                    "status": run.get("status", "completed"),
                    "candidate_count": len(candidates),
                    "created_at": run.get("created_at"),
                    "target_pool_id": pool.get("pool_id") if pool else self._default_pool_id(str(run.get("pool_id", ""))),
                    "target_pool_name": pool.get("name", "") if pool else "",
                    "incubator_item_count": len((pool or {}).get("items", [])),
                    "existing_candidate_count": existing_count,
                    "new_candidate_count": new_count,
                    "already_in_incubator": bool(processed or (candidate_ids and new_count == 0)),
                    "incubator_status": "written" if processed else "covered" if candidate_ids and new_count == 0 else "pending",
                }
            )
        return result

    @classmethod
    def _decorate(cls, pool: dict[str, object]) -> dict[str, object]:
        items = [deepcopy(item) for item in pool.get("items", []) if isinstance(item, dict)]
        items.sort(key=lambda item: cls._decimal(item.get("score", "0")), reverse=True)
        stage = cls._stage(len(items))
        return {
            "pool_id": pool.get("pool_id"),
            "name": pool.get("name", "策略孵化池"),
            "description": pool.get("description", ""),
            "kind": "strategy_incubator",
            "source_type": pool.get("source_type", "screening"),
            "source_pool_id": pool.get("source_pool_id", ""),
            "source_ref": pool.get("source_ref", ""),
            "strategy_id": pool.get("strategy_id", "indicator_screening"),
            "latest_screen_run_id": pool.get("latest_screen_run_id"),
            "source_screen_run_ids": list(pool.get("source_screen_run_ids", [])),
            "candidate_count": len(items),
            "dataset_count": len(items),
            "symbol_count": len({str(item.get("symbol", "")) for item in items if item.get("symbol")}),
            "venue_ids": sorted({str(item.get("venue_id", "")) for item in items if item.get("venue_id")}),
            "symbols": sorted({str(item.get("symbol", "")) for item in items if item.get("symbol")}),
            "items": items,
            "stage": stage,
            "created_at": pool.get("created_at"),
            "updated_at": pool.get("updated_at"),
            "research_only": True,
        }

    @staticmethod
    def _stage(count: int) -> dict[str, object]:
        quick = STAGE_THRESHOLDS["quick_validate"]
        retest = STAGE_THRESHOLDS["retest"]
        progress = min(100, round(count / retest * 100))
        if count >= retest:
            return {
                "key": "ready_for_retest",
                "label": "可重测",
                "summary": "候选数据集达到当前加密市场基线，可用孵化池成员重新做组合回测和参数验证。",
                "progress": 100,
                "next_action": "run_research",
            }
        if count >= quick:
            return {
                "key": "sample_validation",
                "label": "样本验证",
                "summary": f"已有 {count} 条候选，达到快速验证门槛；再积累 {retest - count} 条进入重测阶段。",
                "progress": progress,
                "next_action": "run_research",
            }
        return {
            "key": "collecting",
            "label": "收集中",
            "summary": f"已有 {count} 条候选，距离快速验证门槛还差 {max(0, quick - count)} 条。",
            "progress": progress,
            "next_action": "collect_more",
        }

    @staticmethod
    def _decimal(value: object) -> Decimal:
        try:
            parsed = Decimal(str(value or "0"))
        except (InvalidOperation, ValueError):
            return Decimal("0")
        return parsed if parsed.is_finite() else Decimal("0")

    def _load(self) -> None:
        if self._state is None:
            return
        self._pools.clear()
        try:
            payload = self._state.load({"version": 1, "pools": []})
        except JsonStateError as error:
            raise RuntimeError("strategy incubator state is unreadable") from error
        records = payload.get("pools", []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            raise RuntimeError("strategy incubator state must contain an array")
        for record in records:
            if not isinstance(record, dict):
                continue
            pool_id = str(record.get("pool_id", "")).strip()
            if pool_id:
                pool = deepcopy(record)
                pool["items"] = [item for item in pool.get("items", []) if isinstance(item, dict)]
                pool["source_screen_run_ids"] = list(pool.get("source_screen_run_ids", []))
                self._pools[pool_id] = pool

    def _persist_locked(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save({"version": 1, "pools": list(self._pools.values())})
        except JsonStateError as error:
            raise RuntimeError("strategy incubator state cannot be saved") from error
