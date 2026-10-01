"""Factories for PostgreSQL-backed application state snapshots."""

from __future__ import annotations

from pathlib import Path

from adapters.standalone.state_store import SqlStateStore

from .task_store import TaskStore


def build_domain_state(
    task_store: TaskStore,
    runtime_root: str | Path,
    name: str,
) -> SqlStateStore:
    """Create one domain-namespaced SQL state adapter with a JSON bridge."""
    return build_snapshot_state(task_store, runtime_root, name, namespace="domain")


def build_runtime_state(
    task_store: TaskStore,
    runtime_root: str | Path,
    name: str,
) -> SqlStateStore:
    """Create one runtime-namespaced SQL state adapter with a JSON bridge."""
    return build_snapshot_state(task_store, runtime_root, name, namespace="runtime")


def build_snapshot_state(
    task_store: TaskStore,
    runtime_root: str | Path,
    name: str,
    *,
    namespace: str,
) -> SqlStateStore:
    """Create a bounded SQL snapshot adapter for a simple JSON legacy name."""
    root = Path(runtime_root)
    filename = str(name).strip()
    if not filename or Path(filename).name != filename:
        raise ValueError("snapshot state name must be a simple filename")
    prefix = str(namespace).strip().lower()
    if not prefix or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in prefix):
        raise ValueError("snapshot namespace is invalid")
    return SqlStateStore(
        task_store,
        f"crypto.{prefix}.{Path(filename).stem}",
        legacy_path=root / filename,
    )
