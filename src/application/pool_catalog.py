"""History-backed coin-pool catalog for research, backtest, and paper use."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from application.history_storage import HistoryStorage, HistoryStorageError, StoredDataset

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore


POOL_KINDS = ("research", "backtest", "paper")
_SYSTEM_SPECS = {
    "system.research.cross_venue": {
        "name": "跨市场研究池",
        "kind": "research",
        "interval": None,
        "description": "包含已校验且可用于多交易所比较的现货 K 线。",
    },
    "system.backtest.verified": {
        "name": "已校验回测池",
        "kind": "backtest",
        "interval": "1d",
        "description": "仅包含质量门禁通过的日线数据集。",
    },
    "system.paper.whitelist": {
        "name": "模拟盘白名单",
        "kind": "paper",
        "interval": "1h",
        "description": "使用已校验小时线生成模拟盘可选标的。",
    },
}


class PoolCatalog:
    """Build deterministic system pools and keep user pools in process memory."""

    def __init__(
        self,
        storage: HistoryStorage,
        *,
        state_path: str | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self._storage = storage
        self._custom_specs: dict[str, dict[str, object]] = {}
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._load()

    def list(self, kind: str | None = None) -> list[dict[str, object]]:
        self._load()
        normalized_kind = self._kind(kind) if kind else None
        result: list[dict[str, object]] = []
        for pool_id, spec in _SYSTEM_SPECS.items():
            if normalized_kind and spec["kind"] != normalized_kind:
                continue
            result.append(self._build(pool_id, spec))
        for pool_id, spec in sorted(self._custom_specs.items()):
            if normalized_kind and spec["kind"] != normalized_kind:
                continue
            result.append(self._build(pool_id, spec))
        return result

    def summary(self) -> dict[str, object]:
        pools = self.list()
        members = sum(len(pool["members"]) for pool in pools)
        complete = sum(
            1
            for pool in pools
            for member in pool["members"]
            if member.get("coverage_status") == "COMPLETE"
        )
        return {
            "pool_count": len(pools),
            "member_count": members,
            "complete_member_count": complete,
            "kinds": {kind: sum(1 for pool in pools if pool["kind"] == kind) for kind in POOL_KINDS},
            "pools": pools,
        }

    def get(self, pool_id: str) -> dict[str, object] | None:
        self._load()
        key = str(pool_id).strip()
        if key in _SYSTEM_SPECS:
            return self._build(key, _SYSTEM_SPECS[key])
        spec = self._custom_specs.get(key)
        return None if spec is None else self._build(key, spec)

    def create(
        self,
        *,
        name: str,
        kind: str,
        venue_ids: list[str],
        symbols: list[str],
        interval: str | None,
        description: str = "",
    ) -> dict[str, object]:
        clean_name = str(name).strip()
        if not clean_name:
            raise ValueError("pool name must not be empty")
        normalized_kind = self._kind(kind)
        normalized_venues = sorted({str(value).strip().lower() for value in venue_ids if str(value).strip()})
        normalized_symbols = sorted({self._symbol(value) for value in symbols if str(value).strip()})
        if not normalized_venues or not normalized_symbols:
            raise ValueError("pool requires at least one venue and one symbol")
        normalized_interval = None if interval in (None, "", "all") else self._interval(interval)
        pool_id = f"pool-{uuid4().hex[:12]}"
        self._load()
        self._custom_specs[pool_id] = {
            "pool_id": pool_id,
            "name": clean_name,
            "kind": normalized_kind,
            "venue_ids": normalized_venues,
            "symbols": normalized_symbols,
            "interval": normalized_interval,
            "description": str(description).strip(),
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._persist()
        return self.get(pool_id)  # type: ignore[return-value]

    def refresh(self, pool_id: str) -> dict[str, object]:
        pool = self.get(pool_id)
        if pool is None:
            raise KeyError(f"unknown pool: {pool_id}")
        return pool

    def datasets_for(self, pool_id: str) -> tuple[StoredDataset, ...]:
        pool = self.get(pool_id)
        if pool is None:
            raise KeyError(f"unknown pool: {pool_id}")
        ids = {str(member["dataset_id"]) for member in pool["members"]}
        return tuple(dataset for dataset in self._storage.list_datasets() if dataset.manifest.dataset_id in ids)

    def _build(self, pool_id: str, spec: dict[str, object]) -> dict[str, object]:
        datasets = self._matching_datasets(spec)
        members = [self._member(dataset, datasets) for dataset in datasets]
        symbols = sorted({str(member["symbol"]) for member in members})
        venues = sorted({str(member["venue_id"]) for member in members})
        return {
            "pool_id": pool_id,
            "name": spec["name"],
            "kind": spec["kind"],
            "description": spec.get("description", ""),
            "interval": spec.get("interval"),
            "venue_ids": venues,
            "symbols": symbols,
            "dataset_count": len(members),
            "member_count": len(members),
            "complete_symbol_count": sum(
                1 for symbol in symbols if sum(1 for member in members if member["symbol"] == symbol and member["quality_passed"]) >= 2
            ),
            "members": members,
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def _matching_datasets(self, spec: dict[str, object]) -> tuple[StoredDataset, ...]:
        try:
            datasets = self._storage.list_datasets()
        except HistoryStorageError:
            raise
        configured_venues = {str(value).lower() for value in spec.get("venue_ids", [])}
        configured_symbols = {str(value).upper() for value in spec.get("symbols", [])}
        configured_interval = spec.get("interval")
        result = []
        for dataset in datasets:
            manifest = dataset.manifest
            symbol = self._dataset_symbol(manifest.instrument_key)
            if configured_venues and manifest.venue_id not in configured_venues:
                continue
            if configured_symbols and symbol not in configured_symbols:
                continue
            if configured_interval and manifest.interval != configured_interval:
                continue
            if spec.get("kind") == "research" and manifest.data_level.value != "KLINE":
                continue
            result.append(dataset)
        return tuple(result)

    @classmethod
    def _member(cls, dataset: StoredDataset, siblings: tuple[StoredDataset, ...]) -> dict[str, object]:
        manifest = dataset.manifest
        symbol = cls._dataset_symbol(manifest.instrument_key)
        sibling_venues = {
            item.manifest.venue_id
            for item in siblings
            if cls._dataset_symbol(item.manifest.instrument_key) == symbol and item.manifest.interval == manifest.interval
            and item.manifest.gap_count == 0 and item.manifest.duplicate_count == 0
        }
        quality_passed = manifest.gap_count == 0 and manifest.duplicate_count == 0
        return {
            "dataset_id": manifest.dataset_id,
            "venue_id": manifest.venue_id,
            "symbol": symbol,
            "native_symbol": cls._native_symbol(dataset.storage_key),
            "interval": manifest.interval,
            "row_count": manifest.row_count,
            "start_at": manifest.start_at.isoformat(),
            "end_at": manifest.end_at.isoformat(),
            "latest_at": manifest.end_at.isoformat(),
            "quality_passed": quality_passed,
            "quality_status": "PASS" if quality_passed else "BLOCKED",
            "coverage_status": "COMPLETE" if len(sibling_venues) >= 2 else "PARTIAL",
            "venue_count": len(sibling_venues),
            "storage_key": dataset.storage_key,
        }

    @staticmethod
    def _kind(value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in POOL_KINDS:
            raise ValueError("pool kind must be one of research, backtest, paper")
        return normalized

    @staticmethod
    def _interval(value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in {"1d", "1h", "5m"}:
            raise ValueError("pool interval must be one of 1d, 1h, 5m, or all")
        return normalized

    @staticmethod
    def _symbol(value: str) -> str:
        normalized = str(value).strip().upper().replace("-", "/")
        if "/" not in normalized:
            for quote in ("USDT", "USDC", "USD", "BTC", "ETH", "BNB"):
                if normalized.endswith(quote) and len(normalized) > len(quote):
                    normalized = f"{normalized[:-len(quote)]}/{quote}"
                    break
        parts = normalized.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("symbol must be like BTC/USDT")
        return normalized

    @staticmethod
    def _dataset_symbol(instrument_key: str) -> str:
        return str(instrument_key).rsplit(":", 1)[-1].upper()

    @staticmethod
    def _native_symbol(storage_key: str) -> str:
        parts = str(storage_key).split("/")
        return parts[-2] if len(parts) >= 2 else parts[-1]

    def _load(self) -> None:
        if self._state is None:
            return
        self._custom_specs.clear()
        try:
            payload = self._state.load({"version": 1, "pools": []})
        except JsonStateError as error:
            raise RuntimeError("pool state is unreadable") from error
        raw_pools = payload.get("pools", []) if isinstance(payload, dict) else []
        if not isinstance(raw_pools, list):
            raise RuntimeError("pool state must contain an array")
        for raw_pool in raw_pools:
            if not isinstance(raw_pool, dict):
                continue
            pool_id = str(raw_pool.get("pool_id", "")).strip()
            if pool_id.startswith("pool-") and pool_id not in _SYSTEM_SPECS:
                self._custom_specs[pool_id] = deepcopy(raw_pool)

    def _persist(self) -> None:
        if self._state is not None:
            try:
                self._state.save({"version": 1, "pools": list(self._custom_specs.values())})
            except JsonStateError as error:
                raise RuntimeError("pool state cannot be saved") from error
