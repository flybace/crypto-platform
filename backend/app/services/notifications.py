"""Small durable in-app notification service for the standalone runtime."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore


DEFAULT_CONFIG: dict[str, object] = {
    "enabled": False,
    "in_app_enabled": True,
    "system_sound_enabled": True,
    "event_preferences": {},
}


class NotificationService:
    """Persist only non-secret in-app notices; external channels are later work."""

    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._config = deepcopy(DEFAULT_CONFIG)
        self._items: list[dict[str, object]] = []
        self._lock = RLock()
        self._load()

    def config(self) -> dict[str, object]:
        with self._lock:
            self._load()
            return deepcopy(self._config)

    def update_config(self, values: dict[str, object]) -> dict[str, object]:
        with self._lock:
            self._load()
            next_config = deepcopy(self._config)
            next_config.update(values)
            if not isinstance(next_config.get("enabled"), bool) or not isinstance(next_config.get("in_app_enabled"), bool):
                raise ValueError("notification enabled flags must be boolean")
            if not isinstance(next_config.get("system_sound_enabled"), bool):
                raise ValueError("system_sound_enabled must be boolean")
            if not isinstance(next_config.get("event_preferences"), dict):
                raise ValueError("event_preferences must be an object")
            self._config = next_config
            self._persist_locked()
            return deepcopy(self._config)

    def create(
        self,
        *,
        title: str,
        message: str,
        event_type: str = "system",
        severity: str = "info",
        action_link: str | None = None,
    ) -> dict[str, object]:
        if severity not in {"info", "warning", "critical"}:
            raise ValueError("severity must be info, warning, or critical")
        now = datetime.now(UTC).isoformat()
        item = {
            "id": f"notification-{uuid4().hex}",
            "title": str(title).strip()[:160] or "系统通知",
            "message": str(message).strip()[:500],
            "event_type": str(event_type).strip()[:80] or "system",
            "severity": severity,
            "action_link": str(action_link).strip()[:200] if action_link else None,
            "read": False,
            "created_at": now,
        }
        with self._lock:
            self._load()
            self._items.insert(0, item)
            self._items = self._items[:200]
            self._persist_locked()
            return deepcopy(item)

    def items(self, *, unread_only: bool = False, limit: int = 50) -> list[dict[str, object]]:
        with self._lock:
            self._load()
            records = [item for item in self._items if not unread_only or not item.get("read")]
            return deepcopy(records[: max(1, min(int(limit), 200))])

    def mark_read(self, item_id: str) -> dict[str, object]:
        with self._lock:
            self._load()
            item = next((value for value in self._items if value.get("id") == str(item_id)), None)
            if item is None:
                raise KeyError(item_id)
            item["read"] = True
            self._persist_locked()
            return deepcopy(item)

    def mark_all_read(self) -> int:
        with self._lock:
            self._load()
            changed = 0
            for item in self._items:
                if not item.get("read"):
                    item["read"] = True
                    changed += 1
            if changed:
                self._persist_locked()
            return changed

    def summary(self, *, limit: int = 20) -> dict[str, object]:
        config = self.config()
        items = self.items(limit=limit)
        unread = sum(1 for item in self._items if not item.get("read"))
        enabled = bool(config.get("enabled")) and bool(config.get("in_app_enabled"))
        return {
            "module": "notifications",
            "status": "online" if enabled else "standby",
            "config": config,
            "readiness": {
                "enabled": bool(config.get("enabled")),
                "in_app_ready": enabled,
                "external_channels": "not_configured",
                "message": "仅提供独立站内通知，外部机器人通道尚未接入。",
            },
            "unread_count": unread,
            "items": items,
            "count": len(items),
        }

    def _load(self) -> None:
        if self._state is None:
            return
        self._items = []
        try:
            payload = self._state.load({"version": 1, "config": DEFAULT_CONFIG, "items": []})
        except JsonStateError as error:
            raise RuntimeError("notification state is unreadable") from error
        if not isinstance(payload, dict):
            raise RuntimeError("notification state must be an object")
        config = payload.get("config", DEFAULT_CONFIG)
        items = payload.get("items", [])
        if not isinstance(config, dict) or not isinstance(items, list):
            raise RuntimeError("notification state has invalid fields")
        next_config = deepcopy(DEFAULT_CONFIG)
        next_config.update(config)
        self._config = next_config
        self._items = [deepcopy(item) for item in items if isinstance(item, dict)]

    def _persist_locked(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save({"version": 1, "config": self._config, "items": self._items[:200]})
        except JsonStateError as error:
            raise RuntimeError("notification state cannot be saved") from error
