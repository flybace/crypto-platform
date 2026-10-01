"""Strategy package metadata with an explicit no-arbitrary-code boundary."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore
from application.candle_backtest import strategy_catalog


class StrategyPackageError(ValueError):
    """Raised when a package manifest is invalid."""


class StrategyPackageService:
    """Manage versioned strategy manifests, not executable user scripts.

    Built-in packages point at the trusted candle engine. Custom package
    records remain metadata-only until a separately audited sandbox contract
    is implemented; registering one never makes it runnable.
    """

    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._custom: dict[str, dict[str, object]] = {}
        self._lock = RLock()
        self._load()

    def list(self, *, include_disabled: bool = True) -> list[dict[str, object]]:
        builtins = [self._builtin(item) for item in strategy_catalog()]
        with self._lock:
            self._load()
            custom = [deepcopy(item) for item in self._custom.values()]
        items = builtins + custom
        if not include_disabled:
            items = [item for item in items if item.get("status") in {"validated", "active"}]
        return sorted(items, key=lambda item: (str(item.get("source_type")), str(item.get("package_id"))))

    def get(self, package_id: str) -> dict[str, object] | None:
        key = str(package_id).strip()
        item = next((item for item in self.list() if item.get("package_id") == key), None)
        return deepcopy(item) if item is not None else None

    def register(
        self,
        *,
        package_id: str,
        name: str,
        version: str,
        strategy_ids: list[str],
        note: str = "",
    ) -> dict[str, object]:
        package_key = str(package_id).strip().lower()
        if not package_key or package_key.startswith("builtin:"):
            raise StrategyPackageError("custom package_id must not be empty or use builtin prefix")
        if not str(name).strip() or not str(version).strip():
            raise StrategyPackageError("package name and version must not be empty")
        known = {str(item["strategy_id"]) for item in strategy_catalog()}
        normalized_ids = list(dict.fromkeys(str(item).strip().lower() for item in strategy_ids if str(item).strip()))
        if not normalized_ids or any(item not in known for item in normalized_ids):
            raise StrategyPackageError("package strategy_ids must reference built-in strategies")
        now = datetime.now(UTC).isoformat()
        record = {
            "package_id": package_key,
            "name": str(name).strip()[:120],
            "version": str(version).strip()[:40],
            "strategy_ids": normalized_ids,
            "source_type": "metadata",
            "entrypoint": None,
            "status": "draft",
            "runnable": False,
            "note": str(note).strip()[:300],
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._load()
            self._custom[package_key] = record
            self._persist_locked()
            return deepcopy(record)

    def summary(self) -> dict[str, object]:
        items = self.list()
        return {
            "package_count": len(items),
            "builtin_count": sum(1 for item in items if item.get("source_type") == "builtin"),
            "custom_count": sum(1 for item in items if item.get("source_type") != "builtin"),
            "runnable_count": sum(1 for item in items if item.get("runnable") is True),
            "items": items,
            "execution_boundary": "custom package execution is disabled until sandbox validation exists",
        }

    @staticmethod
    def _builtin(item: dict[str, Any]) -> dict[str, object]:
        strategy_id = str(item["strategy_id"])
        return {
            "package_id": f"builtin:{strategy_id}",
            "name": item["name"],
            "version": item["version"],
            "strategy_ids": [strategy_id],
            "source_type": "builtin",
            "entrypoint": "application.candle_backtest:CandleBacktestEngine",
            "status": "validated",
            "runnable": True,
            "modes": list(item["modes"]),
            "parameter_schema": deepcopy(item.get("parameter_schema", [])),
            "data_level": item.get("data_level", "KLINE"),
            "updated_at": None,
        }

    def _load(self) -> None:
        if self._state is None:
            return
        self._custom.clear()
        try:
            payload = self._state.load({"version": 1, "packages": []})
        except JsonStateError as error:
            raise RuntimeError("strategy package state is unreadable") from error
        records = payload.get("packages", []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            raise RuntimeError("strategy package state must contain an array")
        for record in records:
            if isinstance(record, dict) and str(record.get("package_id", "")).strip():
                self._custom[str(record["package_id"])] = deepcopy(record)

    def _persist_locked(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save({"version": 1, "packages": list(self._custom.values())})
        except JsonStateError as error:
            raise RuntimeError("strategy package state cannot be saved") from error
