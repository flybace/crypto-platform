"""Small atomic JSON store for standalone development state."""

from __future__ import annotations

from copy import deepcopy
import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any, Protocol


class JsonStateError(RuntimeError):
    """The local state file cannot be trusted."""


class StateStore(Protocol):
    """Minimal state contract shared by file and relational adapters."""

    def load(self, default: Any) -> Any:
        ...

    def save(self, payload: Any) -> None:
        ...


class DomainSnapshotBackend(Protocol):
    """Relational backend required by ``SqlStateStore``."""

    def read_domain_snapshot(self, snapshot_key: str) -> dict[str, Any] | None:
        ...

    def write_domain_snapshot(self, snapshot_key: str, payload: Any) -> dict[str, Any]:
        ...


class JsonStateStore:
    """Persist one bounded state document without partially written files."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def load(self, default: Any) -> Any:
        with self._lock:
            if not self.path.exists():
                return deepcopy(default)
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise JsonStateError(f"state file is unreadable: {self.path.name}") from error

    def save(self, payload: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        with self._lock:
            try:
                with tempfile.NamedTemporaryFile(
                    dir=self.path.parent,
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    mode="w",
                    encoding="utf-8",
                    delete=False,
                ) as handle:
                    temp_name = handle.name
                    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, self.path)
            except (OSError, TypeError, ValueError) as error:
                raise JsonStateError(f"state file cannot be saved: {self.path.name}") from error
            finally:
                if temp_name:
                    try:
                        os.unlink(temp_name)
                    except FileNotFoundError:
                        pass


class SqlStateStore:
    """PostgreSQL-backed bounded state with a one-time JSON migration path.

    The relational record is authoritative. The optional JSON path is only
    used to bootstrap an absent record and to keep a best-effort recovery
    export after a successful database write.
    """

    def __init__(
        self,
        backend: DomainSnapshotBackend,
        snapshot_key: str,
        *,
        legacy_path: str | Path | None = None,
    ) -> None:
        key = str(snapshot_key).strip()
        if not key:
            raise ValueError("snapshot_key must not be empty")
        self._backend = backend
        self.snapshot_key = key[:160]
        self._legacy = JsonStateStore(legacy_path) if legacy_path is not None else None
        self._lock = RLock()

    def load(self, default: Any) -> Any:
        with self._lock:
            try:
                record = self._backend.read_domain_snapshot(self.snapshot_key)
            except Exception as error:
                raise JsonStateError(f"domain snapshot is unavailable: {self.snapshot_key}") from error
            if record is not None:
                return deepcopy(record.get("payload", default))
            if self._legacy is None or not self._legacy.path.exists():
                return deepcopy(default)
            try:
                payload = self._legacy.load(default)
                self._backend.write_domain_snapshot(self.snapshot_key, payload)
                return deepcopy(payload)
            except Exception as error:
                if isinstance(error, JsonStateError):
                    raise
                raise JsonStateError(f"domain snapshot migration failed: {self.snapshot_key}") from error

    def save(self, payload: Any) -> None:
        with self._lock:
            try:
                self._backend.write_domain_snapshot(self.snapshot_key, payload)
            except Exception as error:
                raise JsonStateError(f"domain snapshot cannot be saved: {self.snapshot_key}") from error
            if self._legacy is None:
                return
            try:
                self._legacy.save(payload)
            except JsonStateError:
                # A stale recovery export must not turn a committed database
                # write into a failed domain mutation.
                return
