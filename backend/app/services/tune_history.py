"""Persistent history of parameter tuning runs.

Stores TuneResult summaries as JSON so users can review past tunings,
compare robust scores, and pick parameter sets to save as presets.
Follows the same JSON-file pattern as BacktestRunManager.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any


class TuneHistoryService:
    """Append-only JSON store for parameter tuning results."""

    def __init__(self, state_path: str | Path | None = None, *, limit: int = 50) -> None:
        self._path = Path(state_path) if state_path else None
        self._limit = max(1, int(limit))
        self._lock = RLock()
        self._items: list[dict[str, Any]] = []
        self._mtime: float | None = None
        self._load()

    def _reload_if_changed_locked(self) -> None:
        """Reload from disk if another process (e.g. task worker) updated the file."""
        if self._path is None or not self._path.exists():
            return
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return
        if self._mtime is not None and mtime <= self._mtime:
            return
        self._load()

    def save(self, result: dict[str, Any]) -> dict[str, Any]:
        """Persist one tuning result; returns the stored record."""
        record = deepcopy(result)
        record["saved_at"] = datetime.now(timezone.utc).isoformat()
        # Keep the payload bounded: store full ranked list but cap at 50 entries
        ranked = record.get("ranked")
        if isinstance(ranked, list) and len(ranked) > 50:
            record["ranked"] = ranked[:50]
            record["ranked_truncated"] = True
        with self._lock:
            self._items.insert(0, record)
            self._items = self._items[: self._limit]
            self._persist_locked()
            return deepcopy(self._items[0])

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            self._reload_if_changed_locked()
            return deepcopy(self._items[: max(1, min(int(limit), self._limit))])

    def get(self, tune_id: str) -> dict[str, Any] | None:
        tune_id = str(tune_id or "").strip()
        with self._lock:
            self._reload_if_changed_locked()
            for item in self._items:
                if str(item.get("tune_id")) == tune_id:
                    return deepcopy(item)
        return None

    def delete(self, tune_id: str) -> bool:
        tune_id = str(tune_id or "").strip()
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if str(i.get("tune_id")) != tune_id]
            if len(self._items) != before:
                self._persist_locked()
                return True
            return False

    def clear(self) -> int:
        with self._lock:
            count = len(self._items)
            self._items.clear()
            self._persist_locked()
            return count

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
