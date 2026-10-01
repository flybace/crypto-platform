"""Durable strategy management for the standalone research control plane."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore
from application.candle_backtest import strategy_catalog


SUPPORTED_MODES = frozenset({"research", "backtest", "paper", "sell-only"})


class StrategyRegistry:
    """Keep user-facing strategy switches separate from strategy code.

    The first release manages built-in strategy implementations only. This
    deliberately avoids pretending that an arbitrary user script is safe to
    execute; importing third-party code is a separate, audited capability.
    """

    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._overrides: dict[str, dict[str, Any]] = {}
        self._lock = RLock()
        self._load()

    def catalog(self, execution_mode: str = "DISABLED") -> list[dict[str, Any]]:
        with self._lock:
            self._load()
            return [self._definition(item, execution_mode) for item in strategy_catalog()]

    def management(self) -> list[dict[str, Any]]:
        with self._lock:
            self._load()
            return [self._management_item(item) for item in strategy_catalog()]

    def summary(self) -> dict[str, Any]:
        items = self.management()
        return {
            "strategy_count": len(items),
            "enabled_count": sum(1 for item in items if item["enabled"]),
            "backtest_enabled_count": sum(
                1 for item in items if item["enabled"] and "backtest" in item["modes"]
            ),
            "paper_enabled_count": sum(
                1 for item in items if item["enabled"] and "paper" in item["modes"]
            ),
            "items": items,
        }

    def get(self, strategy_id: str) -> dict[str, Any] | None:
        key = self._key(strategy_id)
        with self._lock:
            self._load()
            item = next((item for item in strategy_catalog() if item["strategy_id"] == key), None)
            return None if item is None else self._management_item(item)

    def assert_enabled(self, strategy_id: str, mode: str | None = None) -> None:
        item = self.get(strategy_id)
        if item is None:
            raise ValueError(f"unknown strategy: {strategy_id}")
        if not item["enabled"]:
            raise ValueError(f"strategy is disabled: {strategy_id}")
        if mode and mode not in item["modes"]:
            raise ValueError(f"strategy mode is not enabled: {strategy_id}/{mode}")

    def update(
        self,
        strategy_id: str,
        *,
        enabled: bool | None = None,
        modes: list[str] | None = None,
        default_parameters: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        key = self._key(strategy_id)
        with self._lock:
            self._load()
            base = next((item for item in strategy_catalog() if item["strategy_id"] == key), None)
            if base is None:
                raise KeyError(key)
            current = deepcopy(self._overrides.get(key, self._default_override(base)))
            if enabled is not None:
                current["enabled"] = bool(enabled)
            if modes is not None:
                current["modes"] = self._validate_modes(modes, base["modes"])
            if default_parameters is not None:
                current["default_parameters"] = self._validate_parameters(default_parameters, base)
            if note is not None:
                current["note"] = str(note).strip()[:240]
            current["updated_at"] = datetime.now(UTC).isoformat()
            self._overrides[key] = current
            self._persist_locked()
            return self._management_item(base)

    def reset(self, strategy_id: str) -> dict[str, Any]:
        key = self._key(strategy_id)
        with self._lock:
            self._load()
            base = next((item for item in strategy_catalog() if item["strategy_id"] == key), None)
            if base is None:
                raise KeyError(key)
            self._overrides.pop(key, None)
            self._persist_locked()
            return self._management_item(base)

    def _management_item(self, base: dict[str, Any]) -> dict[str, Any]:
        key = str(base["strategy_id"])
        override = self._overrides.get(key, self._default_override(base))
        return {
            "strategy_id": key,
            "name": base["name"],
            "description": base["description"],
            "version": base["version"],
            "available_modes": list(base["modes"]),
            "modes": list(override["modes"]),
            "enabled": bool(override["enabled"]),
            "default_parameters": deepcopy(override["default_parameters"]),
            "note": str(override.get("note", "")),
            "updated_at": override.get("updated_at"),
            "source": "builtin",
        }

    def _definition(self, base: dict[str, Any], execution_mode: str) -> dict[str, Any]:
        item = dict(base)
        managed = self._management_item(base)
        item.update(
            {
                "modes": managed["modes"],
                "enabled": managed["enabled"],
                "default_parameters": managed["default_parameters"],
                "management_note": managed["note"],
                "management_updated_at": managed["updated_at"],
                "execution_mode": execution_mode,
            }
        )
        return item

    @staticmethod
    def _default_override(base: dict[str, Any]) -> dict[str, Any]:
        return {
            "enabled": True,
            "modes": list(base["modes"]),
            "default_parameters": {
                str(field["key"]): field.get("default")
                for field in base.get("parameter_schema", [])
                if isinstance(field, dict) and field.get("key")
            },
            "note": "",
            "updated_at": None,
        }

    @staticmethod
    def _key(strategy_id: str) -> str:
        key = str(strategy_id).strip().lower()
        if not key:
            raise ValueError("strategy_id must not be empty")
        return key

    @staticmethod
    def _validate_modes(values: list[str], available: tuple[str, ...] | list[str]) -> list[str]:
        normalized = []
        for value in values:
            mode = str(value).strip().lower()
            if mode not in SUPPORTED_MODES:
                raise ValueError(f"unsupported strategy mode: {mode}")
            if mode not in available:
                raise ValueError(f"strategy does not support mode: {mode}")
            if mode not in normalized:
                normalized.append(mode)
        if not normalized:
            raise ValueError("strategy must retain at least one mode")
        return normalized

    @staticmethod
    def _validate_parameters(values: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            str(field["key"]): field
            for field in base.get("parameter_schema", [])
            if isinstance(field, dict) and field.get("key")
        }
        unknown = sorted(set(values) - set(allowed))
        if unknown:
            raise ValueError(f"unknown strategy parameters: {', '.join(unknown)}")
        return deepcopy(values)

    def _load(self) -> None:
        if self._state is None:
            return
        self._overrides.clear()
        try:
            payload = self._state.load({"version": 1, "strategies": {}})
        except JsonStateError as error:
            raise RuntimeError("strategy registry state is unreadable") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("strategies", {}), dict):
            raise RuntimeError("strategy registry state must contain an object")
        known = {str(item["strategy_id"]): item for item in strategy_catalog()}
        for key, value in payload["strategies"].items():
            if key in known and isinstance(value, dict):
                self._overrides[key] = deepcopy(value)

    def _persist_locked(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save({"version": 1, "strategies": self._overrides})
        except JsonStateError as error:
            raise RuntimeError("strategy registry state cannot be saved") from error
