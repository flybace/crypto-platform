"""Research-only advice snapshots for the crypto control plane.

The service deliberately uses verified historical candles and screening output
as research candidates. It never turns a candidate into an order intent.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock
from uuid import uuid4

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore
from application.history_storage import HistoryStorage, HistoryStorageError
from backend.app.services.market_summary import build_market_summary
from application.screening import ScreeningService


SUPPORTED_INTERVALS = frozenset({"1d", "1h", "5m"})


class AdviceError(ValueError):
    """Raised when a research advice request cannot be evaluated."""


class AdviceService:
    """Build, store and query bounded research candidate snapshots."""

    def __init__(
        self,
        storage: HistoryStorage,
        screening: ScreeningService,
        *,
        state_path: str | Path | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self._storage = storage
        self._screening = screening
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._snapshots: dict[str, dict[str, object]] = {}
        self._lock = RLock()
        self._load()

    def summary(self, *, interval: str = "1h", compact: bool = False) -> dict[str, object]:
        snapshot = self._latest_or_ephemeral(interval)
        items = list(snapshot.get("items", [])) if snapshot else []
        counts = {
            "focus": sum(1 for item in items if item.get("recommendation_grade") == "focus"),
            "watch": sum(1 for item in items if item.get("recommendation_grade") == "watch"),
            "avoid": sum(1 for item in items if item.get("recommendation_grade") == "avoid"),
        }
        risk_counts = {
            level: sum(1 for item in items if item.get("risk_level") == level)
            for level in ("low", "medium", "high")
        }
        latest = deepcopy(snapshot) if snapshot else None
        if compact and latest is not None:
            latest.pop("items", None)
        return {
            "module": "advice",
            "status": "research_only",
            "interval": self._interval(interval),
            "latest": latest,
            "total": len(items),
            "focus": counts["focus"],
            "watch": counts["watch"],
            "avoid": counts["avoid"],
            "risk_counts": risk_counts,
            "research_only": True,
            "execution_eligible": False,
            "note": "候选只用于研究、回测和模拟盘，不是买卖建议。",
        }

    def candidates(
        self,
        *,
        interval: str = "1h",
        grade: str = "all",
        risk: str = "all",
        limit: int = 100,
        compact: bool = False,
    ) -> dict[str, object]:
        if grade not in {"all", "focus", "watch", "avoid"}:
            raise AdviceError("grade must be all, focus, watch, or avoid")
        if risk not in {"all", "low", "medium", "high"}:
            raise AdviceError("risk must be all, low, medium, or high")
        snapshot = self._latest_or_ephemeral(interval)
        items = list(snapshot.get("items", [])) if snapshot else []
        if grade != "all":
            items = [item for item in items if item.get("recommendation_grade") == grade]
        if risk != "all":
            items = [item for item in items if item.get("risk_level") == risk]
        total = len(items)
        bounded = deepcopy(items[: max(1, min(int(limit), 500))])
        if compact:
            bounded = [{key: value for key, value in item.items() if key != "raw"} for item in bounded]
        return {
            "items": bounded,
            "count": total,
            "snapshot": deepcopy(snapshot) if snapshot else None,
            "research_only": True,
            "execution_eligible": False,
        }

    def snapshots(self, limit: int = 30) -> list[dict[str, object]]:
        with self._lock:
            self._load()
            records = sorted(
                self._snapshots.values(),
                key=lambda item: str(item.get("created_at", "")),
                reverse=True,
            )
            return [deepcopy(item) for item in records[: max(1, min(int(limit), 100))]]

    def get(self, snapshot_id: str) -> dict[str, object] | None:
        with self._lock:
            self._load()
            item = self._snapshots.get(str(snapshot_id))
            return deepcopy(item) if item is not None else None

    def generate(self, *, interval: str = "1h", limit: int = 100) -> dict[str, object]:
        normalized = self._interval(interval)
        try:
            market = build_market_summary(self._storage, interval=normalized)
        except (HistoryStorageError, ValueError) as error:
            raise AdviceError("verified market history is unavailable") from error
        items = self._market_items(market, limit)
        return self.create(
            title=f"跨市场研究快照 · {normalized}",
            source="verified_market_summary",
            interval=normalized,
            items=items,
        )

    def create(
        self,
        *,
        title: str,
        source: str,
        interval: str,
        items: list[dict[str, object]],
    ) -> dict[str, object]:
        normalized = self._interval(interval)
        if not isinstance(items, list) or len(items) > 500:
            raise AdviceError("items must contain at most 500 candidates")
        normalized_items = [self._normalize_item(item, index, source) for index, item in enumerate(items)]
        now = datetime.now(UTC).isoformat()
        result = {
            "snapshot_id": f"advice-{uuid4().hex}",
            "title": str(title).strip()[:160] or "研究建议快照",
            "source": str(source).strip()[:80] or "manual",
            "interval": normalized,
            "created_at": now,
            "updated_at": now,
            "items": normalized_items,
            "count": len(normalized_items),
            "research_only": True,
            "execution_eligible": False,
        }
        with self._lock:
            self._load()
            self._snapshots[str(result["snapshot_id"])] = deepcopy(result)
            self._persist_locked()
        return deepcopy(result)

    def from_screen(self, screen_run_id: str) -> dict[str, object]:
        screen = self._screening.get(screen_run_id)
        if screen is None:
            raise AdviceError("screening run was not found")
        raw_items = screen.get("items", [])
        if not isinstance(raw_items, list) or not raw_items:
            raise AdviceError("screening run has no candidates")
        items: list[dict[str, object]] = []
        for item in raw_items:
            if not isinstance(item, dict) or item.get("passed") is not True:
                continue
            items.append(
                {
                    "symbol": item.get("symbol", ""),
                    "venue_id": item.get("venue_id", ""),
                    "dataset_id": item.get("dataset_id", ""),
                    "score": item.get("score", "0"),
                    "risk_level": "high" if Decimal(str(item.get("volatility_pct", "0") or "0")) >= 8 else "medium",
                    "reason": "通过历史指标筛选，仍需回测和模拟盘复核。",
                    "next_action": "先运行策略回测，不触发真实交易。",
                    "raw": item,
                }
            )
        if not items:
            raise AdviceError("screening run has no passed candidates")
        return self.create(
            title=f"筛选研究快照 · {screen.get('run_id', screen_run_id)}",
            source=f"screening:{screen_run_id}",
            interval=str(screen.get("interval", "1h")),
            items=items,
        )

    def _latest_or_ephemeral(self, interval: str) -> dict[str, object] | None:
        normalized = self._interval(interval)
        with self._lock:
            stored = next(
                (
                    item
                    for item in sorted(
                        self._snapshots.values(),
                        key=lambda value: str(value.get("created_at", "")),
                        reverse=True,
                    )
                    if item.get("interval") == normalized
                ),
                None,
            )
        if stored is not None:
            return deepcopy(stored)
        try:
            market = build_market_summary(self._storage, interval=normalized)
        except (HistoryStorageError, ValueError) as error:
            raise AdviceError("verified market history is unavailable") from error
        items = self._market_items(market, 100)
        if not items:
            return None
        return {
            "snapshot_id": None,
            "title": f"实时研究投影 · {normalized}",
            "source": "verified_market_summary",
            "interval": normalized,
            "created_at": market.get("as_of"),
            "updated_at": market.get("as_of"),
            "items": items,
            "count": len(items),
            "research_only": True,
            "execution_eligible": False,
        }

    @staticmethod
    def _market_items(summary: dict[str, object], limit: int) -> list[dict[str, object]]:
        spreads = {
            str(item.get("symbol")): item
            for item in summary.get("spreads", [])
            if isinstance(item, dict)
        }
        items: list[dict[str, object]] = []
        quotes = summary.get("quotes", [])
        for index, raw in enumerate(quotes if isinstance(quotes, list) else []):
            if not isinstance(raw, dict):
                continue
            symbol = str(raw.get("symbol", "")).strip()
            if not symbol:
                continue
            change = AdviceService._number(raw.get("change_pct", "0"))
            abs_change = abs(change)
            risk = "high" if abs_change >= Decimal("8") else "medium" if abs_change >= Decimal("3") else "low"
            grade = "focus" if change >= Decimal("2") else "avoid" if change <= Decimal("-8") else "watch"
            score = max(Decimal("0"), min(Decimal("100"), Decimal("60") + change * Decimal("4")))
            spread = spreads.get(symbol, {})
            items.append(
                {
                    "rank": index + 1,
                    "symbol": symbol,
                    "name": symbol,
                    "venue_id": raw.get("venue_id"),
                    "dataset_id": raw.get("dataset_id"),
                    "recommendation_score": str(score.quantize(Decimal("0.01"))),
                    "recommendation_grade": grade,
                    "recommendation_label": {"focus": "重点研究", "watch": "观察", "avoid": "暂不研究"}[grade],
                    "risk_level": risk,
                    "change_pct": str(change),
                    "close": raw.get("close"),
                    "quote_volume": raw.get("quote_volume"),
                    "spread_pct": spread.get("spread_pct"),
                    "spread_buy_venue_id": spread.get("buy_venue_id"),
                    "spread_sell_venue_id": spread.get("sell_venue_id"),
                    "reason": "来自已校验历史 K 线的研究排序；不代表实时盘口信号。",
                    "next_action": "先运行策略回测，再进入模拟盘验证。",
                    "backtest_gate": {
                        "level": "history_verified",
                        "label": "历史质量通过",
                        "allow_paper_research": True,
                        "reason": "数据集已通过 Manifest、连续性和重复检查。",
                    },
                    "source": "verified_market_summary",
                    "research_only": True,
                    "execution_eligible": False,
                }
            )
        items.sort(key=lambda item: AdviceService._number(item.get("recommendation_score", "0")), reverse=True)
        return items[: max(1, min(int(limit), 500))]

    @staticmethod
    def _normalize_item(item: dict[str, object], index: int, source: str) -> dict[str, object]:
        if not isinstance(item, dict):
            raise AdviceError("each candidate must be an object")
        symbol = str(item.get("symbol") or item.get("name") or "").strip()
        if not symbol:
            raise AdviceError(f"candidate {index + 1} must contain symbol")
        score = max(Decimal("0"), min(Decimal("100"), AdviceService._number(item.get("recommendation_score", item.get("score", "0")))))
        risk = str(item.get("risk_level", item.get("risk", "medium"))).strip().lower()
        if risk not in {"low", "medium", "high"}:
            risk = "medium"
        grade = str(item.get("recommendation_grade", "watch")).strip().lower()
        if grade not in {"focus", "watch", "avoid"}:
            grade = "watch"
        return {
            "rank": index + 1,
            "symbol": symbol,
            "name": str(item.get("name") or symbol).strip()[:120],
            "venue_id": str(item.get("venue_id", "")).strip().lower() or None,
            "dataset_id": str(item.get("dataset_id", "")).strip() or None,
            "recommendation_score": str(score),
            "recommendation_grade": grade,
            "recommendation_label": {"focus": "重点研究", "watch": "观察", "avoid": "暂不研究"}[grade],
            "risk_level": risk,
            "reason": str(item.get("reason") or "来自研究候选快照。")[:300],
            "next_action": str(item.get("next_action") or "先运行策略回测，再进入模拟盘验证。")[:300],
            "source": str(item.get("source") or source)[:100],
            "raw": deepcopy(item.get("raw", item)),
            "research_only": True,
            "execution_eligible": False,
        }

    @staticmethod
    def _interval(value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in SUPPORTED_INTERVALS:
            raise AdviceError("interval must be 1d, 1h, or 5m")
        return normalized

    @staticmethod
    def _number(value: object) -> Decimal:
        try:
            number = Decimal(str(value or "0"))
        except (InvalidOperation, ValueError, TypeError) as error:
            raise AdviceError("candidate numeric values must be finite") from error
        if not number.is_finite():
            raise AdviceError("candidate numeric values must be finite")
        return number

    def _load(self) -> None:
        if self._state is None:
            return
        self._snapshots.clear()
        try:
            payload = self._state.load({"version": 1, "snapshots": []})
        except JsonStateError as error:
            raise RuntimeError("advice state is unreadable") from error
        records = payload.get("snapshots", []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            raise RuntimeError("advice state must contain an array")
        for record in records:
            if isinstance(record, dict) and str(record.get("snapshot_id", "")).strip():
                self._snapshots[str(record["snapshot_id"])] = deepcopy(record)

    def _persist_locked(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save({"version": 1, "snapshots": list(self._snapshots.values())[-100:]})
        except JsonStateError as error:
            raise RuntimeError("advice state cannot be saved") from error
