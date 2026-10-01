"""Durable status for the automatic public-history scheduler."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore


STATE_VERSION = 1


def default_history_scheduler_state(interval_seconds: int) -> dict[str, object]:
    """Return the safe initial state used before the first scheduler tick."""
    return {
        "version": STATE_VERSION,
        "automatic": True,
        "interval_seconds": int(interval_seconds),
        "status": "not_started",
        "last_run_at": None,
        "last_result": None,
        "last_error": None,
        "last_plan_id": None,
        "last_job_id": None,
        "last_job_status": None,
        "last_failure_job_id": None,
        "last_failure_status": None,
        "consecutive_failures": 0,
        "next_attempt_at": None,
        "updated_at": None,
    }


class HistorySchedulerState:
    """Atomically persist scheduler lifecycle state shared with the API."""

    def __init__(
        self,
        path: str | Path,
        *,
        interval_seconds: int,
        state_store: StateStore | None = None,
    ) -> None:
        if int(interval_seconds) <= 0:
            raise ValueError("scheduler interval must be positive")
        self.path = Path(path)
        self._store = state_store if state_store is not None else JsonStateStore(self.path)
        self._lock = RLock()
        self._interval_seconds = int(interval_seconds)
        self._state = self._load(self._interval_seconds)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            self._state = self._load(self._interval_seconds)
            return deepcopy(self._state)

    def patch(self, **values: object) -> dict[str, object]:
        with self._lock:
            self._state = self._load(self._interval_seconds)
            self._state.update(values)
            self._state["version"] = STATE_VERSION
            self._state["automatic"] = True
            self._state["updated_at"] = datetime.now(UTC).isoformat()
            self._save_locked()
            return deepcopy(self._state)

    def _load(self, interval_seconds: int) -> dict[str, object]:
        try:
            payload = self._store.load(default_history_scheduler_state(interval_seconds))
        except JsonStateError as error:
            raise RuntimeError("history scheduler state is unreadable") from error
        if not isinstance(payload, dict):
            raise RuntimeError("history scheduler state must be an object")
        state = default_history_scheduler_state(interval_seconds)
        state.update({key: value for key, value in payload.items() if key in state})
        state["interval_seconds"] = interval_seconds
        try:
            state["consecutive_failures"] = max(0, int(state.get("consecutive_failures", 0) or 0))
        except (TypeError, ValueError) as error:
            raise RuntimeError("history scheduler failure count is invalid") from error
        return state

    def _save_locked(self) -> None:
        try:
            self._store.save(self._state)
        except JsonStateError as error:
            raise RuntimeError("history scheduler state cannot be saved") from error


def scheduler_status_from_file(path: str | Path, *, interval_seconds: int) -> dict[str, object]:
    """Read scheduler state for a request without exposing filesystem details."""
    return HistorySchedulerState(path, interval_seconds=interval_seconds).snapshot()
