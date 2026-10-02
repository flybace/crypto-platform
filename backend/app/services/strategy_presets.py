"""Named parameter presets for strategies.

Lets users save a tuned parameter set (e.g. the best result of a tuning
run) under a name, then reload it in the backtest center. Stored as JSON,
same pattern as TuneHistoryService.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4


class StrategyPresetService:
    """Named strategy parameter presets persisted as JSON."""

    def __init__(self, state_path: str | Path | None = None, *, limit: int = 100) -> None:
        self._path = Path(state_path) if state_path else None
        self._limit = max(1, int(limit))
        self._lock = RLock()
        self._items: list[dict[str, Any]] = []
        self._mtime: float | None = None
        self._load()

    def _reload_if_changed_locked(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return
        if self._mtime is not None and mtime <= self._mtime:
            return
        self._load()

    def save(
        self,
        *,
        strategy_id: str,
        name: str,
        parameters: dict[str, Any],
        venue_id: str = "",
        symbol: str = "",
        interval: str = "",
        tune_id: str = "",
        metrics: dict[str, Any] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        strategy_id = str(strategy_id or "").strip()
        name = str(name or "").strip()
        if not strategy_id:
            raise ValueError("strategy_id must not be empty")
        if not name:
            raise ValueError("preset name must not be empty")
        if not isinstance(parameters, dict) or not parameters:
            raise ValueError("parameters must be a non-empty object")
        record = {
            "preset_id": uuid4().hex,
            "strategy_id": strategy_id,
            "name": name[:80],
            "parameters": deepcopy(parameters),
            "venue_id": str(venue_id or ""),
            "symbol": str(symbol or ""),
            "interval": str(interval or ""),
            "tune_id": str(tune_id or ""),
            "metrics": deepcopy(metrics or {}),
            "note": str(note or "")[:300],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._items.insert(0, record)
            self._items = self._items[: self._limit]
            self._persist_locked()
            return deepcopy(self._items[0])

    def list(self, strategy_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            self._reload_if_changed_locked()
            items = self._items
            if strategy_id:
                sid = str(strategy_id).strip()
                items = [i for i in items if str(i.get("strategy_id")) == sid]
            return deepcopy(items[: max(1, min(int(limit), self._limit))])

    def get(self, preset_id: str) -> dict[str, Any] | None:
        preset_id = str(preset_id or "").strip()
        with self._lock:
            self._reload_if_changed_locked()
            for item in self._items:
                if str(item.get("preset_id")) == preset_id:
                    return deepcopy(item)
        return None

    def delete(self, preset_id: str) -> bool:
        preset_id = str(preset_id or "").strip()
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if str(i.get("preset_id")) != preset_id]
            if len(self._items) != before:
                self._persist_locked()
                return True
            return False

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            if self._path.exists():
                self._mtime = self._path.stat().st_mtime
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._items = [i for i in data if isinstance(i, dict)][: self._limit]
        except (OSError, ValueError):
            self._items = []

    def _persist_locked(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._items, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            self._mtime = self._path.stat().st_mtime
        except OSError:
            pass
