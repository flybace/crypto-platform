"""Strategy admission funnel: rating + staged verification before paper trading.

Adapted from the A-share system's strategy pipeline:
  scored → retested (same params, different window) → cross_validated
  (same strategy, different pool) → paper_approved (A-level gate)

- Each backtest/tune run produces an archive: archive_id + param_hash (SHA256).
- Rating S/A/B/C/D from backtest metrics. Only promote, never demote.
- Paper trading requires A-level or above (configurable).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from threading import RLock

# Funnel stages in order
STAGES = ("scored", "retested", "cross_validated", "paper_approved")
STAGE_LABELS = {
    "scored": "已评分",
    "retested": "同规格复测",
    "cross_validated": "跨池验证",
    "paper_approved": "模拟盘准入",
}

# Rating thresholds (0-100 score)
RATING_THRESHOLDS = {"S": 85, "A": 75, "B": 60, "C": 40}
RATING_ORDER = {"D": 0, "C": 1, "B": 2, "A": 3, "S": 4}

# Minimum rating for paper trading
PAPER_MIN_RATING = "A"


def param_hash(params: dict[str, Any]) -> str:
    """SHA256 hash of canonicalized params for reproducibility."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def calculate_score(metrics: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """Score 0-100 from backtest metrics. Returns (total, breakdown)."""
    # Extract metrics with safe defaults
    total_return = float(metrics.get("total_return", 0) or 0)
    max_drawdown = abs(float(metrics.get("max_drawdown", 1) or 1))
    win_rate = float(metrics.get("win_rate", 0) or 0)
    sharpe = float(metrics.get("sharpe_ratio", 0) or 0)
    trade_count = int(metrics.get("trade_count", 0) or 0)

    breakdown: dict[str, float] = {}

    # Return component (0-40): positive return scaled, capped
    # 20% return = 40 pts, 0% = 20 pts, -10% = 0 pts
    return_score = max(0, min(40, 20 + total_return * 100))
    breakdown["return"] = round(return_score, 1)

    # Drawdown component (0-25): smaller drawdown = higher score
    # 5% dd = 25 pts, 20% dd = 0 pts
    dd_score = max(0, min(25, 25 * (1 - max_drawdown / 0.20)))
    breakdown["drawdown"] = round(dd_score, 1)

    # Win rate component (0-15): 50% = 15 pts, 30% = 0 pts
    wr_score = max(0, min(15, (win_rate - 0.30) / 0.20 * 15))
    breakdown["win_rate"] = round(wr_score, 1)

    # Sharpe component (0-15): sharpe 2.0 = 15 pts, 0 = 0 pts
    sharpe_score = max(0, min(15, sharpe / 2.0 * 15))
    breakdown["sharpe"] = round(sharpe_score, 1)

    # Sample size component (0-5): >=30 trades = 5 pts
    sample_score = min(5, trade_count / 30 * 5)
    breakdown["sample"] = round(sample_score, 1)

    total = round(sum(breakdown.values()), 1)
    return total, breakdown


def score_to_rating(score: float) -> str:
    for rating, threshold in sorted(RATING_THRESHOLDS.items(), key=lambda x: -x[1]):
        if score >= threshold:
            return rating
    return "D"


def meets_paper_gate(rating: str, min_rating: str = PAPER_MIN_RATING) -> bool:
    return RATING_ORDER.get(rating, 0) >= RATING_ORDER.get(min_rating, 0)


class StrategyFunnelService:
    """Manages strategy archives, ratings, and funnel progression."""

    def __init__(self, *, state_store=None) -> None:
        self._state = state_store
        self._lock = RLock()
        self._archives: dict[str, dict[str, Any]] = {}  # archive_id -> archive
        self._strategies: dict[str, dict[str, Any]] = {}  # strategy_key -> funnel state
        self._load()

    def _strategy_key(self, strategy_id: str, params: dict[str, Any]) -> str:
        return f"{strategy_id}:{param_hash(params)}"

    def _load(self) -> None:
        if self._state is None:
            return
        try:
            data = self._state.load({"version": 1, "archives": {}, "strategies": {}})
        except Exception:
            return
        if isinstance(data, dict):
            self._archives = data.get("archives", {})
            self._strategies = data.get("strategies", {})

    def _save(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save({
                "version": 1,
                "archives": self._archives,
                "strategies": self._strategies,
            })
        except Exception:
            pass

    def submit_backtest(
        self,
        strategy_id: str,
        params: dict[str, Any],
        metrics: dict[str, Any],
        *,
        venue_id: str = "",
        symbol: str = "",
        pool_id: str = "",
    ) -> dict[str, Any]:
        """Archive a backtest run and (re)calculate rating. Only promotes."""
        with self._lock:
            archive_id = f"run-{uuid.uuid4().hex[:8]}"
            phash = param_hash(params)
            score, breakdown = calculate_score(metrics)
            rating = score_to_rating(score)

            archive = {
                "archive_id": archive_id,
                "strategy_id": strategy_id,
                "param_hash": phash,
                "params": params,
                "metrics": metrics,
                "score": score,
                "score_breakdown": breakdown,
                "rating": rating,
                "venue_id": venue_id,
                "symbol": symbol,
                "pool_id": pool_id,
                "created_at": time.time(),
            }
            self._archives[archive_id] = archive

            key = self._strategy_key(strategy_id, params)
            existing = self._strategies.get(key, {})
            old_rating = existing.get("rating", "D")
            # Only promote, never demote
            new_rating = rating if RATING_ORDER.get(rating, 0) > RATING_ORDER.get(old_rating, 0) else old_rating

            funnel = {
                "strategy_id": strategy_id,
                "param_hash": phash,
                "params": params,
                "rating": new_rating,
                "score": max(score, existing.get("score", 0)),
                "stage": existing.get("stage", "scored"),
                "stage_history": existing.get("stage_history", []),
                "archive_ids": [*existing.get("archive_ids", []), archive_id],
                "updated_at": time.time(),
            }
            # First submission sets stage to scored
            if not existing:
                funnel["stage_history"] = [{"stage": "scored", "at": time.time(), "archive_id": archive_id}]
            self._strategies[key] = funnel
            self._save()
            return {"archive": archive, "funnel": funnel, "promoted": new_rating != old_rating}

    def get_funnel(self, strategy_id: str, params: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            return self._strategies.get(self._strategy_key(strategy_id, params))

    def list_funnels(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(
                self._strategies.values(),
                key=lambda x: (-RATING_ORDER.get(x.get("rating", "D"), 0), x.get("strategy_id", "")),
            )

    def advance_stage(
        self,
        strategy_id: str,
        params: dict[str, Any],
        to_stage: str,
        *,
        note: str = "",
    ) -> dict[str, Any]:
        """Advance funnel to next stage. Must go in order, no skipping."""
        with self._lock:
            key = self._strategy_key(strategy_id, params)
            funnel = self._strategies.get(key)
            if funnel is None:
                raise KeyError("strategy not found in funnel; submit a backtest first")
            if to_stage not in STAGES:
                raise ValueError(f"unknown stage: {to_stage}")
            current_idx = STAGES.index(funnel["stage"])
            target_idx = STAGES.index(to_stage)
            if target_idx != current_idx + 1:
                raise ValueError(
                    f"must advance one stage at a time "
                    f"(current: {funnel['stage']}, requested: {to_stage})"
                )
            # Gate: paper_approved requires A-level
            if to_stage == "paper_approved" and not meets_paper_gate(funnel["rating"]):
                raise ValueError(
                    f"paper gate requires rating {PAPER_MIN_RATING}+ "
                    f"(current: {funnel['rating']})"
                )
            funnel["stage"] = to_stage
            funnel["stage_history"].append({
                "stage": to_stage, "at": time.time(), "note": note,
            })
            funnel["updated_at"] = time.time()
            self._save()
            return funnel

    def check_paper_eligible(self, strategy_id: str, params: dict[str, Any]) -> tuple[bool, str]:
        """Check if strategy+params may enter paper trading."""
        with self._lock:
            funnel = self._strategies.get(self._strategy_key(strategy_id, params))
            if funnel is None:
                return False, "no rating yet; run a backtest first"
            if funnel["stage"] != "paper_approved":
                return False, f"funnel stage is {funnel['stage']}, need paper_approved"
            if not meets_paper_gate(funnel["rating"]):
                return False, f"rating {funnel['rating']} below {PAPER_MIN_RATING}"
            return True, "eligible"
