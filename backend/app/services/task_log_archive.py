"""Durable, credential-free archives for task lifecycle events."""

from __future__ import annotations

from datetime import UTC, datetime
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any, Iterable, Iterator, Mapping


CONTRACT_VERSION = "task-log-v1"
MANIFEST_CONTRACT_VERSION = "task-log-manifest-v1"
_MANIFEST_NAME = "manifest.json"
_MANIFEST_LOCK_NAME = ".manifest.lock"
_SECRET_KEYS = frozenset(
    {
        "accesstoken",
        "apikey",
        "apisig",
        "apisecret",
        "authorization",
        "cookie",
        "passphrase",
        "password",
        "privatekey",
        "refreshtoken",
        "secret",
        "signature",
        "token",
    }
)
_SECRET_NAME = re.compile(
    r"(?i)(?:^|[_-])(?:api(?:[_-]?(?:key|secret|sig(?:nature)?))|"
    r"access[_-]?token|refresh[_-]?token|authorization|cookie|passphrase|"
    r"password|private[_-]?key|secret|signature|token)(?:$|[_-])"
)
_SECRET_TEXT = re.compile(
    r"(?i)(\b(?:api[_-]?(?:key|secret|sig(?:nature)?)|access[_-]?token|"
    r"authorization|cookie|passphrase|password|private[_-]?key|"
    r"refresh[_-]?token|secret|signature|token)\b\s*[:=]\s*)([^\s,;]+)"
)
_ARCHIVE_KEY = re.compile(
    r"^(?P<shard>[0-9a-f]{2})/(?P<task>[0-9a-f]{64})/"
    r"(?P<event>[0-9]{20})-(?P<digest>[0-9a-f]{64})\.json$"
)


class TaskLogArchiveError(RuntimeError):
    """Raised when a task log cannot be safely written or verified."""


class TaskLogArchive:
    """Store immutable event objects beneath a task-scoped archive root."""

    def __init__(self, root: str | Path, *, max_event_bytes: int = 1_048_576) -> None:
        self.root = Path(root)
        self.max_event_bytes = max(16 * 1024, min(int(max_event_bytes), 16 * 1024 * 1024))

    def write(self, event: Mapping[str, object]) -> dict[str, object]:
        """Write one redacted event atomically and return its immutable reference."""
        if not isinstance(event, Mapping):
            raise TaskLogArchiveError("task log event must be an object")
        task_id = str(event.get("task_id", "")).strip()
        if not task_id:
            raise TaskLogArchiveError("task log event requires task_id")
        try:
            event_id = int(event.get("event_id", 0) or 0)
        except (TypeError, ValueError) as error:
            raise TaskLogArchiveError("task log event_id is invalid") from error
        if event_id <= 0:
            raise TaskLogArchiveError("task log event_id must be positive")
        event_type = str(event.get("event_type", "")).strip()
        if not event_type:
            raise TaskLogArchiveError("task log event requires event_type")

        document = {
            "contract_version": CONTRACT_VERSION,
            "task_id": task_id,
            "event_id": event_id,
            "event_type": event_type[:80],
            "status": str(event.get("status", ""))[:40] or None,
            "message": redact_log_text(str(event.get("message", ""))[:500]),
            "payload": redact_log_value(event.get("payload")),
            "created_at": str(event.get("created_at") or datetime.now(UTC).isoformat())[:40],
        }
        try:
            encoded = json.dumps(
                document,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise TaskLogArchiveError("task log event is not JSON serializable") from error
        if len(encoded) > self.max_event_bytes:
            raise TaskLogArchiveError("task log event exceeds the archive limit")

        content_digest = hashlib.sha256(encoded).hexdigest()
        task_digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
        relative = PurePosixPath(task_digest[:2], task_digest, f"{event_id:020d}-{content_digest}.json")
        path = self._safe_path(Path(*relative.parts))
        self._atomic_write(path, encoded)
        reference = {
            "contract_version": CONTRACT_VERSION,
            "key": relative.as_posix(),
            "sha256": content_digest,
            "bytes": len(encoded),
            "event_id": event_id,
            "state": "READY",
        }
        self._update_manifest(task_id, reference)
        return reference

    def read(
        self,
        reference: Mapping[str, object],
        *,
        expected_task_id: str | None = None,
        expected_event_id: int | None = None,
    ) -> dict[str, object]:
        """Read and verify one archived event."""
        if not isinstance(reference, Mapping):
            raise TaskLogArchiveError("task log archive reference is invalid")
        if str(reference.get("contract_version", "")) != CONTRACT_VERSION:
            raise TaskLogArchiveError("task log archive contract is unsupported")
        key = str(reference.get("key", "")).strip()
        if not key:
            raise TaskLogArchiveError("task log archive key is missing")
        path = self._safe_path(Path(key))
        key_parts = self._key_parts(key)
        if expected_task_id is not None:
            expected_task_digest = hashlib.sha256(str(expected_task_id).encode("utf-8")).hexdigest()
            if key_parts["task"] != expected_task_digest:
                raise TaskLogArchiveError("task log archive task path mismatch")
        try:
            encoded = path.read_bytes()
        except OSError as error:
            raise TaskLogArchiveError("task log archive object is unavailable") from error
        actual_digest = hashlib.sha256(encoded).hexdigest()
        if str(reference.get("sha256", "")).strip().lower() != actual_digest:
            raise TaskLogArchiveError("task log archive checksum mismatch")
        try:
            reference_event_id = int(reference.get("event_id", 0) or 0)
        except (TypeError, ValueError) as error:
            raise TaskLogArchiveError("task log archive event_id is invalid") from error
        if (
            reference_event_id <= 0
            or reference_event_id != int(key_parts["event"])
            or key_parts["digest"] != actual_digest
        ):
            raise TaskLogArchiveError("task log archive key digest is invalid")
        try:
            expected_bytes = int(reference.get("bytes", -1))
        except (TypeError, ValueError) as error:
            raise TaskLogArchiveError("task log archive size is invalid") from error
        if expected_bytes != len(encoded):
            raise TaskLogArchiveError("task log archive size mismatch")
        try:
            document = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise TaskLogArchiveError("task log archive JSON is invalid") from error
        if not isinstance(document, dict) or document.get("contract_version") != CONTRACT_VERSION:
            raise TaskLogArchiveError("task log archive document is invalid")
        if expected_task_id is not None and document.get("task_id") != str(expected_task_id):
            raise TaskLogArchiveError("task log archive task identity mismatch")
        try:
            document_event_id = int(document.get("event_id", 0) or 0)
        except (TypeError, ValueError) as error:
            raise TaskLogArchiveError("task log archive document event_id is invalid") from error
        if document_event_id != reference_event_id:
            raise TaskLogArchiveError("task log archive event identity mismatch")
        if expected_event_id is not None and document_event_id != int(expected_event_id):
            raise TaskLogArchiveError("task log archive event identity mismatch")
        return document

    def read_manifest(
        self,
        task_id: str,
        *,
        verify_objects: bool = True,
    ) -> dict[str, object] | None:
        """Read and verify one task manifest and, by default, every object."""
        normalized = str(task_id or "").strip()
        if not normalized:
            raise TaskLogArchiveError("task log archive requires task_id")
        path = self._manifest_path(normalized)
        if not path.exists():
            if not self._task_directory(normalized).exists():
                return None
            raise TaskLogArchiveError("task log archive manifest is unavailable")
        document = self._load_manifest(path, normalized)
        if verify_objects:
            for reference in document["events"]:
                self.read(
                    reference,
                    expected_task_id=normalized,
                    expected_event_id=int(reference["event_id"]),
                )
        return document

    def manifest_reference(self, task_id: str) -> dict[str, object] | None:
        """Return a content checksum reference for the verified manifest."""
        normalized = str(task_id or "").strip()
        document = self.read_manifest(normalized, verify_objects=True)
        if document is None:
            return None
        path = self._manifest_path(normalized)
        encoded = path.read_bytes()
        key = _resolved_path(path).relative_to(_resolved_path(self.root)).as_posix()
        return {
            "contract_version": MANIFEST_CONTRACT_VERSION,
            "key": key,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "bytes": len(encoded),
            "state": "READY",
            "event_count": int(document["event_count"]),
            "first_event_id": document["first_event_id"],
            "last_event_id": document["last_event_id"],
        }

    def ensure_manifest(
        self,
        task_id: str,
        *,
        expected_references: Iterable[Mapping[str, object]] = (),
    ) -> dict[str, object] | None:
        """Create or repair a manifest from already verified SQL references."""
        normalized = str(task_id or "").strip()
        if not normalized:
            raise TaskLogArchiveError("task log archive requires task_id")
        references = [self._normalize_reference(value, expected_task_id=normalized) for value in expected_references]
        for reference in references:
            self.read(
                reference,
                expected_task_id=normalized,
                expected_event_id=int(reference["event_id"]),
            )
        directory = self._task_directory(normalized)
        if not references and not directory.exists():
            return None
        with _exclusive_file_lock(directory / _MANIFEST_LOCK_NAME):
            path = directory / _MANIFEST_NAME
            current: dict[str, object] | None = None
            if path.exists():
                try:
                    current = self._load_manifest(path, normalized)
                except TaskLogArchiveError:
                    current = None
            if current is not None and self._same_references(current["events"], references):
                return self.manifest_reference(normalized)
            return self._write_manifest(normalized, references, directory=directory)

    def verify_task(
        self,
        task_id: str,
        *,
        expected_references: Iterable[Mapping[str, object]] | None = None,
    ) -> dict[str, object]:
        """Verify the manifest, referenced objects, and unreferenced JSON files."""
        normalized = str(task_id or "").strip()
        if not normalized:
            raise TaskLogArchiveError("task log archive requires task_id")
        directory = self._task_directory(normalized)
        if not directory.exists():
            if expected_references:
                return {
                    "ok": False,
                    "state": "MISSING",
                    "event_count": 0,
                    "orphan_object_count": 0,
                    "error": "task log archive manifest is unavailable",
                }
            return {
                "ok": True,
                "state": "EMPTY",
                "event_count": 0,
                "orphan_object_count": 0,
            }
        try:
            document = self.read_manifest(normalized, verify_objects=True)
            if document is None:
                raise TaskLogArchiveError("task log archive manifest is unavailable")
            declared = [self._normalize_reference(value, expected_task_id=normalized) for value in document["events"]]
            if expected_references is not None:
                expected = [
                    self._normalize_reference(value, expected_task_id=normalized)
                    for value in expected_references
                ]
                if not self._same_references(declared, expected):
                    raise TaskLogArchiveError("task log archive manifest does not match SQL references")
            objects = self._scan_object_references(normalized, verify_objects=False)
            declared_keys = {str(value["key"]) for value in declared}
            object_keys = {str(value["key"]) for value in objects}
            orphan_count = len(object_keys - declared_keys)
            return {
                "ok": True,
                "state": "READY",
                "event_count": int(document["event_count"]),
                "orphan_object_count": orphan_count,
            }
        except (OSError, TaskLogArchiveError, TypeError, ValueError) as error:
            return {
                "ok": False,
                "state": "CORRUPT",
                "event_count": 0,
                "orphan_object_count": 0,
                "error": str(error),
            }

    def list(self, task_id: str, *, limit: int = 500) -> list[dict[str, object]]:
        """Read a bounded, manifest-ordered task log from the archive."""
        normalized = str(task_id or "").strip()
        document = self.read_manifest(normalized, verify_objects=True)
        if document is None:
            return []
        bounded = max(1, min(int(limit), 5000))
        documents: list[dict[str, object]] = []
        for reference in document["events"][-bounded:]:
            try:
                documents.append(self.read(reference, expected_task_id=normalized))
            except (OSError, TaskLogArchiveError, ValueError):
                raise TaskLogArchiveError("task log archive object is unavailable")
        return documents[-bounded:]

    def _update_manifest(self, task_id: str, reference: Mapping[str, object]) -> None:
        normalized = str(task_id or "").strip()
        directory = self._task_directory(normalized)
        with _exclusive_file_lock(directory / _MANIFEST_LOCK_NAME):
            path = directory / _MANIFEST_NAME
            if path.exists():
                manifest = self._load_manifest(path, normalized)
                references = list(manifest["events"])
            else:
                references = self._scan_object_references(normalized, verify_objects=True)
            by_event_id = {int(value["event_id"]): value for value in references}
            normalized_reference = self._normalize_reference(reference, expected_task_id=normalized)
            by_event_id[int(normalized_reference["event_id"])] = normalized_reference
            self._write_manifest(normalized, list(by_event_id.values()), directory=directory)

    def _write_manifest(
        self,
        task_id: str,
        references: Iterable[Mapping[str, object]],
        *,
        directory: Path | None = None,
    ) -> dict[str, object]:
        normalized = str(task_id or "").strip()
        ordered = sorted(
            (
                self._normalize_reference(value, expected_task_id=normalized)
                for value in references
            ),
            key=lambda value: int(value["event_id"]),
        )
        event_ids = [int(value["event_id"]) for value in ordered]
        if len(set(event_ids)) != len(event_ids):
            raise TaskLogArchiveError("task log archive manifest has duplicate event ids")
        now = datetime.now(UTC).isoformat()
        path = (directory or self._task_directory(normalized)) / _MANIFEST_NAME
        created_at = now
        if path.exists():
            try:
                current = self._load_manifest(path, normalized)
                created_at = str(current.get("created_at") or now)
            except TaskLogArchiveError:
                pass
        body = {
            "contract_version": MANIFEST_CONTRACT_VERSION,
            "task_id": normalized,
            "task_digest": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            "created_at": created_at,
            "updated_at": now,
            "event_count": len(ordered),
            "first_event_id": event_ids[0] if event_ids else None,
            "last_event_id": event_ids[-1] if event_ids else None,
            "events": ordered,
        }
        body["manifest_sha256"] = hashlib.sha256(_json_bytes(body)).hexdigest()
        self._atomic_write(path, _json_bytes(body))
        return {
            "contract_version": MANIFEST_CONTRACT_VERSION,
            "key": _resolved_path(path).relative_to(_resolved_path(self.root)).as_posix(),
            "sha256": hashlib.sha256(_json_bytes(body)).hexdigest(),
            "bytes": len(_json_bytes(body)),
            "state": "READY",
            "event_count": len(ordered),
            "first_event_id": body["first_event_id"],
            "last_event_id": body["last_event_id"],
        }

    def _load_manifest(self, path: Path, expected_task_id: str) -> dict[str, object]:
        try:
            encoded = path.read_bytes()
            document = json.loads(encoded.decode("utf-8"))
        except (OSError, UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise TaskLogArchiveError("task log archive manifest is invalid") from error
        if not isinstance(document, dict):
            raise TaskLogArchiveError("task log archive manifest is invalid")
        if document.get("contract_version") != MANIFEST_CONTRACT_VERSION:
            raise TaskLogArchiveError("task log archive manifest contract is unsupported")
        normalized = str(expected_task_id).strip()
        expected_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if document.get("task_id") != normalized or document.get("task_digest") != expected_digest:
            raise TaskLogArchiveError("task log archive manifest task identity mismatch")
        events = document.get("events")
        if not isinstance(events, list):
            raise TaskLogArchiveError("task log archive manifest events are invalid")
        references = [self._normalize_reference(value, expected_task_id=normalized) for value in events]
        if not self._same_references(events, references):
            raise TaskLogArchiveError("task log archive manifest references are not canonical")
        event_ids = [int(value["event_id"]) for value in references]
        if event_ids != sorted(event_ids) or len(set(event_ids)) != len(event_ids):
            raise TaskLogArchiveError("task log archive manifest event order is invalid")
        try:
            event_count = int(document.get("event_count", -1))
        except (TypeError, ValueError) as error:
            raise TaskLogArchiveError("task log archive manifest event count is invalid") from error
        if event_count != len(references):
            raise TaskLogArchiveError("task log archive manifest event count is invalid")
        if document.get("first_event_id") != (event_ids[0] if event_ids else None):
            raise TaskLogArchiveError("task log archive manifest first event is invalid")
        if document.get("last_event_id") != (event_ids[-1] if event_ids else None):
            raise TaskLogArchiveError("task log archive manifest last event is invalid")
        expected_manifest_digest = str(document.get("manifest_sha256", "")).strip().lower()
        body = dict(document)
        body.pop("manifest_sha256", None)
        if expected_manifest_digest != hashlib.sha256(_json_bytes(body)).hexdigest():
            raise TaskLogArchiveError("task log archive manifest checksum mismatch")
        return {**document, "events": references}

    def _scan_object_references(self, task_id: str, *, verify_objects: bool) -> list[dict[str, object]]:
        directory = self._task_directory(task_id)
        if not directory.exists():
            return []
        references: list[dict[str, object]] = []
        for path in sorted(directory.glob("*.json")):
            if path.name == _MANIFEST_NAME:
                continue
            reference = self._reference_from_path(path)
            if verify_objects:
                self.read(
                    reference,
                    expected_task_id=task_id,
                    expected_event_id=int(reference["event_id"]),
                )
            references.append(reference)
        references.sort(key=lambda value: int(value["event_id"]))
        return references

    def _reference_from_path(self, path: Path) -> dict[str, object]:
        encoded = path.read_bytes()
        key = _resolved_path(path).relative_to(_resolved_path(self.root)).as_posix()
        event_id = int(self._key_parts(key)["event"])
        return {
            "contract_version": CONTRACT_VERSION,
            "key": key,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "bytes": len(encoded),
            "event_id": event_id,
            "state": "READY",
        }

    def _normalize_reference(
        self,
        reference: Mapping[str, object],
        *,
        expected_task_id: str | None = None,
    ) -> dict[str, object]:
        if not isinstance(reference, Mapping):
            raise TaskLogArchiveError("task log archive reference is invalid")
        if str(reference.get("contract_version", "")) != CONTRACT_VERSION:
            raise TaskLogArchiveError("task log archive reference contract is unsupported")
        key = str(reference.get("key", "")).strip()
        parts = self._key_parts(key)
        if expected_task_id is not None:
            expected_digest = hashlib.sha256(str(expected_task_id).encode("utf-8")).hexdigest()
            if parts["task"] != expected_digest:
                raise TaskLogArchiveError("task log archive task path mismatch")
        digest = str(reference.get("sha256", "")).strip().lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise TaskLogArchiveError("task log archive checksum is invalid")
        try:
            event_id = int(reference.get("event_id", 0) or 0)
            size_bytes = int(reference.get("bytes", -1))
        except (TypeError, ValueError) as error:
            raise TaskLogArchiveError("task log archive reference numeric fields are invalid") from error
        if event_id <= 0 or event_id != int(parts["event"]) or size_bytes < 0:
            raise TaskLogArchiveError("task log archive reference is invalid")
        return {
            "contract_version": CONTRACT_VERSION,
            "key": key,
            "sha256": digest,
            "bytes": size_bytes,
            "event_id": event_id,
            "state": str(reference.get("state") or "READY"),
        }

    @staticmethod
    def _same_references(left: Iterable[Mapping[str, object]], right: Iterable[Mapping[str, object]]) -> bool:
        def canonical(values: Iterable[Mapping[str, object]]) -> list[tuple[object, ...]]:
            return sorted(
                (
                    int(value["event_id"]),
                    str(value["key"]),
                    str(value["sha256"]),
                    int(value["bytes"]),
                )
                for value in values
            )

        return canonical(left) == canonical(right)

    def _task_directory(self, task_id: str) -> Path:
        normalized = str(task_id or "").strip()
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return self._safe_path(Path(digest[:2]) / digest)

    def _manifest_path(self, task_id: str) -> Path:
        normalized = str(task_id or "").strip()
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return self._safe_path(Path(digest[:2]) / digest / _MANIFEST_NAME)

    @staticmethod
    def _key_parts(key: str) -> dict[str, str]:
        normalized = PurePosixPath(key)
        if normalized.as_posix() != key:
            raise TaskLogArchiveError("task log archive key is invalid")
        match = _ARCHIVE_KEY.fullmatch(key)
        if match is None or match.group("shard") != match.group("task")[:2]:
            raise TaskLogArchiveError("task log archive key is invalid")
        return match.groupdict()

    def _atomic_write(self, path: Path, encoded: bytes) -> None:
        temp_name: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                mode="wb",
                delete=False,
            ) as handle:
                temp_name = handle.name
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
            _fsync_directory(path.parent)
        except (OSError, TypeError, ValueError) as error:
            raise TaskLogArchiveError("unable to persist task log archive") from error
        finally:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass

    def _safe_path(self, relative: Path) -> Path:
        if relative.is_absolute() or relative.suffix.lower() not in {"", ".json"}:
            raise TaskLogArchiveError("task log archive key is invalid")
        root = _resolved_path(self.root)
        path = _resolved_path(self.root / relative)
        try:
            path.relative_to(root)
        except ValueError as error:
            raise TaskLogArchiveError("task log archive path escaped its root") from error
        return path


def _json_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _resolved_path(path: Path) -> Path:
    """Normalize Windows extended paths before comparing archive boundaries."""
    resolved = str(path.resolve())
    if resolved.startswith("\\\\?\\"):
        resolved = resolved[4:]
    return Path(resolved)


def _fsync_directory(directory: Path) -> None:
    """Persist the rename on filesystems that support directory fsync."""
    if os.name == "nt":
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(directory, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        # Some mounted filesystems reject directory fsync; the object remains
        # durable at the file level and will be verified on the next startup.
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    """Serialize manifest updates across backend and worker processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def redact_log_value(value: Any) -> Any:
    """Redact secret-shaped mapping fields while preserving useful structure."""
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            normalized = re.sub(r"[^a-z0-9]", "", name.lower())
            if (
                normalized in _SECRET_KEYS
                or normalized.endswith("secret")
                or normalized.endswith("token")
                or normalized.startswith(("apikey", "apisecret", "apisig", "privatekey"))
                or "signature" in normalized
                or _SECRET_NAME.search(name) is not None
            ):
                result[name] = "[REDACTED]"
            else:
                result[name] = redact_log_value(item)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_log_value(item) for item in value]
    if isinstance(value, str):
        return redact_log_text(value)
    return value


def redact_log_text(value: str) -> str:
    return _SECRET_TEXT.sub(r"\1[REDACTED]", str(value))
