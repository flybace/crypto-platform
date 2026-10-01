"""Inventory, backup, restore, and retention boundaries for task logs."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
from typing import Any

from .task_log_archive import CONTRACT_VERSION, TaskLogArchive, TaskLogArchiveError


LIFECYCLE_CONTRACT_VERSION = "task-log-lifecycle-v1"
BACKUP_CONTRACT_VERSION = "task-log-backup-v1"
PURGE_CONFIRMATION = "PURGE_TASK_LOG_ARCHIVE"
TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "blocked", "interrupted", "partial", "dead_lettered"}
)
MAX_BACKUP_FILES = 200_000
MAX_BACKUP_BYTES = 512 * 1024 * 1024
_MANIFEST_NAME = "manifest.json"
_MANIFEST_LOCK_NAME = ".manifest.lock"
_MANIFEST_KEY = re.compile(r"^(?P<shard>[0-9a-f]{2})/(?P<task>[0-9a-f]{64})/manifest\.json$")
_EVENT_KEY = re.compile(
    r"^(?P<shard>[0-9a-f]{2})/(?P<task>[0-9a-f]{64})/"
    r"(?P<event>[0-9]{20})-(?P<digest>[0-9a-f]{64})\.json$"
)


class TaskLogLifecycleError(RuntimeError):
    """Raised when a task-log lifecycle operation cannot be trusted."""


def resolve_task_log_lifecycle_path(
    configured: str | Path | None,
    project_root: str | Path,
) -> Path | None:
    """Resolve an operator-supplied lifecycle path without choosing a fallback."""
    raw = str(configured or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else Path(project_root) / path


class TaskLogArchiveLifecycle:
    """Operate on a task-log archive without coupling it to SQL internals."""

    def __init__(
        self,
        archive: TaskLogArchive | str | Path,
        *,
        retention_days: int = 365,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.archive = archive if isinstance(archive, TaskLogArchive) else TaskLogArchive(archive)
        if int(retention_days) <= 0 or int(retention_days) > 3650:
            raise ValueError("retention_days must be between 1 and 3650")
        self.retention_days = int(retention_days)
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def root(self) -> Path:
        return self.archive.root

    def inspect(
        self,
        *,
        verify_objects: bool = True,
        include_tasks: bool = True,
        include_files: bool = False,
        limit: int = 10_000,
    ) -> dict[str, object]:
        """Return a bounded inventory and integrity state for the archive root."""
        bounded_limit = max(1, min(int(limit), MAX_BACKUP_FILES))
        if self.root.is_symlink() or (self.root.exists() and not self.root.is_dir()):
            raise TaskLogLifecycleError("task log archive root is not a directory")
        if not self.root.exists():
            return self._empty_inventory()
        files, task_dirs, counters = self._scan_files()

        manifests_by_digest: dict[str, tuple[str, Path]] = {}
        tasks: list[dict[str, object]] = []
        corrupt_count = 0
        missing_manifest_count = 0
        referenced_object_keys: set[str] = set()

        for relative, path in files:
            match = _MANIFEST_KEY.fullmatch(relative)
            if match is None:
                continue
            digest = match.group("task")
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                task_id = str(raw.get("task_id", "")).strip() if isinstance(raw, dict) else ""
                expected_digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
                if not task_id or digest != expected_digest or match.group("shard") != digest[:2]:
                    raise TaskLogLifecycleError("task log manifest task identity mismatch")
                manifests_by_digest[digest] = (task_id, path)
                manifest = self.archive.read_manifest(task_id, verify_objects=verify_objects)
                if manifest is None:
                    raise TaskLogLifecycleError("task log manifest is unavailable")
                references = manifest.get("events") if isinstance(manifest, dict) else None
                if not isinstance(references, list):
                    raise TaskLogLifecycleError("task log manifest events are invalid")
                declared_keys = {str(item.get("key", "")) for item in references if isinstance(item, dict)}
                referenced_object_keys.update(declared_keys)
                task_files = [
                    (key, candidate)
                    for key, candidate in files
                    if _same_task_digest(key, digest) and _EVENT_KEY.fullmatch(key)
                ]
                if verify_objects:
                    for key, candidate in task_files:
                        self._verify_event_file(key, candidate, expected_task_id=task_id)
                orphan_count = len({key for key, _ in task_files} - declared_keys)
                task_bytes = sum(candidate.stat().st_size for _, candidate in task_files) + path.stat().st_size
                tasks.append(
                    {
                        "task_id": task_id,
                        "task_digest": digest,
                        "state": "READY",
                        "event_count": int(manifest.get("event_count", len(references))),
                        "object_count": len(task_files),
                        "orphan_object_count": orphan_count,
                        "bytes": task_bytes,
                        "manifest_sha256": _sha256_file(path),
                    }
                )
            except (OSError, TaskLogArchiveError, TaskLogLifecycleError, TypeError, ValueError, json.JSONDecodeError) as error:
                corrupt_count += 1
                tasks.append(
                    {
                        "task_digest": digest,
                        "state": "CORRUPT",
                        "event_count": 0,
                        "object_count": 0,
                        "orphan_object_count": 0,
                        "bytes": path.stat().st_size if path.exists() else 0,
                        "error": str(error)[:240],
                    }
                )

        for digest in sorted(task_dirs - set(manifests_by_digest)):
            object_paths = [
                (key, path)
                for key, path in files
                if _same_task_digest(key, digest) and _EVENT_KEY.fullmatch(key)
            ]
            if object_paths:
                missing_manifest_count += 1
                tasks.append(
                    {
                        "task_digest": digest,
                        "state": "MISSING_MANIFEST",
                        "event_count": 0,
                        "object_count": len(object_paths),
                        "orphan_object_count": len(object_paths),
                        "bytes": sum(path.stat().st_size for _, path in object_paths),
                        "error": "task log archive manifest is unavailable",
                    }
                )

        if verify_objects:
            for relative, path in files:
                if _EVENT_KEY.fullmatch(relative) is None or relative in referenced_object_keys:
                    continue
                try:
                    self._verify_event_file(relative, path)
                except (OSError, TaskLogArchiveError, TaskLogLifecycleError, TypeError, ValueError) as error:
                    corrupt_count += 1
                    counters["corrupt_object_count"] += 1
                    counters.setdefault("errors", []).append(str(error)[:240])

        tasks.sort(key=lambda item: (str(item.get("task_id", "")), str(item.get("task_digest", ""))))
        visible_tasks = tasks[:bounded_limit]
        orphan_count = sum(int(item.get("orphan_object_count", 0) or 0) for item in tasks)
        backup_ready = (
            corrupt_count == 0
            and missing_manifest_count == 0
            and counters["unexpected_file_count"] == 0
            and counters["transient_file_count"] == 0
            and orphan_count == 0
        )
        if corrupt_count or missing_manifest_count or counters["unexpected_file_count"] or counters["transient_file_count"]:
            status = "CORRUPT"
        elif orphan_count:
            status = "DEGRADED"
        else:
            status = "READY"
        result: dict[str, object] = {
            "contract_version": LIFECYCLE_CONTRACT_VERSION,
            "archive_contract_version": CONTRACT_VERSION,
            "status": status,
            "ok": backup_ready,
            "backup_ready": backup_ready,
            "root": str(self.root),
            "task_count": len(tasks),
            "event_object_count": sum(
                1 for relative, _ in files if _EVENT_KEY.fullmatch(relative) is not None
            ),
            "manifest_count": len(manifests_by_digest),
            "bytes": sum(path.stat().st_size for _, path in files),
            "orphan_object_count": orphan_count,
            "corrupt_count": corrupt_count,
            "missing_manifest_count": missing_manifest_count,
            "unexpected_file_count": counters["unexpected_file_count"],
            "transient_file_count": counters["transient_file_count"],
            "lock_file_count": counters["lock_file_count"],
            "inventory_sha256": self._inventory_digest(files),
            "retention_policy": self._retention_policy(),
        }
        if counters.get("errors"):
            result["errors"] = list(counters["errors"])[:10]
        if include_tasks:
            result["tasks"] = visible_tasks
            result["returned_task_count"] = len(visible_tasks)
        if include_files:
            result["files"] = [
                {
                    "key": relative,
                    "sha256": _sha256_file(path),
                    "bytes": path.stat().st_size,
                    "kind": "manifest" if _MANIFEST_KEY.fullmatch(relative) else "event",
                }
                for relative, path in files
                if _MANIFEST_KEY.fullmatch(relative) or _EVENT_KEY.fullmatch(relative)
            ]
        return result

    def export_backup(
        self,
        destination: str | Path,
        *,
        replace: bool = False,
    ) -> dict[str, object]:
        """Export a verified archive to an atomic, portable tar backup."""
        inventory = self.inspect(verify_objects=True, include_tasks=True, include_files=True)
        if not bool(inventory.get("backup_ready")):
            raise TaskLogLifecycleError("task log archive is not safe to back up")
        destination_path = Path(destination)
        try:
            destination_path.resolve().relative_to(self.root.resolve())
        except ValueError:
            pass
        else:
            raise TaskLogLifecycleError("task log backup destination must be outside the archive root")
        if destination_path.exists() and not replace:
            raise TaskLogLifecycleError("task log backup destination already exists")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        entries = inventory.get("files") if isinstance(inventory.get("files"), list) else []
        manifest_body: dict[str, object] = {
            "contract_version": BACKUP_CONTRACT_VERSION,
            "archive_contract_version": CONTRACT_VERSION,
            "created_at": self._now().isoformat(),
            "source_inventory_sha256": inventory["inventory_sha256"],
            "task_count": inventory["task_count"],
            "event_object_count": inventory["event_object_count"],
            "orphan_object_count": inventory["orphan_object_count"],
            "files": entries,
        }
        manifest = {
            **manifest_body,
            "manifest_sha256": hashlib.sha256(_canonical_bytes(manifest_body)).hexdigest(),
        }
        manifest_bytes = _canonical_bytes(manifest)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination_path.parent,
                prefix=f".{destination_path.name}.",
                suffix=".tmp",
                mode="wb",
                delete=False,
            ) as handle:
                temp_name = handle.name
                with tarfile.open(fileobj=handle, mode="w") as bundle:
                    _add_tar_bytes(bundle, "backup-manifest.json", manifest_bytes)
                    for entry in entries:
                        if not isinstance(entry, Mapping):
                            raise TaskLogLifecycleError("task log backup file entry is invalid")
                        key = str(entry.get("key", ""))
                        source = self._safe_archive_file(key)
                        _add_tar_bytes(bundle, key, source.read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
            if destination_path.exists() and not replace:
                raise TaskLogLifecycleError("task log backup destination already exists")
            os.replace(temp_name, destination_path)
            temp_name = None
        except (OSError, tarfile.TarError, TypeError, ValueError) as error:
            raise TaskLogLifecycleError("unable to export task log backup") from error
        finally:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass
        _restrict_file(destination_path)
        verified = self.verify_backup(destination_path)
        return {
            "contract_version": BACKUP_CONTRACT_VERSION,
            "state": "READY",
            "path": str(destination_path),
            "sha256": _sha256_file(destination_path),
            "bytes": destination_path.stat().st_size,
            "inventory_sha256": verified["inventory_sha256"],
            "task_count": verified["task_count"],
            "file_count": verified["file_count"],
            "orphan_object_count": verified["orphan_object_count"],
        }

    def verify_backup(self, backup: str | Path) -> dict[str, object]:
        """Verify tar member safety, manifest integrity, and every file digest."""
        manifest, contents = self._read_backup(Path(backup))
        if manifest.get("archive_contract_version") != CONTRACT_VERSION:
            raise TaskLogLifecycleError("task log backup archive contract is unsupported")
        files = manifest.get("files")
        if not isinstance(files, list):
            raise TaskLogLifecycleError("task log backup file list is invalid")
        expected_entries = [_normalize_backup_entry(item) for item in files]
        if expected_entries != sorted(expected_entries, key=lambda item: str(item["key"])):
            raise TaskLogLifecycleError("task log backup file list is not canonical")
        expected_keys = {str(item["key"]) for item in expected_entries}
        if len(expected_keys) != len(expected_entries) or set(contents) != expected_keys:
            raise TaskLogLifecycleError("task log backup members do not match its manifest")
        for entry in expected_entries:
            key = str(entry["key"])
            encoded = contents[key]
            if len(encoded) != int(entry["bytes"]):
                raise TaskLogLifecycleError("task log backup member size mismatch")
            if hashlib.sha256(encoded).hexdigest() != str(entry["sha256"]):
                raise TaskLogLifecycleError("task log backup member checksum mismatch")
        manifest_count = sum(1 for entry in expected_entries if entry["kind"] == "manifest")
        event_count = sum(1 for entry in expected_entries if entry["kind"] == "event")
        if int(manifest.get("task_count", -1)) != manifest_count:
            raise TaskLogLifecycleError("task log backup task count mismatch")
        if int(manifest.get("event_object_count", -1)) != event_count:
            raise TaskLogLifecycleError("task log backup event count mismatch")
        if int(manifest.get("orphan_object_count", -1)) != 0:
            raise TaskLogLifecycleError("task log backup contains orphan objects")
        inventory_digest = _entries_digest(expected_entries)
        if str(manifest.get("source_inventory_sha256", "")) != inventory_digest:
            raise TaskLogLifecycleError("task log backup inventory checksum mismatch")
        return {
            "contract_version": BACKUP_CONTRACT_VERSION,
            "state": "READY",
            "path": str(Path(backup)),
            "inventory_sha256": inventory_digest,
            "task_count": int(manifest.get("task_count", 0) or 0),
            "event_object_count": int(manifest.get("event_object_count", 0) or 0),
            "orphan_object_count": int(manifest.get("orphan_object_count", 0) or 0),
            "file_count": len(expected_entries),
            "bytes": sum(len(value) for value in contents.values()),
        }

    def restore_backup(
        self,
        backup: str | Path,
        target_root: str | Path,
    ) -> dict[str, object]:
        """Restore into a new directory and verify it before publishing it."""
        backup_path = Path(backup)
        verified = self.verify_backup(backup_path)
        target = Path(target_root)
        if target.is_symlink() or target.exists():
            raise TaskLogLifecycleError("restore target already exists")
        try:
            target.resolve().relative_to(self.root.resolve())
        except ValueError:
            pass
        else:
            raise TaskLogLifecycleError("restore target must be outside the archive root")
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.restore-", dir=target.parent))
        try:
            _, contents = self._read_backup(backup_path)
            for key, encoded in contents.items():
                path = _safe_relative_path(stage, key)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(encoded)
                _restrict_file(path)
            restored = TaskLogArchiveLifecycle(
                stage,
                retention_days=self.retention_days,
                clock=self._clock,
            ).inspect(verify_objects=True, include_tasks=True)
            if not bool(restored.get("backup_ready")):
                raise TaskLogLifecycleError("restored task log archive did not verify")
            if restored.get("inventory_sha256") != verified.get("inventory_sha256"):
                raise TaskLogLifecycleError("restored task log archive inventory differs")
            os.replace(stage, target)
            stage = None  # type: ignore[assignment]
        except (OSError, TaskLogArchiveError, TaskLogLifecycleError, TypeError, ValueError) as error:
            raise TaskLogLifecycleError("unable to restore task log backup") from error
        finally:
            if stage is not None:
                shutil.rmtree(stage, ignore_errors=True)
        return {
            "contract_version": BACKUP_CONTRACT_VERSION,
            "state": "READY",
            "backup": verified,
            "target_root": str(target),
        }

    def plan_retention(
        self,
        task_records: Iterable[Mapping[str, object]],
        *,
        now: datetime | None = None,
        limit: int = 1000,
    ) -> dict[str, object]:
        """Plan terminal-task removal without changing the archive."""
        inventory = self.inspect(verify_objects=True, include_tasks=True)
        if not bool(inventory.get("backup_ready")):
            raise TaskLogLifecycleError("cannot plan retention for an unverified archive")
        current = now or self._now()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        records = {
            str(record.get("task_id", "")).strip(): record
            for record in task_records
            if isinstance(record, Mapping) and str(record.get("task_id", "")).strip()
        }
        cutoff = (
            current.astimezone(UTC) - timedelta(days=self.retention_days)
        ).timestamp()
        candidates: list[dict[str, object]] = []
        skipped = {"missing_task_record": 0, "active": 0, "missing_finished_at": 0, "too_recent": 0}
        for item in inventory.get("tasks", []):
            if not isinstance(item, Mapping) or str(item.get("state")) != "READY":
                continue
            task_id = str(item.get("task_id", "")).strip()
            record = records.get(task_id)
            if record is None:
                skipped["missing_task_record"] += 1
                continue
            status = str(record.get("status", "")).strip().lower()
            if status not in TERMINAL_STATUSES:
                skipped["active"] += 1
                continue
            finished_at = _parse_timestamp(record.get("finished_at") or record.get("updated_at"))
            if finished_at is None:
                skipped["missing_finished_at"] += 1
                continue
            if finished_at > cutoff:
                skipped["too_recent"] += 1
                continue
            candidates.append(
                {
                    "task_id": task_id,
                    "task_digest": item.get("task_digest"),
                    "manifest_sha256": item.get("manifest_sha256"),
                    "event_count": item.get("event_count", 0),
                    "bytes": item.get("bytes", 0),
                    "status": status,
                    "finished_at": datetime.fromtimestamp(finished_at, tz=UTC).isoformat(),
                    "eligible_after": datetime.fromtimestamp(
                        finished_at + self.retention_days * 86400,
                        tz=UTC,
                    ).isoformat(),
                }
            )
            if len(candidates) >= max(1, min(int(limit), 10_000)):
                break
        body: dict[str, object] = {
            "contract_version": LIFECYCLE_CONTRACT_VERSION,
            "created_at": current.astimezone(UTC).isoformat(),
            "retention_days": self.retention_days,
            "inventory_sha256": inventory["inventory_sha256"],
            "candidates": candidates,
            "skipped": skipped,
        }
        return {
            **body,
            "plan_id": hashlib.sha256(_canonical_bytes(body)).hexdigest(),
            "state": "PLANNED",
            "candidate_count": len(candidates),
        }

    def purge_retention_plan(
        self,
        plan: Mapping[str, object],
        *,
        backup: str | Path,
        confirmation: str,
    ) -> dict[str, object]:
        """Purge exact planned task directories only after backup verification."""
        if confirmation != PURGE_CONFIRMATION:
            raise TaskLogLifecycleError("retention purge requires explicit confirmation")
        if not isinstance(plan, Mapping) or str(plan.get("state")) != "PLANNED":
            raise TaskLogLifecycleError("retention plan is invalid")
        body = {key: plan[key] for key in ("contract_version", "created_at", "retention_days", "inventory_sha256", "candidates", "skipped") if key in plan}
        expected_plan_id = hashlib.sha256(_canonical_bytes(body)).hexdigest()
        if str(plan.get("plan_id")) != expected_plan_id:
            raise TaskLogLifecycleError("retention plan checksum mismatch")
        backup_status = self.verify_backup(backup)
        if backup_status.get("inventory_sha256") != plan.get("inventory_sha256"):
            raise TaskLogLifecycleError("retention backup does not match the plan inventory")
        inventory = self.inspect(verify_objects=True, include_tasks=True)
        if inventory.get("inventory_sha256") != plan.get("inventory_sha256"):
            raise TaskLogLifecycleError("task log archive changed after retention planning")
        candidates = plan.get("candidates") if isinstance(plan.get("candidates"), list) else []
        directories: list[tuple[str, Path]] = []
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                raise TaskLogLifecycleError("retention candidate is invalid")
            task_id = str(candidate.get("task_id", "")).strip()
            digest = str(candidate.get("task_digest", "")).strip()
            if not task_id or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise TaskLogLifecycleError("retention candidate identity is invalid")
            expected_digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
            if digest != expected_digest:
                raise TaskLogLifecycleError("retention candidate task identity mismatch")
            directory = _safe_relative_path(self.root, f"{digest[:2]}/{digest}")
            if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                raise TaskLogLifecycleError("retention candidate path is unsafe")
            directories.append((task_id, directory))
        removed: list[str] = []
        for task_id, directory in directories:
            if directory.exists():
                shutil.rmtree(directory)
                removed.append(task_id)
        return {
            "contract_version": LIFECYCLE_CONTRACT_VERSION,
            "state": "PURGED",
            "plan_id": plan.get("plan_id"),
            "backup_inventory_sha256": backup_status.get("inventory_sha256"),
            "removed_task_ids": removed,
            "inventory": self.inspect(verify_objects=True, include_tasks=False),
        }

    def _scan_files(self) -> tuple[list[tuple[str, Path]], set[str], dict[str, object]]:
        if not self.root.exists():
            return [], set(), _empty_counters()
        files: list[tuple[str, Path]] = []
        task_dirs: set[str] = set()
        counters = _empty_counters()
        for path in sorted(self.root.rglob("*")):
            if path.is_symlink():
                counters["unexpected_file_count"] += 1
                continue
            if path.is_dir():
                continue
            relative = path.relative_to(self.root).as_posix()
            parts = PurePosixPath(relative).parts
            if len(parts) >= 2 and re.fullmatch(r"[0-9a-f]{64}", parts[1]):
                task_dirs.add(parts[1])
            if path.name == _MANIFEST_LOCK_NAME:
                counters["lock_file_count"] += 1
                continue
            if path.name.endswith(".tmp"):
                counters["transient_file_count"] += 1
                continue
            if _MANIFEST_KEY.fullmatch(relative) or _EVENT_KEY.fullmatch(relative):
                files.append((relative, path))
            else:
                counters["unexpected_file_count"] += 1
        return files, task_dirs, counters

    def _verify_event_file(
        self,
        relative: str,
        path: Path,
        *,
        expected_task_id: str | None = None,
    ) -> None:
        match = _EVENT_KEY.fullmatch(relative)
        if match is None or match.group("shard") != match.group("task")[:2]:
            raise TaskLogLifecycleError("task log event key is invalid")
        encoded = path.read_bytes()
        digest = hashlib.sha256(encoded).hexdigest()
        if digest != match.group("digest"):
            raise TaskLogLifecycleError("task log event checksum mismatch")
        try:
            document = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise TaskLogLifecycleError("task log event JSON is invalid") from error
        if not isinstance(document, Mapping):
            raise TaskLogLifecycleError("task log event document is invalid")
        task_id = str(document.get("task_id", "")).strip()
        if not task_id or hashlib.sha256(task_id.encode("utf-8")).hexdigest() != match.group("task"):
            raise TaskLogLifecycleError("task log event task identity mismatch")
        if expected_task_id is not None and task_id != expected_task_id:
            raise TaskLogLifecycleError("task log event task identity mismatch")
        event_id = int(document.get("event_id", 0) or 0)
        if event_id != int(match.group("event")):
            raise TaskLogLifecycleError("task log event id mismatch")
        self.archive.read(
            {
                "contract_version": CONTRACT_VERSION,
                "key": relative,
                "sha256": digest,
                "bytes": len(encoded),
                "event_id": event_id,
                "state": "READY",
            },
            expected_task_id=task_id,
            expected_event_id=event_id,
        )

    def _safe_archive_file(self, key: str) -> Path:
        if _MANIFEST_KEY.fullmatch(key) is None and _EVENT_KEY.fullmatch(key) is None:
            raise TaskLogLifecycleError("task log backup contains an invalid archive key")
        path = _safe_relative_path(self.root, key)
        if not path.is_file() or path.is_symlink():
            raise TaskLogLifecycleError("task log archive object is unavailable")
        return path

    def _read_backup(self, backup: Path) -> tuple[dict[str, object], dict[str, bytes]]:
        if not backup.is_file() or backup.is_symlink():
            raise TaskLogLifecycleError("task log backup is unavailable")
        try:
            with tarfile.open(backup, mode="r:*") as bundle:
                members = bundle.getmembers()
                if len(members) > MAX_BACKUP_FILES + 1:
                    raise TaskLogLifecycleError("task log backup contains too many files")
                names: set[str] = set()
                contents: dict[str, bytes] = {}
                total_bytes = 0
                for member in members:
                    name = _safe_tar_name(member.name)
                    if name in names:
                        raise TaskLogLifecycleError("task log backup contains duplicate members")
                    names.add(name)
                    if not member.isfile():
                        raise TaskLogLifecycleError("task log backup contains a non-file member")
                    if member.size < 0 or member.size > MAX_BACKUP_BYTES:
                        raise TaskLogLifecycleError("task log backup member is too large")
                    total_bytes += member.size
                    if total_bytes > MAX_BACKUP_BYTES:
                        raise TaskLogLifecycleError("task log backup is too large")
                    handle = bundle.extractfile(member)
                    if handle is None:
                        raise TaskLogLifecycleError("task log backup member cannot be read")
                    contents[name] = handle.read()
        except (OSError, tarfile.TarError, EOFError) as error:
            raise TaskLogLifecycleError("task log backup is invalid") from error
        encoded_manifest = contents.pop("backup-manifest.json", None)
        if encoded_manifest is None:
            raise TaskLogLifecycleError("task log backup manifest is missing")
        try:
            manifest = json.loads(encoded_manifest.decode("utf-8"))
        except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise TaskLogLifecycleError("task log backup manifest is invalid") from error
        if not isinstance(manifest, dict) or manifest.get("contract_version") != BACKUP_CONTRACT_VERSION:
            raise TaskLogLifecycleError("task log backup contract is unsupported")
        expected_digest = str(manifest.get("manifest_sha256", "")).strip().lower()
        body = dict(manifest)
        body.pop("manifest_sha256", None)
        if expected_digest != hashlib.sha256(_canonical_bytes(body)).hexdigest():
            raise TaskLogLifecycleError("task log backup manifest checksum mismatch")
        return manifest, contents

    def _inventory_digest(self, files: Iterable[tuple[str, Path]]) -> str:
        entries = [
            {
                "key": relative,
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
                "kind": "manifest" if _MANIFEST_KEY.fullmatch(relative) else "event",
            }
            for relative, path in files
            if _MANIFEST_KEY.fullmatch(relative) or _EVENT_KEY.fullmatch(relative)
        ]
        return _entries_digest(entries)

    def _retention_policy(self) -> dict[str, object]:
        return {
            "contract_version": "task-log-retention-v1",
            "retention_days": self.retention_days,
            "eligible_statuses": sorted(TERMINAL_STATUSES),
            "requires_finished_at": True,
            "automatic_purge": False,
            "purge_confirmation": PURGE_CONFIRMATION,
        }

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value

    def _empty_inventory(self) -> dict[str, object]:
        return {
            "contract_version": LIFECYCLE_CONTRACT_VERSION,
            "archive_contract_version": CONTRACT_VERSION,
            "status": "EMPTY",
            "ok": True,
            "backup_ready": True,
            "root": str(self.root),
            "task_count": 0,
            "event_object_count": 0,
            "manifest_count": 0,
            "bytes": 0,
            "orphan_object_count": 0,
            "corrupt_count": 0,
            "missing_manifest_count": 0,
            "unexpected_file_count": 0,
            "transient_file_count": 0,
            "lock_file_count": 0,
            "inventory_sha256": _entries_digest([]),
            "retention_policy": self._retention_policy(),
            "tasks": [],
            "returned_task_count": 0,
        }


def _empty_counters() -> dict[str, object]:
    return {
        "unexpected_file_count": 0,
        "transient_file_count": 0,
        "lock_file_count": 0,
        "corrupt_object_count": 0,
        "errors": [],
    }


def _same_task_digest(relative: str, digest: str) -> bool:
    parts = PurePosixPath(relative).parts
    return len(parts) >= 2 and parts[1] == digest and parts[0] == digest[:2]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _entries_digest(entries: Iterable[Mapping[str, object]]) -> str:
    normalized = [
        {
            "key": str(entry.get("key", "")),
            "sha256": str(entry.get("sha256", "")).lower(),
            "bytes": int(entry.get("bytes", 0) or 0),
            "kind": str(entry.get("kind", "")),
        }
        for entry in entries
    ]
    normalized.sort(key=lambda item: str(item["key"]))
    return hashlib.sha256(_canonical_bytes(normalized)).hexdigest()


def _normalize_backup_entry(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TaskLogLifecycleError("task log backup file entry is invalid")
    key = str(value.get("key", ""))
    if _MANIFEST_KEY.fullmatch(key) is None and _EVENT_KEY.fullmatch(key) is None:
        raise TaskLogLifecycleError("task log backup file key is invalid")
    digest = str(value.get("sha256", "")).strip().lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise TaskLogLifecycleError("task log backup file checksum is invalid")
    try:
        size = int(value.get("bytes", -1))
    except (TypeError, ValueError) as error:
        raise TaskLogLifecycleError("task log backup file size is invalid") from error
    if size < 0:
        raise TaskLogLifecycleError("task log backup file size is invalid")
    kind = str(value.get("kind", ""))
    if kind not in {"manifest", "event"}:
        raise TaskLogLifecycleError("task log backup file kind is invalid")
    expected_kind = "manifest" if _MANIFEST_KEY.fullmatch(key) else "event"
    if kind != expected_kind:
        raise TaskLogLifecycleError("task log backup file kind does not match its key")
    return {"key": key, "sha256": digest, "bytes": size, "kind": kind}


def _safe_tar_name(name: str) -> str:
    normalized = PurePosixPath(str(name))
    if normalized.is_absolute() or normalized.as_posix() != str(name) or ".." in normalized.parts:
        raise TaskLogLifecycleError("task log backup contains an unsafe path")
    if normalized.as_posix() == "backup-manifest.json":
        return normalized.as_posix()
    if _MANIFEST_KEY.fullmatch(normalized.as_posix()) is None and _EVENT_KEY.fullmatch(normalized.as_posix()) is None:
        raise TaskLogLifecycleError("task log backup contains an unknown path")
    return normalized.as_posix()


def _safe_relative_path(root: Path, relative: str) -> Path:
    normalized = PurePosixPath(relative)
    if normalized.is_absolute() or ".." in normalized.parts or not relative:
        raise TaskLogLifecycleError("task log path escaped its root")
    candidate = (root / Path(*normalized.parts)).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as error:
        raise TaskLogLifecycleError("task log path escaped its root") from error
    return candidate


def _add_tar_bytes(bundle: tarfile.TarFile, name: str, encoded: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(encoded)
    info.mode = 0o600
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    bundle.addfile(info, io.BytesIO(encoded))


def _restrict_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError as error:
        raise TaskLogLifecycleError("task log file permissions cannot be restricted") from error


def _parse_timestamp(value: object) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).astimezone(UTC).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None
