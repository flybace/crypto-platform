"""Market regime service: scores market state and links it to position sizing.

Adapted from the A-share system's RISK/CONTROL ROOM:
  market scored as strong / neutral / weak / crisis
  → position factor scales allocation
  → crisis / data-unavailable blocks new positions entirely

Regime inputs (BTC/USDT daily as benchmark, configurable):
- Trend: close vs 60-day MA, consecutive up/down days
- Drawdown: distance from 60-day high
- Breadth: % of tracked symbols above 20-day MA (when available)

Score 0-100 → regime:
  >=65 strong   (factor 1.0)
  >=45 neutral  (factor 0.6)
  >=30 weak     (factor 0.3)
  <30  crisis   (factor 0.0 — no new positions)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from threading import RLock
from typing import Any


REGIMES = ("strong", "neutral", "weak", "crisis")
REGIME_LABELS = {"strong": "偏强", "neutral": "中性", "weak": "偏弱", "crisis": "危机"}

DEFAULT_THRESHOLDS = {"strong": 65.0, "neutral": 45.0, "weak": 30.0}
DEFAULT_FACTORS = {"strong": 1.0, "neutral": 0.6, "weak": 0.3, "crisis": 0.0}

DEFAULTS = {
    "enabled": True,
    "benchmark_venue": "binance",
    "benchmark_symbol": "BTC/USDT",
    "benchmark_interval": "1d",
    "lookback_days": 60,
    "thresholds": dict(DEFAULT_THRESHOLDS),
    "factors": dict(DEFAULT_FACTORS),
}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class RegimeResult:
    regime: str
    score: float
    factor: float
    blocks_new_positions: bool
    reason: str
    details: dict[str, Any]
    evaluated_at: str


class MarketRegimeService:
    """Evaluates market regime from benchmark candles. Thread-safe, cached."""

    def __init__(self, *, storage=None, state_store=None, cache_ttl_seconds: int = 300) -> None:
        self._storage = storage
        self._state = state_store
        self._lock = RLock()
        self._config: dict[str, Any] = dict(DEFAULTS)
        self._cache: RegimeResult | None = None
        self._cache_at: float = 0.0
        self._cache_ttl = max(60, int(cache_ttl_seconds))
        self._load()

    # -- config --
    def _load(self) -> None:
        if self._state is None:
            return
        try:
            data = self._state.load({"version": 1, "regime": dict(DEFAULTS)})
        except Exception:
            return
        if isinstance(data, dict) and isinstance(data.get("regime"), dict):
            merged = dict(DEFAULTS)
            merged.update(data["regime"])
            self._config = merged

    def _save(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save({"version": 1, "regime": self._config})
        except Exception:
            pass

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            import copy
            return copy.deepcopy(self._config)

    def update_config(self, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            for key in ("enabled", "benchmark_venue", "benchmark_symbol",
                        "benchmark_interval", "lookback_days"):
                if key in values:
                    self._config[key] = values[key]
            for key in ("thresholds", "factors"):
                if isinstance(values.get(key), dict):
                    merged = dict(self._config[key])
                    merged.update(values[key])
                    self._config[key] = merged
            self._cache = None  # invalidate
            self._save()
            return self.get_config()

    # -- evaluation --
    def evaluate(self, *, force: bool = False) -> RegimeResult:
        import time
        with self._lock:
            now = time.time()
            if not force and self._cache and (now - self._cache_at) < self._cache_ttl:
                return self._cache
            result = self._compute()
            self._cache = result
            self._cache_at = now
            return result

    def _compute(self) -> RegimeResult:
        at = datetime.now(UTC).isoformat()
        if not self._config.get("enabled"):
            return RegimeResult("neutral", 50.0, 0.6, False,
                                "regime disabled → neutral", {}, at)
        if self._storage is None:
            return self._unavailable(at, "no storage")

        venue = str(self._config["benchmark_venue"]).strip().lower()
        symbol = str(self._config["benchmark_symbol"]).strip().upper()
        interval = str(self._config["benchmark_interval"]).strip().lower()
        lookback = int(self._config.get("lookback_days", 60))

        try:
            dataset = self._storage.find_dataset(
                venue_id=venue,
                instrument_key=f"{venue}:spot:{symbol}",
                interval=interval,
            )
        except Exception as exc:
            return self._unavailable(at, f"dataset lookup failed: {exc}")
        if dataset is None:
            return self._unavailable(at, f"no {symbol} {interval} dataset")
        if dataset.manifest.gap_count or dataset.manifest.duplicate_count:
            return self._unavailable(at, "benchmark dataset quality gate failed")

        try:
            page = self._storage.read_page(dataset, limit=lookback + 5, tail=True)
            rows = list(page.items)
        except Exception as exc:
            return self._unavailable(at, f"candle read failed: {exc}")
        if len(rows) < 30:
            return self._unavailable(at, f"only {len(rows)} candles, need 30+")

        closes = [_to_float(getattr(r, "close", 0)) for r in rows]
        closes = [c for c in closes if c > 0]
        if len(closes) < 30:
            return self._unavailable(at, "insufficient valid closes")

        score, details = self._score(closes)
        regime = self._classify(score)
        factor = float(self._config["factors"].get(regime, 0.0))
        blocks = regime == "crisis"
        reason = f"{REGIME_LABELS[regime]} (score {score:.1f})"
        return RegimeResult(regime, score, factor, blocks, reason, details, at)

    def _unavailable(self, at: str, why: str) -> RegimeResult:
        # Data unavailable → crisis: block new positions (A-share rule)
        return RegimeResult("crisis", 0.0, 0.0, True,
                            f"数据不可用，禁止开仓: {why}",
                            {"unavailable_reason": why}, at)

    def _score(self, closes: list[float]) -> tuple[float, dict[str, Any]]:
        n = len(closes)
        details: dict[str, Any] = {}

        # 1. Trend vs MA60 (0-40)
        ma60 = sum(closes[-60:]) / min(60, n)
        last = closes[-1]
        trend_ratio = (last / ma60 - 1) if ma60 > 0 else 0
        # +10% above MA60 → 40, at MA60 → 20, -10% → 0
        trend_score = max(0.0, min(40.0, 20.0 + trend_ratio * 200.0))
        details["trend_ratio"] = round(trend_ratio, 4)
        details["trend_score"] = round(trend_score, 1)

        # 2. Consecutive days (0-20): up streak adds, down streak subtracts
        streak = 0
        for i in range(n - 1, 0, -1):
            if closes[i] > closes[i - 1]:
                if streak >= 0:
                    streak += 1
                else:
                    break
            elif closes[i] < closes[i - 1]:
                if streak <= 0:
                    streak -= 1
                else:
                    break
            else:
                break
        # +5 up days → 20, 0 → 10, -5 down → 0
        streak_score = max(0.0, min(20.0, 10.0 + streak * 2.0))
        details["streak_days"] = streak
        details["streak_score"] = round(streak_score, 1)

        # 3. Drawdown from 60d high (0-25)
        high60 = max(closes[-60:])
        dd = (last / high60 - 1) if high60 > 0 else 0  # negative or 0
        # 0 dd → 25, -20% dd → 0
        dd_score = max(0.0, min(25.0, 25.0 * (1 + dd / 0.20)))
        details["drawdown_60d"] = round(dd, 4)
        details["drawdown_score"] = round(dd_score, 1)

        # 4. Volatility penalty (0-15): high vol reduces score
        # Use 20d std of daily returns
        rets = []
        for i in range(max(1, n - 20), n):
            if closes[i - 1] > 0:
                rets.append(abs(closes[i] / closes[i - 1] - 1))
        avg_vol = sum(rets) / len(rets) if rets else 0
        # 2% avg daily move → 15, 8% → 0
        vol_score = max(0.0, min(15.0, 15.0 * (1 - max(0.0, avg_vol - 0.02) / 0.06)))
        details["avg_daily_vol"] = round(avg_vol, 4)
        details["vol_score"] = round(vol_score, 1)

        total = round(trend_score + streak_score + dd_score + vol_score, 1)
        return total, details

    def _classify(self, score: float) -> str:
        t = self._config["thresholds"]
        if score >= float(t.get("strong", 65)):
            return "strong"
        if score >= float(t.get("neutral", 45)):
            return "neutral"
        if score >= float(t.get("weak", 30)):
            return "weak"
        return "crisis"
