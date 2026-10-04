"""动态币池：规则池（24h 成交额 TopN / 近 7 天低波动 / 手动）+ 候选/交易权限分层。

链路：行情 → 筛选 → 候选池 → 人工确认 → 交易池 → 策略实例 → 模拟下单。

- 候选池（candidate）：只能做研究/回测，不能直接交易。
- 交易池（trading）：必须人工确认（confirm），策略实例只能选交易池。
- 动态池每次刷新保存成分快照；composition_at(as_of) 返回时间点成分，
  回测引用快照避免未来函数。
"""

from __future__ import annotations

import math
from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

POOL_TYPES = ("static", "top_volume", "low_volatility")
POOL_ROLES = ("candidate", "trading")
DEFAULT_REFRESH_INTERVAL_SECONDS = 3600
MAX_SNAPSHOTS = 200
DEFAULT_TOP_N = 20


class CoinPoolError(ValueError):
    """币池业务错误（参数非法、权限不足等）。"""


def _normalize_symbol(value: str) -> str:
    normalized = str(value).strip().upper().replace("-", "/")
    if "/" not in normalized:
        for quote in ("USDT", "USDC", "USD", "BTC", "ETH", "BNB"):
            if normalized.endswith(quote) and len(normalized) > len(quote):
                normalized = f"{normalized[:-len(quote)]}/{quote}"
                break
    parts = normalized.split("/")
    if len(parts) != 2 or not all(parts):
        raise CoinPoolError("symbol must be like BTC/USDT")
    return normalized


class CoinPoolService:
    """管理币池：创建、规则刷新、快照、权限分层。"""

    def __init__(
        self,
        *,
        state_store=None,
        ticker_service=None,
        history_storage=None,
    ) -> None:
        self._state = state_store
        self._tickers = ticker_service
        self._storage = history_storage
        self._lock = RLock()
        self._pools: dict[str, dict] = {}
        self._load()

    # ---------- 查询 ----------

    def list(self, role: str | None = None, pool_type: str | None = None) -> list[dict]:
        with self._lock:
            self._load()
            result = [deepcopy(spec) for spec in self._pools.values()]
        if role:
            result = [p for p in result if p["role"] == self._role(role)]
        if pool_type:
            result = [p for p in result if p["pool_type"] == self._pool_type(pool_type)]
        return sorted(result, key=lambda p: p["created_at"])

    def get(self, pool_id: str) -> dict | None:
        with self._lock:
            self._load()
            spec = self._pools.get(str(pool_id).strip())
            return deepcopy(spec) if spec else None

    def summary(self) -> dict:
        pools = self.list()
        return {
            "pool_count": len(pools),
            "member_count": sum(len(p["current_members"]) for p in pools),
            "roles": {role: sum(1 for p in pools if p["role"] == role) for role in POOL_ROLES},
            "types": {t: sum(1 for p in pools if p["pool_type"] == t) for t in POOL_TYPES},
        }

    # ---------- 创建 / 删除 ----------

    def create(
        self,
        *,
        name: str,
        pool_type: str,
        venue_id: str,
        rule: dict | None = None,
        symbols: list[str] | None = None,
        refresh_interval_seconds: int = DEFAULT_REFRESH_INTERVAL_SECONDS,
    ) -> dict:
        clean_name = str(name).strip()
        if not clean_name:
            raise CoinPoolError("pool name must not be empty")
        normalized_type = self._pool_type(pool_type)
        venue = str(venue_id).strip().lower()
        if not venue:
            raise CoinPoolError("venue_id must not be empty")
        normalized_rule = self._validate_rule(normalized_type, rule or {})
        normalized_symbols: list[str] = []
        if normalized_type == "static":
            raw = symbols or []
            normalized_symbols = sorted({_normalize_symbol(s) for s in raw if str(s).strip()})
            if not normalized_symbols:
                raise CoinPoolError("static pool requires at least one symbol")
        if refresh_interval_seconds < 300:
            raise CoinPoolError("refresh_interval_seconds must be >= 300")
        pool_id = f"cpool-{uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        spec = {
            "pool_id": pool_id,
            "name": clean_name,
            "pool_type": normalized_type,
            # 新池一律从候选开始，人工确认后才能进交易池
            "role": "candidate",
            "confirmed": False,
            "venue_id": venue,
            "rule": normalized_rule,
            "static_symbols": normalized_symbols,
            "refresh_interval_seconds": int(refresh_interval_seconds),
            "current_members": normalized_symbols,
            "snapshots": [],
            "created_at": now,
            "updated_at": now,
            "last_refresh_at": None,
        }
        with self._lock:
            self._load()
            self._pools[pool_id] = spec
            self._persist_locked()
        return deepcopy(spec)

    def delete(self, pool_id: str) -> None:
        with self._lock:
            self._load()
            key = str(pool_id).strip()
            if key not in self._pools:
                raise KeyError(f"unknown pool: {pool_id}")
            del self._pools[key]
            self._persist_locked()

    # ---------- 权限分层：人工确认 ----------

    def confirm(self, pool_id: str) -> dict:
        """人工确认：候选池 → 交易池。只有确认过的池能被策略实例选用。"""
        with self._lock:
            self._load()
            spec = self._pools.get(str(pool_id).strip())
            if spec is None:
                raise KeyError(f"unknown pool: {pool_id}")
            if not spec["current_members"]:
                raise CoinPoolError("cannot confirm an empty pool: refresh it first")
            spec["role"] = "trading"
            spec["confirmed"] = True
            spec["updated_at"] = datetime.now(UTC).isoformat()
            self._persist_locked()
            return deepcopy(spec)

    def demote(self, pool_id: str) -> dict:
        """降级回候选池（随时可做，不需要确认）。"""
        with self._lock:
            self._load()
            spec = self._pools.get(str(pool_id).strip())
            if spec is None:
                raise KeyError(f"unknown pool: {pool_id}")
            spec["role"] = "candidate"
            spec["confirmed"] = False
            spec["updated_at"] = datetime.now(UTC).isoformat()
            self._persist_locked()
            return deepcopy(spec)

    def assert_tradable(self, pool_id: str) -> dict:
        """策略实例选用币池时的门禁：必须是已确认的交易池。"""
        spec = self.get(pool_id)
        if spec is None:
            raise KeyError(f"unknown pool: {pool_id}")
        if spec["role"] != "trading" or not spec["confirmed"]:
            raise CoinPoolError(
                f"pool '{spec['name']}' is a candidate pool and cannot trade: "
                "confirm it to the trading pool first"
            )
        if not spec["current_members"]:
            raise CoinPoolError(f"pool '{spec['name']}' has no members: refresh it first")
        return spec

    # ---------- 刷新与快照 ----------

    def refresh(self, pool_id: str) -> dict:
        with self._lock:
            self._load()
            spec = self._pools.get(str(pool_id).strip())
            if spec is None:
                raise KeyError(f"unknown pool: {pool_id}")
            members = self._evaluate_locked(spec)
            now = datetime.now(UTC).isoformat()
            spec["current_members"] = members
            spec["last_refresh_at"] = now
            spec["updated_at"] = now
            spec["snapshots"].append({
                "snapshot_id": f"snap-{uuid4().hex[:8]}",
                "taken_at": now,
                "member_count": len(members),
                "members": members,
            })
            spec["snapshots"] = spec["snapshots"][-MAX_SNAPSHOTS:]
            self._persist_locked()
            return deepcopy(spec)

    def refresh_due_pools(self) -> list[dict]:
        """刷新所有到期的动态池。由引擎循环定期调用，内部判断是否到期。"""
        due: list[str] = []
        with self._lock:
            self._load()
            now_ts = datetime.now(UTC).timestamp()
            for pool_id, spec in self._pools.items():
                if spec["pool_type"] == "static":
                    continue
                last = spec.get("last_refresh_at")
                interval = int(spec.get("refresh_interval_seconds") or DEFAULT_REFRESH_INTERVAL_SECONDS)
                if last is None:
                    due.append(pool_id)
                    continue
                try:
                    last_ts = datetime.fromisoformat(last).timestamp()
                except ValueError:
                    due.append(pool_id)
                    continue
                if now_ts - last_ts >= interval:
                    due.append(pool_id)
        refreshed = []
        for pool_id in due:
            try:
                refreshed.append(self.refresh(pool_id))
            except Exception:
                # 单个池刷新失败不影响其他池，错误由调用方记日志
                continue
        return refreshed

    def composition_at(self, pool_id: str, as_of: str) -> dict:
        """返回 as_of 时间点或之前最近一次的成分快照（回测用，避免未来函数）。"""
        spec = self.get(pool_id)
        if spec is None:
            raise KeyError(f"unknown pool: {pool_id}")
        try:
            as_of_ts = datetime.fromisoformat(as_of).timestamp()
        except ValueError as error:
            raise CoinPoolError("as_of must be an ISO datetime") from error
        best = None
        for snap in spec["snapshots"]:
            try:
                snap_ts = datetime.fromisoformat(snap["taken_at"]).timestamp()
            except ValueError:
                continue
            if snap_ts <= as_of_ts and (best is None or snap_ts > best[0]):
                best = (snap_ts, snap)
        if best is None:
            raise CoinPoolError(f"no snapshot at or before {as_of}")
        snap = best[1]
        return {
            "pool_id": pool_id,
            "as_of": as_of,
            "snapshot_id": snap["snapshot_id"],
            "taken_at": snap["taken_at"],
            "members": list(snap["members"]),
            "member_count": snap["member_count"],
        }

    # ---------- 规则评估 ----------

    def _evaluate_locked(self, spec: dict) -> list[str]:
        pool_type = spec["pool_type"]
        if pool_type == "static":
            return list(spec["static_symbols"])
        if pool_type == "top_volume":
            return self._top_volume_members(spec)
        if pool_type == "low_volatility":
            return self._low_volatility_members(spec)
        raise CoinPoolError(f"unknown pool type: {pool_type}")

    def _top_volume_members(self, spec: dict) -> list[str]:
        if self._tickers is None:
            raise CoinPoolError("ticker service is unavailable")
        top_n = int(spec["rule"]["top_n"])
        snapshot = self._tickers.snapshot(
            quote_asset="USDT",
            limit=min(max(top_n * 3, top_n), 200),
            offset=0,
            sort_by="volume",
            refresh=True,
        )
        venue = spec["venue_id"]
        members: list[str] = []
        items = snapshot.get("items", []) if isinstance(snapshot, dict) else []
        # snapshot 可能按 venue 分组，也可能是扁平列表
        candidates: list[dict] = []
        if isinstance(items, dict):
            candidates = items.get(venue, []) or []
        elif isinstance(items, list):
            candidates = [it for it in items if str(it.get("venue_id", "")).lower() == venue]
        for item in candidates:
            symbol = str(item.get("symbol", "")).upper().replace("-", "/")
            if "/" not in symbol:
                continue
            if symbol in members:
                continue
            members.append(symbol)
            if len(members) >= top_n:
                break
        return members

    def _low_volatility_members(self, spec: dict) -> list[str]:
        if self._storage is None:
            raise CoinPoolError("history storage is unavailable")
        top_n = int(spec["rule"]["top_n"])
        max_vol = float(spec["rule"]["max_daily_volatility"])
        venue = spec["venue_id"]
        scored: list[tuple[float, str]] = []
        try:
            datasets = self._storage.list_datasets()
        except Exception as error:
            raise CoinPoolError("history storage is unavailable") from error
        for dataset in datasets:
            manifest = dataset.manifest
            if manifest.venue_id != venue or manifest.interval != "1d":
                continue
            if manifest.gap_count or manifest.duplicate_count:
                continue
            symbol = str(manifest.instrument_key).rsplit(":", 1)[-1].upper()
            if "/" not in symbol:
                continue
            try:
                page = self._storage.read_page(dataset, limit=10, tail=True)
                rows = list(page.items)
            except Exception:
                continue
            closes = [float(getattr(row, "close", 0) or 0) for row in rows]
            if len(closes) < 8 or any(c <= 0 for c in closes):
                continue
            log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
            mean = sum(log_returns) / len(log_returns)
            variance = sum((r - mean) ** 2 for r in log_returns) / len(log_returns)
            daily_vol = math.sqrt(variance)
            if daily_vol <= max_vol:
                scored.append((daily_vol, symbol))
        scored.sort(key=lambda item: item[0])
        return [symbol for _, symbol in scored[:top_n]]

    # ---------- 校验 ----------

    @staticmethod
    def _pool_type(value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in POOL_TYPES:
            raise CoinPoolError(f"pool_type must be one of {', '.join(POOL_TYPES)}")
        return normalized

    @staticmethod
    def _role(value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in POOL_ROLES:
            raise CoinPoolError(f"role must be one of {', '.join(POOL_ROLES)}")
        return normalized

    @classmethod
    def _validate_rule(cls, pool_type: str, rule: dict) -> dict:
        if not isinstance(rule, dict):
            raise CoinPoolError("rule must be an object")
        if pool_type == "static":
            return {}
        top_n = rule.get("top_n", DEFAULT_TOP_N)
        try:
            top_n = int(top_n)
        except (TypeError, ValueError) as error:
            raise CoinPoolError("rule.top_n must be an integer") from error
        if not 1 <= top_n <= 100:
            raise CoinPoolError("rule.top_n must be between 1 and 100")
        normalized = {"top_n": top_n}
        if pool_type == "low_volatility":
            max_vol = rule.get("max_daily_volatility", 0.03)
            try:
                max_vol = float(max_vol)
            except (TypeError, ValueError) as error:
                raise CoinPoolError("rule.max_daily_volatility must be a number") from error
            if not 0 < max_vol <= 0.5:
                raise CoinPoolError("rule.max_daily_volatility must be between 0 and 0.5")
            normalized["max_daily_volatility"] = max_vol
        return normalized

    # ---------- 持久化 ----------

    def _load(self) -> None:
        if self._state is None:
            return
        try:
            payload = self._state.load({"version": 1, "pools": {}})
        except Exception:
            payload = {"version": 1, "pools": {}}
        raw = payload.get("pools", {}) if isinstance(payload, dict) else {}
        self._pools = {}
        if isinstance(raw, dict):
            for pool_id, spec in raw.items():
                if isinstance(spec, dict) and str(pool_id).startswith("cpool-"):
                    self._pools[str(pool_id)] = spec

    def _persist_locked(self) -> None:
        if self._state is not None:
            self._state.save({"version": 1, "pools": self._pools})
