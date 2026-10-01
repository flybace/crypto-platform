"""Durable control-plane, task, and domain-snapshot storage.

Long-running operations and bounded control-plane snapshots share one
relational connection boundary. Large historical payloads remain in files;
this store keeps metadata and mutable application state queryable and
consistent across API, worker, and scheduler processes.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    case,
    Text,
    create_engine,
    desc,
    func,
    inspect,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import QueuePool

from .task_event_store import TaskEventStoreMixin
from .task_history_store import TaskHistoryStoreMixin
from .task_log_archive import TaskLogArchive
from .task_log_lifecycle_store import TaskLogLifecycleStoreMixin
from .task_dispatch_receipts import TaskDispatchReceiptMixin
from .task_store_codec import UNSET as _UNSET
from .task_store_codec import dump as _dump
from .task_store_codec import load as _load


SCHEMA_ADVISORY_LOCK_KEY = "crypto-platform-task-store-schema-v1"
ACTIVE_IDEMPOTENCY_INDEX = "ux_control_tasks_active_idempotency"
PENDING_DISPATCH_INDEX = "ux_control_task_dispatch_outbox_pending_task"


_ACTIVE_STATUSES = ("queued", "running", "cancelling", "pending")
_TERMINAL_STATUSES = (
    "completed",
    "failed",
    "cancelled",
    "blocked",
    "interrupted",
    "partial",
    "dead_lettered",
)
_REPLAY_PENDING_STATUSES = frozenset({"requested", "queued"})


class TaskStoreError(RuntimeError):
    """Raised when a required task-store operation cannot be completed."""


class TaskLeaseLost(TaskStoreError):
    """Raised when a worker tries to write after its task lease was fenced."""


class TaskStore(
    TaskDispatchReceiptMixin,
    TaskEventStoreMixin,
    TaskHistoryStoreMixin,
    TaskLogLifecycleStoreMixin,
):
    """Small SQLAlchemy Core repository shared by API and worker processes."""

    def __init__(
        self,
        database_url: str,
        *,
        required: bool = False,
        event_archive: TaskLogArchive | None = None,
    ) -> None:
        self.database_url = str(database_url or "").strip()
        self.required = bool(required)
        self.event_archive = event_archive
        self._engine: Engine | None = None
        self._init_error = ""
        self._metadata = MetaData()
        self._tasks = Table(
            "control_tasks",
            self._metadata,
            Column("task_id", String(160), primary_key=True),
            Column("kind", String(80), nullable=False),
            Column("title", String(240), nullable=False),
            Column("status", String(40), nullable=False),
            Column("payload_json", Text, nullable=False, default="{}"),
            Column("result_json", Text, nullable=False, default="{}"),
            Column("error_json", Text, nullable=False, default="{}"),
            Column("progress_json", Text, nullable=False, default="{}"),
            Column("created_at", String(40), nullable=False),
            Column("updated_at", String(40), nullable=False),
            Column("started_at", String(40), nullable=True),
            Column("finished_at", String(40), nullable=True),
            Column("retry_of", String(160), nullable=True),
            Column("idempotency_key", String(240), nullable=True),
            Column("cancel_requested", Boolean, nullable=False, default=False),
            Column("version", Integer, nullable=False, default=1),
            Column("attempt", Integer, nullable=False, default=0),
            Column("max_attempts", Integer, nullable=False, default=3),
            Column("worker_id", String(160), nullable=True),
            Column("lease_expires_at", String(40), nullable=True),
            Column("dead_lettered_at", String(40), nullable=True),
            Index("ix_control_tasks_status_updated", "status", "updated_at"),
            Index("ix_control_tasks_idempotency", "idempotency_key"),
        )
        Index(
            ACTIVE_IDEMPOTENCY_INDEX,
            self._tasks.c.idempotency_key,
            unique=True,
            sqlite_where=text(
                "idempotency_key IS NOT NULL AND status IN "
                "('queued', 'running', 'cancelling', 'pending')"
            ),
            postgresql_where=text(
                "idempotency_key IS NOT NULL AND status IN "
                "('queued', 'running', 'cancelling', 'pending')"
            ),
        )
        self._task_dispatch_outbox = Table(
            "control_task_dispatch_outbox",
            self._metadata,
            Column("message_id", String(64), primary_key=True),
            Column("task_id", String(160), nullable=False),
            Column("kind", String(80), nullable=False),
            Column("payload_json", Text, nullable=False, default="{}"),
            Column("attempt", Integer, nullable=False, default=1),
            Column("max_attempts", Integer, nullable=False, default=3),
            Column("status", String(20), nullable=False, default="pending"),
            Column("replay_source_task_id", String(160), nullable=True),
            Column("replay_request_id", String(160), nullable=True),
            Column("last_error_json", Text, nullable=False, default="{}"),
            Column("created_at", String(40), nullable=False),
            Column("updated_at", String(40), nullable=False),
            Column("published_at", String(40), nullable=True),
            Column("receipt_state", String(40), nullable=True),
            Column("receipt_key", String(320), nullable=True),
            Column("receipt_sha256", String(64), nullable=True),
            Column("receipt_at", String(40), nullable=True),
            Column("publish_attempts", Integer, nullable=False, default=0, server_default="0"),
            Index("ix_control_task_dispatch_outbox_status_created", "status", "created_at"),
            Index("ix_control_task_dispatch_outbox_task", "task_id", "created_at"),
            Index("ix_control_task_dispatch_outbox_receipt_state", "receipt_state", "updated_at"),
        )
        Index(
            PENDING_DISPATCH_INDEX,
            self._task_dispatch_outbox.c.task_id,
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        )
        self._events = Table(
            "control_task_events",
            self._metadata,
            Column("event_id", Integer, primary_key=True, autoincrement=True),
            Column("task_id", String(160), nullable=False),
            Column("event_type", String(80), nullable=False),
            Column("status", String(40), nullable=True),
            Column("message", String(500), nullable=True),
            Column("payload_json", Text, nullable=False, default="{}"),
            Column("created_at", String(40), nullable=False),
            Column("archive_key", String(500), nullable=True),
            Column("archive_sha256", String(64), nullable=True),
            Column("archive_bytes", Integer, nullable=True),
            Column("archive_state", String(20), nullable=False, default="DISABLED"),
            Column("archive_attempts", Integer, nullable=False, default=0),
            Column("archive_error", String(500), nullable=True),
            Index("ix_control_task_events_task_created", "task_id", "created_at"),
        )
        self._dead_letter_replays = Table(
            "control_task_dead_letter_replays",
            self._metadata,
            Column("request_id", String(160), primary_key=True),
            Column("source_task_id", String(160), nullable=False),
            Column("replay_task_id", String(160), nullable=False),
            Column("actor", String(120), nullable=False),
            Column("reason", String(500), nullable=False),
            Column("status", String(40), nullable=False),
            Column("source_attempt", Integer, nullable=False, default=0),
            Column("source_max_attempts", Integer, nullable=False, default=3),
            Column("result_json", Text, nullable=False, default="{}"),
            Column("error_json", Text, nullable=False, default="{}"),
            Column("created_at", String(40), nullable=False),
            Column("updated_at", String(40), nullable=False),
            Index("ix_control_task_dead_letter_replays_source", "source_task_id", "created_at"),
        )
        self._task_log_lifecycle_runs = Table(
            "control_task_log_lifecycle_runs",
            self._metadata,
            Column("run_id", String(64), primary_key=True),
            Column("operation", String(80), nullable=False),
            Column("state", String(20), nullable=False),
            Column("actor", String(120), nullable=False),
            Column("request_id", String(160), nullable=True),
            Column("started_at", String(40), nullable=False),
            Column("finished_at", String(40), nullable=True),
            Column("result_json", Text, nullable=False, default="{}"),
            Column("error_json", Text, nullable=False, default="{}"),
            Column("created_at", String(40), nullable=False),
            Column("updated_at", String(40), nullable=False),
            Index("ix_control_task_log_lifecycle_runs_started", "started_at"),
            Index("ix_control_task_log_lifecycle_runs_state", "state", "started_at"),
        )
        self._history_datasets = Table(
            "history_dataset_metadata",
            self._metadata,
            Column("storage_key", String(240), primary_key=True),
            Column("dataset_id", String(320), nullable=False),
            Column("venue_id", String(80), nullable=False),
            Column("market_type", String(40), nullable=False),
            Column("instrument_key", String(180), nullable=False),
            Column("data_level", String(40), nullable=False),
            Column("interval", String(20), nullable=False),
            Column("start_at", String(40), nullable=False),
            Column("end_at", String(40), nullable=False),
            Column("file_format", String(20), nullable=False),
            Column("source", String(500), nullable=False),
            Column("row_count", Integer, nullable=False),
            Column("gap_count", Integer, nullable=False),
            Column("duplicate_count", Integer, nullable=False),
            Column("content_sha256", String(64), nullable=False),
            Column("updated_at", String(40), nullable=False),
            Index("ix_history_dataset_metadata_dataset_id", "dataset_id"),
            Index("ix_history_dataset_metadata_venue_interval", "venue_id", "interval"),
        )
        self._history_records = Table(
            "history_dataset_records",
            self._metadata,
            Column("storage_key", String(240), primary_key=True),
            Column("dataset_id", String(320), nullable=False),
            Column("venue_id", String(80), nullable=False),
            Column("market_type", String(40), nullable=False),
            Column("instrument_key", String(180), nullable=False),
            Column("data_level", String(40), nullable=False),
            Column("interval", String(20), nullable=False),
            Column("start_at", String(40), nullable=False),
            Column("end_at", String(40), nullable=False),
            Column("file_format", String(20), nullable=False),
            Column("source", String(500), nullable=False),
            Column("row_count", Integer, nullable=False),
            Column("gap_count", Integer, nullable=False),
            Column("duplicate_count", Integer, nullable=False),
            Column("content_sha256", String(64), nullable=False),
            Column("record_status", String(30), nullable=False),
            Column("storage_state", String(30), nullable=False),
            Column("manifest_state", String(30), nullable=False),
            Column("raw_response_state", String(30), nullable=False),
            Column("raw_response_count", Integer, nullable=False),
            Column("parquet_state", String(30), nullable=False),
            Column("parquet_key", String(500), nullable=True),
            Column("parquet_sha256", String(64), nullable=True),
            Column("last_error", String(1000), nullable=True),
            Column("created_at", String(40), nullable=False),
            Column("verified_at", String(40), nullable=False),
            Column("updated_at", String(40), nullable=False),
            Index("ix_history_dataset_records_dataset_id", "dataset_id"),
            Index("ix_history_dataset_records_status", "record_status"),
            Index("ix_history_dataset_records_venue_interval", "venue_id", "interval"),
        )
        self._history_raw_responses = Table(
            "history_raw_responses",
            self._metadata,
            Column("response_id", String(160), primary_key=True),
            Column("dataset_id", String(320), nullable=False),
            Column("storage_key", String(240), nullable=False),
            Column("venue_id", String(80), nullable=False),
            Column("market_type", String(40), nullable=False),
            Column("instrument_key", String(180), nullable=False),
            Column("native_symbol", String(120), nullable=False),
            Column("interval", String(20), nullable=False),
            Column("page_index", Integer, nullable=False),
            Column("request_path", String(240), nullable=False),
            Column("request_params_json", Text, nullable=False),
            Column("archive_key", String(500), nullable=False),
            Column("payload_sha256", String(64), nullable=False),
            Column("content_sha256", String(64), nullable=False),
            Column("size_bytes", Integer, nullable=False),
            Column("received_at", String(40), nullable=False),
            Column("state", String(30), nullable=False),
            Column("created_at", String(40), nullable=False),
            Index("ix_history_raw_responses_dataset", "dataset_id"),
            Index("ix_history_raw_responses_storage", "storage_key"),
        )
        self._domain_snapshots = Table(
            "control_domain_snapshots",
            self._metadata,
            Column("snapshot_key", String(160), primary_key=True),
            Column("payload_json", Text, nullable=False, default="{}"),
            Column("version", Integer, nullable=False, default=1),
            Column("created_at", String(40), nullable=False),
            Column("updated_at", String(40), nullable=False),
            Index("ix_control_domain_snapshots_updated", "updated_at"),
        )
        if not self.database_url:
            return
        try:
            self._engine = self._build_engine(self.database_url)
            self._create_schema()
            self._ensure_task_runtime_columns()
        except Exception as error:  # pragma: no cover - depends on runtime driver/network
            self._init_error = str(error)
            self._engine = None
            if self.required:
                raise TaskStoreError("required task store is unavailable") from error

    def _create_schema(self) -> None:
        """Serialize PostgreSQL startup schema creation across app processes."""
        if self._engine is None:
            return
        if self.database_url.startswith("postgresql"):
            with self._engine.begin() as connection:
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                    {"lock_key": SCHEMA_ADVISORY_LOCK_KEY},
                )
                self._metadata.create_all(connection)
            return
        self._metadata.create_all(self._engine)

    @staticmethod
    def _build_engine(database_url: str) -> Engine:
        options: dict[str, Any] = {"future": True, "pool_pre_ping": True}
        if database_url.startswith("sqlite"):
            options["connect_args"] = {"check_same_thread": False}
            if ":memory:" in database_url:
                # Keep the in-memory database visible to API and worker
                # threads while serializing access to its single connection.
                options["poolclass"] = QueuePool
                options["pool_size"] = 1
                options["max_overflow"] = 0
            elif database_url.startswith("sqlite:///"):
                raw_path = database_url[len("sqlite:///") :]
                if raw_path and raw_path != ":memory:":
                    Path(raw_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        return create_engine(database_url, **options)

    @property
    def enabled(self) -> bool:
        return bool(self.database_url)

    def status(self) -> dict[str, object]:
        """Return a safe health projection without exposing connection details."""
        if not self.enabled:
            return {
                "enabled": False,
                "required": self.required,
                "ok": False,
                "status": "disabled",
                "backend": "none",
                "message": "CRYPTO_DATABASE_URL is empty",
            }
        if self._engine is None:
            return {
                "enabled": True,
                "required": self.required,
                "ok": False,
                "status": "unreachable",
                "backend": self._backend_name(),
                "message": self._init_error or "database engine is unavailable",
            }
        try:
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as error:  # pragma: no cover - depends on runtime state
            return {
                "enabled": True,
                "required": self.required,
                "ok": False,
                "status": "unreachable",
                "backend": self._backend_name(),
                "message": str(error),
            }
        event_archive = self.event_archive_status()
        return {
            "enabled": True,
            "required": self.required,
            "ok": bool(event_archive.get("ok", True)),
            "status": "ready" if bool(event_archive.get("ok", True)) else "degraded",
            "backend": self._backend_name(),
            "message": (
                "control-plane database and task log archive are available"
                if bool(event_archive.get("ok", True))
                else "control-plane database is available but task log archive is incomplete"
            ),
            "event_log_archive": event_archive,
        }

    def read_domain_snapshot(self, snapshot_key: str) -> dict[str, Any] | None:
        """Read one bounded mutable state document from the control plane."""
        self._require_engine()
        key = self._required_text(snapshot_key, "snapshot_key")[:160]
        with self._engine.connect() as connection:
            row = connection.execute(
                select(self._domain_snapshots).where(self._domain_snapshots.c.snapshot_key == key)
            ).mappings().first()
        if row is None:
            return None
        return {
            "snapshot_key": row["snapshot_key"],
            "payload": _load(row["payload_json"]),
            "version": int(row["version"] or 0),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def write_domain_snapshot(self, snapshot_key: str, payload: Any) -> dict[str, Any]:
        """Atomically upsert a bounded state document and its revision."""
        self._require_engine()
        key = self._required_text(snapshot_key, "snapshot_key")[:160]
        encoded = _dump(payload)
        if len(encoded.encode("utf-8")) > 32 * 1024 * 1024:
            raise TaskStoreError("domain snapshot exceeds the 32 MiB limit")
        now = _now()
        for attempt in range(2):
            try:
                with self._engine.begin() as connection:
                    current = connection.execute(
                        select(self._domain_snapshots)
                        .where(self._domain_snapshots.c.snapshot_key == key)
                    ).mappings().first()
                    if current is None:
                        connection.execute(
                            insert(self._domain_snapshots).values(
                                snapshot_key=key,
                                payload_json=encoded,
                                version=1,
                                created_at=now,
                                updated_at=now,
                            )
                        )
                        version = 1
                    else:
                        version = int(current["version"] or 0) + 1
                        connection.execute(
                            update(self._domain_snapshots)
                            .where(self._domain_snapshots.c.snapshot_key == key)
                            .values(payload_json=encoded, version=version, updated_at=now)
                        )
                return {
                    "snapshot_key": key,
                    "payload": _load(encoded),
                    "version": version,
                    "updated_at": now,
                }
            except Exception:
                if attempt:
                    raise
        raise TaskStoreError("domain snapshot write failed")

    def list_domain_snapshots(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return bounded snapshot metadata without exposing connection details."""
        self._require_engine()
        bounded = max(1, min(int(limit), 500))
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(
                    self._domain_snapshots.c.snapshot_key,
                    self._domain_snapshots.c.version,
                    self._domain_snapshots.c.created_at,
                    self._domain_snapshots.c.updated_at,
                )
                .order_by(desc(self._domain_snapshots.c.updated_at))
                .limit(bounded)
            ).mappings().all()
        return [
            {
                "snapshot_key": row["snapshot_key"],
                "version": int(row["version"] or 0),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def domain_snapshot_status(self) -> dict[str, object]:
        """Return health and count for the shared application-state table."""
        if self._engine is None:
            return {
                "enabled": self.enabled,
                "required": self.required,
                "ok": False,
                "status": "unavailable",
                "backend": self._backend_name(),
                "snapshot_count": 0,
                "updated_at": None,
            }
        try:
            with self._engine.connect() as connection:
                count = int(
                    connection.execute(
                        select(func.count()).select_from(self._domain_snapshots)
                    ).scalar_one()
                )
                latest = connection.execute(
                    select(self._domain_snapshots.c.updated_at)
                    .order_by(desc(self._domain_snapshots.c.updated_at))
                    .limit(1)
                ).scalar_one_or_none()
        except Exception as error:  # pragma: no cover - depends on runtime state
            return {
                "enabled": self.enabled,
                "required": self.required,
                "ok": False,
                "status": "unreachable",
                "backend": self._backend_name(),
                "snapshot_count": 0,
                "updated_at": None,
                "message": str(error),
            }
        return {
            "enabled": self.enabled,
            "required": self.required,
            "ok": True,
            "status": "ready" if count else "empty",
            "backend": self._backend_name(),
            "snapshot_count": count,
            "updated_at": latest,
        }

    def create(
        self,
        task_id: str,
        *,
        kind: str,
        title: str,
        payload: Any = None,
        status: str = "queued",
        progress: Any = None,
        retry_of: str | None = None,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
        stage_dispatch: bool = False,
        dispatch_payload: Any = _UNSET,
        replay_source_task_id: str | None = None,
        replay_request_id: str | None = None,
    ) -> dict[str, object]:
        """Create a task, optionally staging its Redis delivery in SQL.

        A staged dispatch is committed in the same transaction as the task
        row. Redis publication can then be retried by the API or Worker after
        a process or network failure without losing the durable intent.
        """
        self._require_engine()
        task_id = self._required_text(task_id, "task_id")
        now = _now()
        bounded_max_attempts = self._bounded_attempts(max_attempts)
        payload_json = _dump(payload)
        dispatch_payload_json = _dump(payload if dispatch_payload is _UNSET else dispatch_payload)
        message_id = uuid4().hex if stage_dispatch else None
        values = {
            "task_id": task_id,
            "kind": self._bounded(kind, 80),
            "title": self._bounded(title, 240),
            "status": self._bounded(status or "queued", 40),
            "payload_json": payload_json,
            "result_json": "{}",
            "error_json": "{}",
            "progress_json": _dump(progress),
            "created_at": now,
            "updated_at": now,
            "retry_of": self._bounded(retry_of, 160) if retry_of else None,
            "idempotency_key": self._bounded(idempotency_key, 240) if idempotency_key else None,
            "cancel_requested": False,
            "version": 1,
            "attempt": 0,
            "max_attempts": bounded_max_attempts,
            "worker_id": None,
            "lease_expires_at": None,
            "dead_lettered_at": None,
        }
        try:
            with self._engine.begin() as connection:
                existing = connection.execute(
                    select(self._tasks).where(self._tasks.c.task_id == task_id)
                ).mappings().first()
                if existing is not None:
                    result = self._record(existing)
                    result["deduplicated"] = True
                    return result
                if idempotency_key:
                    duplicate = connection.execute(
                        select(self._tasks)
                        .where(self._tasks.c.idempotency_key == idempotency_key)
                        .where(self._tasks.c.status.in_(_ACTIVE_STATUSES))
                        .order_by(desc(self._tasks.c.created_at))
                    ).mappings().first()
                    if duplicate is not None:
                        result = self._record(duplicate)
                        result["deduplicated"] = True
                        return result
                connection.execute(insert(self._tasks).values(**values))
                if message_id is not None:
                    connection.execute(
                        insert(self._task_dispatch_outbox).values(
                            message_id=message_id,
                            task_id=task_id,
                            kind=self._bounded(kind, 80),
                            payload_json=dispatch_payload_json,
                            attempt=1,
                            max_attempts=bounded_max_attempts,
                            status="pending",
                            replay_source_task_id=(
                                self._bounded(replay_source_task_id, 160)
                                if replay_source_task_id
                                else None
                            ),
                            replay_request_id=(
                                self._bounded(replay_request_id, 160)
                                if replay_request_id
                                else None
                            ),
                            last_error_json="{}",
                            created_at=now,
                            updated_at=now,
                            published_at=None,
                            receipt_state="PENDING",
                            receipt_key=None,
                            receipt_sha256=None,
                            receipt_at=None,
                            publish_attempts=0,
                        )
                    )
        except IntegrityError:
            # The partial unique indexes close the race between the identity
            # read and insert. Re-read the committed winner and preserve the
            # idempotent API contract instead of returning a 500.
            with self._engine.connect() as connection:
                winner = connection.execute(
                    select(self._tasks).where(self._tasks.c.task_id == task_id)
                ).mappings().first()
                if winner is None and idempotency_key:
                    winner = connection.execute(
                        select(self._tasks)
                        .where(self._tasks.c.idempotency_key == idempotency_key)
                        .where(self._tasks.c.status.in_(_ACTIVE_STATUSES))
                        .order_by(desc(self._tasks.c.created_at))
                    ).mappings().first()
            if winner is None:
                raise
            result = self._record(winner)
            result["deduplicated"] = True
            return result
        return self.get(task_id) or self._record(values)

    def stage_dispatch_for_task(
        self,
        task_id: str,
        *,
        dispatch_payload: Any = _UNSET,
        replay_source_task_id: str | None = None,
        replay_request_id: str | None = None,
    ) -> dict[str, object] | None:
        """Create a missing dispatch intent for one still-queued task."""
        self._require_engine()
        task_key = self._required_text(task_id, "task_id")
        now = _now()
        try:
            with self._engine.begin() as connection:
                task = connection.execute(
                    select(self._tasks).where(self._tasks.c.task_id == task_key)
                ).mappings().first()
                if task is None or str(task.get("status", "")) != "queued":
                    return None
                existing = connection.execute(
                    select(self._task_dispatch_outbox)
                    .where(self._task_dispatch_outbox.c.task_id == task_key)
                    .order_by(self._task_dispatch_outbox.c.created_at.desc())
                ).mappings().first()
                if existing is not None:
                    return self._dispatch_record(existing)
                message_id = uuid4().hex
                dispatch_payload_json = (
                    str(task["payload_json"] or "{}")
                    if dispatch_payload is _UNSET
                    else _dump(dispatch_payload)
                )
                connection.execute(
                    insert(self._task_dispatch_outbox).values(
                        message_id=message_id,
                        task_id=task_key,
                        kind=self._bounded(task["kind"], 80),
                        payload_json=dispatch_payload_json,
                        attempt=max(1, int(task.get("attempt") or 1)),
                        max_attempts=self._bounded_attempts(int(task.get("max_attempts") or 3)),
                        status="pending",
                        replay_source_task_id=(
                            self._bounded(replay_source_task_id, 160)
                            if replay_source_task_id
                            else None
                        ),
                        replay_request_id=(
                            self._bounded(replay_request_id, 160)
                            if replay_request_id
                            else None
                        ),
                        last_error_json="{}",
                        created_at=now,
                        updated_at=now,
                        published_at=None,
                        receipt_state="PENDING",
                        receipt_key=None,
                        receipt_sha256=None,
                        receipt_at=None,
                        publish_attempts=0,
                    )
                )
                row = connection.execute(
                    select(self._task_dispatch_outbox)
                    .where(self._task_dispatch_outbox.c.message_id == message_id)
                ).mappings().one()
        except IntegrityError:
            # The unique pending-task index closes the race between the
            # existence read and insert. The failed transaction must be fully
            # rolled back before the winning row can be read.
            with self._engine.connect() as connection:
                existing = connection.execute(
                    select(self._task_dispatch_outbox)
                    .where(self._task_dispatch_outbox.c.task_id == task_key)
                    .where(self._task_dispatch_outbox.c.status == "pending")
                    .order_by(self._task_dispatch_outbox.c.created_at)
                ).mappings().first()
            if existing is None:
                raise
            return self._dispatch_record(existing)
        return self._dispatch_record(row)

    def stage_missing_dispatches(self, limit: int = 100) -> int:
        """Backfill outbox rows for queued tasks created before outbox support."""
        self._require_engine()
        bounded = max(1, min(int(limit), 500))
        with self._engine.connect() as connection:
            task_ids = [
                str(row[0])
                for row in connection.execute(
                    select(self._tasks.c.task_id)
                    .where(self._tasks.c.status == "queued")
                    .order_by(self._tasks.c.created_at)
                    .limit(bounded)
                ).all()
            ]
        staged = 0
        for task_id in task_ids:
            before = self.pending_dispatches(task_id=task_id, limit=1)
            if before:
                continue
            created = self.stage_dispatch_for_task(task_id)
            if created is not None and str(created.get("status")) == "pending":
                staged += 1
        return staged

    def dispatch_consistency_candidates(self, limit: int = 100) -> list[dict[str, object]]:
        """Return active tasks and terminal tasks with pending delivery intents.

        The consistency auditor needs the task payload to repair a missing
        Redis list item, but that payload stays inside this internal result and
        is never included in the auditor's API projection or log messages.
        """
        self._require_engine()
        bounded = max(1, min(int(limit), 500))
        with self._engine.connect() as connection:
            pending_delivery = (
                select(self._task_dispatch_outbox.c.message_id)
                .where(self._task_dispatch_outbox.c.task_id == self._tasks.c.task_id)
                .where(self._task_dispatch_outbox.c.status == "pending")
                .exists()
            )
            tasks = connection.execute(
                select(self._tasks)
                .where(
                    or_(
                        self._tasks.c.status.in_(_ACTIVE_STATUSES),
                        pending_delivery,
                    )
                )
                .order_by(
                    case((pending_delivery, 0), else_=1),
                    self._tasks.c.updated_at,
                    self._tasks.c.task_id,
                )
                .limit(bounded)
            ).mappings().all()
            candidates: list[dict[str, object]] = []
            for task in tasks:
                dispatch = connection.execute(
                    select(self._task_dispatch_outbox)
                    .where(self._task_dispatch_outbox.c.task_id == task["task_id"])
                    .order_by(
                        desc(self._task_dispatch_outbox.c.created_at),
                        desc(self._task_dispatch_outbox.c.message_id),
                    )
                    .limit(1)
                ).mappings().first()
                candidates.append(
                    {
                        "task_id": task["task_id"],
                        "kind": task["kind"],
                        "status": task["status"],
                        "payload": _load(task["payload_json"]),
                        "attempt": int(task.get("attempt") or 0),
                        "max_attempts": int(task.get("max_attempts") or 3),
                        "dispatch": None if dispatch is None else self._dispatch_record(dispatch),
                    }
                )
        return candidates

    def pending_dispatches(
        self,
        *,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """Return SQL-committed Redis delivery intents that are not published."""
        self._require_engine()
        bounded = max(1, min(int(limit), 500))
        with self._engine.connect() as connection:
            statement = (
                select(self._task_dispatch_outbox)
                .where(self._task_dispatch_outbox.c.status == "pending")
            )
            if task_id is not None:
                statement = statement.where(
                    self._task_dispatch_outbox.c.task_id == str(task_id)
                )
            statement = statement.order_by(self._task_dispatch_outbox.c.created_at).limit(bounded)
            rows = connection.execute(statement).mappings().all()
        return [self._dispatch_record(row) for row in rows]

    def _dispatch_by_message_id(self, message_id: str) -> dict[str, object] | None:
        """Read one outbox row after an ambiguous SQL acknowledgement."""
        self._require_engine()
        key = self._required_text(message_id, "message_id")[:64]
        with self._engine.connect() as connection:
            row = connection.execute(
                select(self._task_dispatch_outbox)
                .where(self._task_dispatch_outbox.c.message_id == key)
            ).mappings().first()
        return None if row is None else self._dispatch_record(row)

    def publish_pending_dispatches(
        self,
        publisher: Any,
        *,
        task_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        """Publish SQL outbox rows with fixed message IDs and bounded retry state."""
        pending = self.pending_dispatches(task_id=task_id, limit=limit)
        published: list[dict[str, object]] = []
        failed: list[dict[str, object]] = []
        for dispatch in pending:
            message_id = str(dispatch["message_id"])
            try:
                task = self.get(str(dispatch["task_id"]))
                task_status = str((task or {}).get("status", "")).strip().lower()
                if task_status in _TERMINAL_STATUSES and task_status != "dead_lettered":
                    # A worker can finish the task after Redis accepted the
                    # message but before the SQL outbox acknowledgement was
                    # committed.  Re-publishing that fixed delivery would
                    # only create a terminal-task duplicate.
                    acknowledged = self.mark_dispatch_published(
                        message_id,
                        receipt={"state": "SETTLED_NO_PUBLISH"},
                    )
                    if not acknowledged:
                        current = self._dispatch_by_message_id(message_id)
                        if current is None or str(current.get("status")) != "published":
                            raise TaskStoreError("SQL terminal dispatch acknowledgement was not committed")
                        dispatch = current
                    published.append({
                        "dispatch": dispatch,
                        "envelope": self._dispatch_envelope(dispatch),
                        "settled": True,
                    })
                    continue
                confirmed = self._existing_dispatch_envelope(publisher, dispatch)
                if confirmed is not None:
                    receipt = self._publisher_delivery_receipt(publisher, dispatch)
                    if receipt is None:
                        acknowledged = self.mark_dispatch_published(message_id)
                    else:
                        acknowledged = self.mark_dispatch_published(message_id, receipt=receipt)
                    if not acknowledged:
                        current = self._dispatch_by_message_id(message_id)
                        if current is None or str(current.get("status")) != "published":
                            raise TaskStoreError("SQL outbox publish acknowledgement was not committed")
                    published.append({
                        "dispatch": dispatch,
                        "envelope": confirmed,
                        "confirmed": True,
                    })
                    continue
                payload = dispatch.get("payload")
                source_task_id = str(dispatch.get("replay_source_task_id") or "")
                replay_request_id = str(dispatch.get("replay_request_id") or "")
                if source_task_id and replay_request_id:
                    replay = getattr(publisher, "replay_dead_letter", None)
                    if not callable(replay):
                        raise TaskStoreError("task queue does not support dead-letter replay")
                    outcome = replay(
                        source_task_id,
                        str(dispatch["kind"]),
                        payload,
                        replay_task_id=str(dispatch["task_id"]),
                        request_id=replay_request_id,
                        max_attempts=int(dispatch["max_attempts"]),
                        message_id=message_id,
                    )
                    envelope = outcome.get("envelope") if isinstance(outcome, dict) else None
                    if (
                        not envelope
                        and isinstance(outcome, dict)
                        and outcome.get("deduplicated")
                    ):
                        envelope = {
                            "message_id": message_id,
                            "task_id": str(dispatch["task_id"]),
                            "kind": str(dispatch["kind"]),
                            "attempt": int(dispatch["attempt"]),
                            "max_attempts": int(dispatch["max_attempts"]),
                        }
                else:
                    # A pending SQL row can mean that Redis accepted the
                    # message but the SQL acknowledgement was lost. Prefer
                    # the fixed-ID repair path so a surviving Redis receipt
                    # does not hide a missing queue item.
                    ensure = getattr(publisher, "ensure_enqueued", None)
                    enqueue = ensure if callable(ensure) else getattr(publisher, "enqueue", None)
                    if not callable(enqueue):
                        raise TaskStoreError("task publisher is unavailable")
                    envelope = enqueue(
                        str(dispatch["task_id"]),
                        str(dispatch["kind"]),
                        payload,
                        attempt=int(dispatch["attempt"]),
                        max_attempts=int(dispatch["max_attempts"]),
                        message_id=message_id,
                    )
                if not isinstance(envelope, dict):
                    raise TaskStoreError("task publisher returned an invalid envelope")
                self._validate_dispatch_envelope(dispatch, envelope)
                receipt = self._publisher_delivery_receipt(publisher, dispatch)
                if receipt is None:
                    acknowledged = self.mark_dispatch_published(message_id)
                else:
                    acknowledged = self.mark_dispatch_published(message_id, receipt=receipt)
                if not acknowledged:
                    # A concurrent relay may have committed the same row. Do
                    # not accept a false success until the durable row confirms
                    # that outcome; otherwise a pending row can be lost after a
                    # Redis publish.
                    current = self._dispatch_by_message_id(message_id)
                    if current is None or str(current.get("status")) != "published":
                        raise TaskStoreError("SQL outbox publish acknowledgement was not committed")
                published.append({"dispatch": dispatch, "envelope": envelope})
            except Exception as error:
                # The Redis publish and the SQL acknowledgement are separate
                # systems.  If the acknowledgement committed and only its
                # response was lost, report the durable winner instead of
                # turning a successful delivery into a retry signal.
                try:
                    current = self._dispatch_by_message_id(message_id)
                except Exception:
                    current = None
                if current is not None and str(current.get("status")) == "published":
                    published.append({
                        "dispatch": current,
                        "envelope": self._dispatch_envelope(current),
                        "confirmed": True,
                    })
                    continue
                failure = {
                    "kind": str(error.__class__.__name__).upper()[:80],
                    "message": str(error)[:500],
                }
                try:
                    self.mark_dispatch_failed(message_id, failure)
                except Exception:
                    pass
                failed.append({"dispatch": dispatch, "error": failure})
        return {"published": published, "failed": failed, "pending": len(pending)}

    def schedule_retry(
        self,
        task_id: str,
        *,
        attempt: int,
        error: Any,
        expected_worker_id: str | None | object = _UNSET,
    ) -> dict[str, object] | None:
        """Queue a retry through one SQL transaction before Redis is touched.

        The task state and its next delivery intent must become durable
        together.  The Worker can then acknowledge the old Redis claim; if it
        crashes before that acknowledgement, the old claim and pending outbox
        are both recoverable without leaving the SQL task in ``running``.
        """
        self._require_engine()
        task_key = self._required_text(task_id, "task_id")
        retry_attempt = self._bounded_attempts(int(attempt))
        now = _now()
        with self._engine.begin() as connection:
            current = connection.execute(
                select(self._tasks)
                .where(self._tasks.c.task_id == task_key)
                .with_for_update()
            ).mappings().first()
            if current is None:
                return None
            if expected_worker_id is not _UNSET:
                expected = self._bounded(expected_worker_id, 160)
                if (
                    str(current.get("status", "")) not in {"running", "cancelling"}
                    or str(current.get("worker_id") or "") != expected
                ):
                    return None
            max_attempts = self._bounded_attempts(int(current.get("max_attempts") or 3))
            pending = connection.execute(
                select(self._task_dispatch_outbox)
                .where(self._task_dispatch_outbox.c.task_id == task_key)
                .where(self._task_dispatch_outbox.c.status == "pending")
                .order_by(self._task_dispatch_outbox.c.created_at)
            ).mappings().first()
            message_id = str(pending.get("message_id", "")) if pending is not None else uuid4().hex
            safe_error = error if error is not None else {}
            if pending is None:
                connection.execute(
                    insert(self._task_dispatch_outbox).values(
                        message_id=message_id,
                        task_id=task_key,
                        kind=self._bounded(current.get("kind"), 80),
                        payload_json=str(current.get("payload_json") or "{}"),
                        attempt=retry_attempt,
                        max_attempts=max_attempts,
                        status="pending",
                        replay_source_task_id=None,
                        replay_request_id=None,
                        last_error_json=_dump(safe_error),
                        created_at=now,
                        updated_at=now,
                        published_at=None,
                        receipt_state="PENDING",
                        receipt_key=None,
                        receipt_sha256=None,
                        receipt_at=None,
                        publish_attempts=0,
                    )
                )
            else:
                connection.execute(
                    update(self._task_dispatch_outbox)
                    .where(self._task_dispatch_outbox.c.message_id == message_id)
                    .values(
                        kind=self._bounded(current.get("kind"), 80),
                        payload_json=str(current.get("payload_json") or "{}"),
                        attempt=retry_attempt,
                        max_attempts=max_attempts,
                        last_error_json=_dump(safe_error),
                        updated_at=now,
                    )
                )
            connection.execute(
                update(self._tasks)
                .where(self._tasks.c.task_id == task_key)
                .values(
                    status="queued",
                    error_json=_dump(safe_error),
                    attempt=retry_attempt,
                    worker_id=None,
                    lease_expires_at=None,
                    cancel_requested=False,
                    updated_at=now,
                    version=int(current.get("version") or 0) + 1,
                )
            )
            row = connection.execute(
                select(self._tasks).where(self._tasks.c.task_id == task_key)
            ).mappings().first()
        record = self._record(row) if row is not None else None
        if record is not None:
            self.append_event(
                task_key,
                "retry_scheduled",
                status="queued",
                message="任务失败后已通过 SQL outbox 重新排队",
                payload={
                    "attempt": retry_attempt,
                    "max_attempts": max_attempts,
                    "message_id": message_id,
                    "error": safe_error,
                },
                expected_version=int(record["version"]),
            )
        return record

    def reconcile_queued_dispatches(
        self,
        publisher: Any,
        *,
        limit: int = 100,
    ) -> dict[str, object]:
        """Repair queued tasks whose Redis message disappeared after publish.

        SQL remains the durable intent.  The queue implementation's
        ``ensure_enqueued`` operation checks both Redis lists while retaining
        the fixed message ID, so a receipt/list split can be repaired without
        producing an unbounded stream of duplicate messages.
        """
        self._require_engine()
        bounded = max(1, min(int(limit), 500))
        with self._engine.connect() as connection:
            tasks = connection.execute(
                select(self._tasks)
                .where(self._tasks.c.status == "queued")
                .order_by(self._tasks.c.updated_at)
                .limit(bounded)
            ).mappings().all()
        repaired: list[dict[str, object]] = []
        staged: list[str] = []
        failed: list[dict[str, object]] = []
        for task in tasks:
            task_key = str(task.get("task_id", "")).strip()
            if not task_key:
                continue
            with self._engine.connect() as connection:
                dispatch = connection.execute(
                    select(self._task_dispatch_outbox)
                    .where(self._task_dispatch_outbox.c.task_id == task_key)
                    .order_by(self._task_dispatch_outbox.c.created_at.desc())
                    .limit(1)
                ).mappings().first()
            if dispatch is None:
                created = self.stage_dispatch_for_task(task_key)
                if created is not None and str(created.get("status")) == "pending":
                    staged.append(task_key)
                continue
            if str(dispatch.get("status", "")) != "published":
                continue
            try:
                ensure = getattr(publisher, "ensure_enqueued", None)
                enqueue = ensure if callable(ensure) else getattr(publisher, "enqueue", None)
                if not callable(enqueue):
                    raise TaskStoreError("task publisher is unavailable")
                envelope = enqueue(
                    task_key,
                    str(dispatch["kind"]),
                    _load(dispatch["payload_json"]),
                    attempt=int(dispatch.get("attempt") or 1),
                    max_attempts=int(dispatch.get("max_attempts") or 3),
                    message_id=str(dispatch["message_id"]),
                )
                if not isinstance(envelope, dict):
                    raise TaskStoreError("task publisher returned an invalid envelope")
                self._validate_dispatch_envelope(dispatch, envelope)
                repaired.append({"dispatch": self._dispatch_record(dispatch), "envelope": envelope})
            except Exception as error:
                failed.append(
                    {
                        "task_id": task_key,
                        "message_id": str(dispatch.get("message_id", "")),
                        "error": {
                            "kind": str(error.__class__.__name__).upper()[:80],
                            "message": str(error)[:500],
                        },
                    }
                )
        return {
            "checked": len(tasks),
            "repaired": repaired,
            "staged": staged,
            "failed": failed,
        }

    def get(self, task_id: str) -> dict[str, object] | None:
        self._require_engine()
        with self._engine.connect() as connection:
            row = connection.execute(
                select(self._tasks).where(self._tasks.c.task_id == str(task_id))
            ).mappings().first()
        return None if row is None else self._record(row)

    def list(
        self,
        limit: int = 100,
        *,
        status: str | None = None,
        kind: str | None = None,
    ) -> list[dict[str, object]]:
        self._require_engine()
        bounded = max(1, min(int(limit), 500))
        with self._engine.connect() as connection:
            statement = select(self._tasks)
            if status:
                statement = statement.where(self._tasks.c.status == self._bounded(status, 40))
            if kind:
                statement = statement.where(self._tasks.c.kind == self._bounded(kind, 80))
            rows = connection.execute(statement.order_by(desc(self._tasks.c.updated_at)).limit(bounded)).mappings().all()
        return [self._record(row) for row in rows]

    def sync(
        self,
        task_id: str,
        *,
        status: str | None = None,
        progress: Any = _UNSET,
        payload: Any = _UNSET,
        result: Any = _UNSET,
        error: Any = _UNSET,
        cancel_requested: bool | None = None,
        attempt: int | object = _UNSET,
        worker_id: str | None | object = _UNSET,
        lease_expires_at: str | None | object = _UNSET,
        dead_lettered_at: str | None | object = _UNSET,
        expected_worker_id: str | None | object = _UNSET,
    ) -> dict[str, object] | None:
        """Advance one task version, optionally fenced to a live worker lease."""
        self._require_engine()
        now = _now()
        replay_settlement_events: list[dict[str, object]] = []
        with self._engine.begin() as connection:
            current = connection.execute(
                select(self._tasks)
                .where(self._tasks.c.task_id == str(task_id))
                .with_for_update()
            ).mappings().first()
            if current is None:
                return None
            if expected_worker_id is not _UNSET:
                expected = self._bounded(expected_worker_id, 160)
                if (
                    str(current.get("status", "")) not in {"running", "cancelling"}
                    or str(current.get("worker_id") or "") != expected
                ):
                    return None
            values: dict[str, object] = {
                "updated_at": now,
                "version": int(current.get("version") or 0) + 1,
            }
            if status is not None:
                normalized = self._bounded(status, 40)
                values["status"] = normalized
                if normalized == "running" and not current.get("started_at"):
                    values["started_at"] = now
                if normalized in _TERMINAL_STATUSES and not current.get("finished_at"):
                    values["finished_at"] = now
                if normalized in _TERMINAL_STATUSES:
                    if worker_id is _UNSET:
                        values["worker_id"] = None
                    if lease_expires_at is _UNSET:
                        values["lease_expires_at"] = None
            if progress is not _UNSET:
                values["progress_json"] = _dump(progress)
            if payload is not _UNSET:
                values["payload_json"] = _dump(payload)
            if result is not _UNSET:
                values["result_json"] = _dump(result)
            if error is not _UNSET:
                values["error_json"] = _dump(error)
            if cancel_requested is not None:
                values["cancel_requested"] = bool(cancel_requested)
            if attempt is not _UNSET:
                values["attempt"] = self._bounded_attempts(int(attempt), allow_zero=True)
            if worker_id is not _UNSET:
                values["worker_id"] = self._bounded(worker_id, 160) if worker_id else None
            if lease_expires_at is not _UNSET:
                values["lease_expires_at"] = str(lease_expires_at)[:40] if lease_expires_at else None
            if dead_lettered_at is not _UNSET:
                values["dead_lettered_at"] = str(dead_lettered_at)[:40] if dead_lettered_at else None
            connection.execute(
                update(self._tasks).where(self._tasks.c.task_id == str(task_id)).values(**values)
            )
            updated = connection.execute(
                select(self._tasks).where(self._tasks.c.task_id == str(task_id))
            ).mappings().first()
            if updated is not None:
                replay_settlement_events = self._settle_dead_letter_replay_for_task(
                    connection,
                    updated,
                )
        for replay_settlement_event in replay_settlement_events:
            self._archive_event(replay_settlement_event)
        return self.get(task_id)

    def mark_claimed(
        self,
        task_id: str,
        *,
        worker_id: str,
        attempt: int,
        lease_seconds: int,
        message_id: str | None = None,
    ) -> dict[str, object] | None:
        """Atomically acquire a task lease before domain code starts running."""
        self._require_engine()
        task_key = str(task_id)
        worker_key = self._bounded(worker_id, 160)
        now = datetime.now(UTC)
        expires_at = now.timestamp() + max(30, int(lease_seconds))
        expires_value = datetime.fromtimestamp(expires_at, tz=UTC).isoformat()
        with self._engine.begin() as connection:
            current = connection.execute(
                select(self._tasks)
                .where(self._tasks.c.task_id == task_key)
                .with_for_update()
            ).mappings().first()
            if current is None:
                return None
            requested_attempt = self._bounded_attempts(int(attempt))
            delivery_key = str(message_id or "").strip()
            if delivery_key:
                latest_dispatch = connection.execute(
                    select(self._task_dispatch_outbox)
                    .where(self._task_dispatch_outbox.c.task_id == task_key)
                    .order_by(
                        desc(self._task_dispatch_outbox.c.created_at),
                        desc(self._task_dispatch_outbox.c.message_id),
                    )
                    .limit(1)
                ).mappings().first()
                # Tasks created before the SQL outbox was introduced have no
                # fixed delivery to fence. Keep those legacy messages usable;
                # all current Redis dispatches carry a registered outbox row.
                if latest_dispatch is not None:
                    expected_message_id = str(latest_dispatch.get("message_id", "")).strip()
                    expected_attempt = self._bounded_attempts(
                        int(latest_dispatch.get("attempt") or 1)
                    )
                    dispatch_status = str(latest_dispatch.get("status", "")).strip().lower()
                    rejection_reason = ""
                    if expected_message_id != delivery_key:
                        rejection_reason = "stale_delivery"
                    elif requested_attempt != expected_attempt:
                        rejection_reason = "attempt_mismatch"
                    elif dispatch_status not in {"pending", "published"}:
                        rejection_reason = "delivery_not_publishable"
                    if rejection_reason:
                        record = self._record(current)
                        record["claim_acquired"] = False
                        record["claim_rejected"] = rejection_reason
                        record["expected_message_id"] = expected_message_id
                        record["expected_attempt"] = expected_attempt
                        return record
            current_status = str(current.get("status", ""))
            if current_status in _TERMINAL_STATUSES:
                record = self._record(current)
                record["claim_acquired"] = False
                return record
            lease_expiry = self._parse_timestamp(current.get("lease_expires_at"))
            if (
                current_status == "running"
                and lease_expiry is not None
                and lease_expiry > now.timestamp()
            ):
                record = self._record(current)
                record["claim_acquired"] = False
                return record
            # A Redis claim can be restored after SQL already committed a
            # retry attempt.  Never let the stale envelope move the durable
            # attempt counter backwards when that claim is acquired again.
            effective_attempt = max(
                requested_attempt,
                self._bounded_attempts(int(current.get("attempt") or 0), allow_zero=True),
            )
            values: dict[str, object] = {
                "status": "running",
                "attempt": effective_attempt,
                "worker_id": worker_key or None,
                "lease_expires_at": expires_value,
                "updated_at": now.isoformat(),
                "version": int(current.get("version") or 0) + 1,
            }
            if not current.get("started_at"):
                values["started_at"] = now.isoformat()
            connection.execute(
                update(self._tasks)
                .where(self._tasks.c.task_id == task_key)
                .values(**values)
            )
            row = connection.execute(
                select(self._tasks).where(self._tasks.c.task_id == task_key)
            ).mappings().first()
        record = self._record(row) if row is not None else None
        if record is not None:
            record["claim_acquired"] = True
        return record

    def renew_claim(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        """Extend a live SQL lease only while this worker still owns it."""
        self._require_engine()
        task_key = str(task_id)
        worker_key = self._bounded(worker_id, 160)
        now = datetime.now(UTC)
        expires_value = datetime.fromtimestamp(
            now.timestamp() + max(30, int(lease_seconds)),
            tz=UTC,
        ).isoformat()
        with self._engine.begin() as connection:
            current = connection.execute(
                select(self._tasks)
                .where(self._tasks.c.task_id == task_key)
                .with_for_update()
            ).mappings().first()
            if (
                current is None
                or str(current.get("status", "")) != "running"
                or str(current.get("worker_id") or "") != worker_key
            ):
                return False
            connection.execute(
                update(self._tasks)
                .where(self._tasks.c.task_id == task_key)
                .values(
                    lease_expires_at=expires_value,
                    updated_at=now.isoformat(),
                    version=int(current.get("version") or 0) + 1,
                )
            )
        return True

    def recover_after_lease(
        self,
        task_id: str,
        *,
        attempt: int,
        message_id: str = "",
    ) -> dict[str, object] | None:
        """Make a task visible as queued after a worker lease expired."""
        self._require_engine()
        task_key = str(task_id)
        now = _now()
        error = {
            "kind": "WORKER_LEASE_EXPIRED",
            "message": "Worker lease expired; task was returned to the queue",
            "message_id": str(message_id),
        }
        with self._engine.begin() as connection:
            current = connection.execute(
                select(self._tasks)
                .where(self._tasks.c.task_id == task_key)
                .with_for_update()
            ).mappings().first()
            if current is None:
                return None
            if str(current.get("status", "")) != "running":
                return self._record(current)
            connection.execute(
                update(self._tasks)
                .where(self._tasks.c.task_id == task_key)
                .values(
                    status="queued",
                    error_json=_dump(error),
                    attempt=self._bounded_attempts(int(attempt)),
                    worker_id=None,
                    lease_expires_at=None,
                    updated_at=now,
                    version=int(current.get("version") or 0) + 1,
                )
            )
            row = connection.execute(
                select(self._tasks).where(self._tasks.c.task_id == task_key)
            ).mappings().first()
        record = self._record(row) if row is not None else None
        if record is not None:
            self.append_event(
                task_key,
                "requeued_after_lease_expiry",
                status="queued",
                message="Worker 租约过期，任务已恢复到队列",
                payload={"attempt": attempt, "message_id": str(message_id)},
            )
        return record

    def recover_after_worker_loss(
        self,
        task_id: str,
        *,
        worker_id: str,
        attempt: int,
        message_id: str = "",
        failure_kind: str = "WORKER_HEARTBEAT_EXPIRED",
        failure_message: str | None = None,
        event_type: str = "requeued_after_worker_loss",
        event_message: str | None = None,
    ) -> dict[str, object] | None:
        """Requeue a task after worker loss or a controlled worker shutdown.

        The SQL transition and a fresh outbox intent are one transaction. The
        caller removes the old Redis processing claim only after this method
        returns, so a crash between the two steps is recoverable by the outbox
        relay or the normal claim recovery path.
        """
        self._require_engine()
        task_key = str(task_id).strip()
        worker_key = self._bounded(worker_id, 160)
        now = _now()
        reason_kind = self._bounded(failure_kind, 80) or "WORKER_HEARTBEAT_EXPIRED"
        error = {
            "kind": reason_kind,
            "message": self._bounded(
                failure_message or "Worker exited before task completion; task was returned to the queue",
                500,
            ),
            "worker_id": worker_key,
            "message_id": str(message_id),
        }
        recovered = False
        with self._engine.begin() as connection:
            current = connection.execute(
                select(self._tasks)
                .where(self._tasks.c.task_id == task_key)
                .with_for_update()
            ).mappings().first()
            if current is None:
                return None
            if (
                str(current.get("status", "")) != "running"
                or str(current.get("worker_id") or "") != worker_key
            ):
                return self._record(current)
            recovery_attempt = self._bounded_attempts(int(attempt))
            max_attempts = self._bounded_attempts(int(current.get("max_attempts") or 3))
            pending = connection.execute(
                select(self._task_dispatch_outbox)
                .where(self._task_dispatch_outbox.c.task_id == task_key)
                .where(self._task_dispatch_outbox.c.status == "pending")
                .order_by(self._task_dispatch_outbox.c.created_at)
            ).mappings().first()
            recovery_message_id = str(pending.get("message_id", "")) if pending is not None else uuid4().hex
            error["message_id"] = recovery_message_id
            if pending is None:
                connection.execute(
                    insert(self._task_dispatch_outbox).values(
                        message_id=recovery_message_id,
                        task_id=task_key,
                        kind=self._bounded(current.get("kind"), 80),
                        payload_json=str(current.get("payload_json") or "{}"),
                        attempt=recovery_attempt,
                        max_attempts=max_attempts,
                        status="pending",
                        replay_source_task_id=None,
                        replay_request_id=None,
                        last_error_json=_dump(error),
                        created_at=now,
                        updated_at=now,
                        published_at=None,
                        receipt_state="PENDING",
                        receipt_key=None,
                        receipt_sha256=None,
                        receipt_at=None,
                        publish_attempts=0,
                    )
                )
            else:
                connection.execute(
                    update(self._task_dispatch_outbox)
                    .where(self._task_dispatch_outbox.c.message_id == recovery_message_id)
                    .values(
                        attempt=recovery_attempt,
                        max_attempts=max_attempts,
                        last_error_json=_dump(error),
                        updated_at=now,
                    )
                )
            connection.execute(
                update(self._tasks)
                .where(self._tasks.c.task_id == task_key)
                .values(
                    status="queued",
                    error_json=_dump(error),
                    attempt=recovery_attempt,
                    worker_id=None,
                    lease_expires_at=None,
                    updated_at=now,
                    version=int(current.get("version") or 0) + 1,
                )
            )
            row = connection.execute(
                select(self._tasks).where(self._tasks.c.task_id == task_key)
            ).mappings().first()
            recovered = True
        record = self._record(row) if row is not None else None
        if record is not None and recovered:
            record["recovered_after_worker_loss"] = True
            self.append_event(
                task_key,
                self._bounded(event_type, 80) or "requeued_after_worker_loss",
                status="queued",
                message=self._bounded(
                    event_message or "Worker 已退出，任务已通过 outbox 恢复到队列",
                    500,
                ),
                payload={
                    "reason": reason_kind,
                    "attempt": recovery_attempt,
                    "max_attempts": max_attempts,
                    "message_id": recovery_message_id,
                    "worker_id": worker_key,
                },
            )
        return record

    def recover_expired_leases(self, limit: int = 100) -> list[dict[str, object]]:
        """Recover running SQL tasks whose Redis claim is no longer present.

        The SQL row is changed to queued and a fresh outbox intent is inserted
        in the same transaction. Redis publication remains a separate, retryable
        step handled by the worker's normal outbox relay.
        """
        self._require_engine()
        bounded = max(1, min(int(limit), 500))
        now = datetime.now(UTC)
        with self._engine.connect() as connection:
            candidates = connection.execute(
                select(self._tasks)
                .where(self._tasks.c.status == "running")
                .where(self._tasks.c.lease_expires_at.is_not(None))
                .order_by(self._tasks.c.updated_at)
                .limit(bounded * 2)
            ).mappings().all()

        recovered: list[dict[str, object]] = []
        for candidate in candidates:
            candidate_expiry = self._parse_timestamp(candidate.get("lease_expires_at"))
            if candidate_expiry is None or candidate_expiry > now.timestamp():
                continue
            task_id = str(candidate.get("task_id", "")).strip()
            if not task_id:
                continue
            recovery: dict[str, object] | None = None
            with self._engine.begin() as connection:
                current = connection.execute(
                    select(self._tasks)
                    .where(self._tasks.c.task_id == task_id)
                    .with_for_update()
                ).mappings().first()
                if current is None or str(current.get("status", "")) != "running":
                    continue
                lease_timestamp = self._parse_timestamp(current.get("lease_expires_at"))
                if lease_timestamp is None or lease_timestamp > datetime.now(UTC).timestamp():
                    continue
                attempt = max(1, int(current.get("attempt") or 1))
                max_attempts = self._bounded_attempts(int(current.get("max_attempts") or 3))
                pending = connection.execute(
                    select(self._task_dispatch_outbox)
                    .where(self._task_dispatch_outbox.c.task_id == task_id)
                    .where(self._task_dispatch_outbox.c.status == "pending")
                    .order_by(self._task_dispatch_outbox.c.created_at)
                ).mappings().first()
                message_id = str(pending.get("message_id", "")) if pending is not None else uuid4().hex
                error = {
                    "kind": "WORKER_LEASE_EXPIRED",
                    "message": "SQL worker lease expired; task was returned to the queue",
                    "worker_id": str(current.get("worker_id") or ""),
                    "message_id": message_id,
                }
                if pending is None:
                    connection.execute(
                        insert(self._task_dispatch_outbox).values(
                            message_id=message_id,
                            task_id=task_id,
                            kind=self._bounded(current.get("kind"), 80),
                            payload_json=str(current.get("payload_json") or "{}"),
                            attempt=attempt,
                            max_attempts=max_attempts,
                            status="pending",
                            replay_source_task_id=None,
                            replay_request_id=None,
                            last_error_json=_dump(error),
                            created_at=now.isoformat(),
                            updated_at=now.isoformat(),
                            published_at=None,
                            receipt_state="PENDING",
                            receipt_key=None,
                            receipt_sha256=None,
                            receipt_at=None,
                            publish_attempts=0,
                        )
                    )
                else:
                    connection.execute(
                        update(self._task_dispatch_outbox)
                        .where(self._task_dispatch_outbox.c.message_id == message_id)
                        .values(
                            kind=self._bounded(current.get("kind"), 80),
                            payload_json=str(current.get("payload_json") or "{}"),
                            attempt=attempt,
                            max_attempts=max_attempts,
                            last_error_json=_dump(error),
                            updated_at=now.isoformat(),
                        )
                    )
                connection.execute(
                    update(self._tasks)
                    .where(self._tasks.c.task_id == task_id)
                    .values(
                        status="queued",
                        error_json=_dump(error),
                        worker_id=None,
                        lease_expires_at=None,
                        updated_at=now.isoformat(),
                        version=int(current.get("version") or 0) + 1,
                    )
                )
                recovery = {
                    "task_id": task_id,
                    "message_id": message_id,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "worker_id": str(current.get("worker_id") or ""),
                }
            if recovery is not None:
                self.append_event(
                    task_id,
                    "requeued_after_sql_lease_expiry",
                    status="queued",
                    message="SQL 租约过期，任务已通过 outbox 恢复到队列",
                    payload={
                        "attempt": recovery["attempt"],
                        "max_attempts": recovery["max_attempts"],
                        "message_id": recovery["message_id"],
                        "worker_id": recovery["worker_id"],
                    },
                )
                recovered.append(recovery)
            if len(recovered) >= bounded:
                break
        return recovered

    def dead_letter(
        self,
        task_id: str,
        *,
        error: Any,
        attempt: int,
        dead_letter: dict[str, object] | None = None,
        expected_worker_id: str | None = None,
    ) -> dict[str, object] | None:
        """Persist the terminal SQL projection for a task sent to the DLQ."""
        record = self.sync(
            task_id,
            status="dead_lettered",
            attempt=attempt,
            worker_id=None,
            lease_expires_at=None,
            dead_lettered_at=_now(),
            error=error,
            cancel_requested=False,
            expected_worker_id=(
                expected_worker_id if expected_worker_id is not None else _UNSET
            ),
        )
        if record is not None:
            self.append_event(
                task_id,
                "dead_lettered",
                status="dead_lettered",
                message="任务已进入死信队列",
                payload={
                    "error": error,
                    "dead_letter": self._dead_letter_reference(dead_letter),
                },
                expected_version=int(record["version"]),
            )
        return record

    def dead_letters(self, limit: int = 100) -> list[dict[str, object]]:
        """Return durable dead-letter task records for operator inspection."""
        return self.list(limit, status="dead_lettered")

    def reserve_dead_letter_replay(
        self,
        source_task_id: str,
        *,
        replay_task_id: str,
        request_id: str,
        actor: str,
        reason: str,
    ) -> dict[str, object]:
        """Reserve one audited replay request with a globally unique request id."""
        self._require_engine()
        source_id = self._required_text(source_task_id, "source_task_id")[:160]
        replay_id = self._required_text(replay_task_id, "replay_task_id")[:160]
        request_key = self._required_text(request_id, "request_id")[:160]
        actor_value = self._required_text(actor, "actor")[:120]
        reason_value = self._required_text(reason, "reason")[:500]
        now = _now()
        replay_events: list[dict[str, object]] = []
        result: dict[str, object] | None = None
        try:
            with self._engine.begin() as connection:
                existing = connection.execute(
                    select(self._dead_letter_replays).where(
                        self._dead_letter_replays.c.request_id == request_key
                    ).with_for_update()
                ).mappings().first()
                if existing is not None:
                    if str(existing["source_task_id"]) != source_id:
                        raise ValueError("replay request_id is already assigned to another task")
                    target = connection.execute(
                        select(self._tasks)
                        .where(self._tasks.c.task_id == str(existing["replay_task_id"]))
                        .with_for_update()
                    ).mappings().first()
                    if target is not None:
                        replay_events.extend(
                            self._settle_dead_letter_replay_for_task(connection, target)
                        )
                    current = connection.execute(
                        select(self._dead_letter_replays).where(
                            self._dead_letter_replays.c.request_id == request_key
                        )
                    ).mappings().first()
                    result = self._replay_record(current or existing)
                    result["deduplicated"] = True
                else:
                    source = connection.execute(
                    select(self._tasks).where(self._tasks.c.task_id == source_id)
                    ).mappings().first()
                    if source is None:
                        raise KeyError(source_id)
                    if str(source["status"]) != "dead_lettered":
                        raise ValueError("only dead-lettered tasks can be replayed")
                    values = {
                        "request_id": request_key,
                        "source_task_id": source_id,
                        "replay_task_id": replay_id,
                        "actor": actor_value,
                        "reason": reason_value,
                        "status": "requested",
                        "source_attempt": int(source.get("attempt") or 0),
                        "source_max_attempts": int(source.get("max_attempts") or 3),
                        "result_json": "{}",
                        "error_json": "{}",
                        "created_at": now,
                        "updated_at": now,
                    }
                    connection.execute(insert(self._dead_letter_replays).values(**values))
                    replay_events.append(
                        self._insert_event_row(
                            connection,
                            source_id,
                            "dead_letter_replay_requested",
                            status="dead_lettered",
                            message="已登记死信任务重放请求",
                            payload=self._replay_event_payload(
                                values,
                                status="requested",
                                result={},
                                error={},
                            ),
                        )
                    )
                    target = connection.execute(
                        select(self._tasks)
                        .where(self._tasks.c.task_id == replay_id)
                        .with_for_update()
                    ).mappings().first()
                    if target is not None:
                        replay_events.extend(
                            self._settle_dead_letter_replay_for_task(connection, target)
                        )
                    result = self._replay_record(values)
        except IntegrityError:
            # Two API/worker processes can pass the initial SELECT together.
            # Once the losing transaction is rolled back, the committed row is
            # the authoritative idempotency result.
            existing = self.get_dead_letter_replay(request_key)
            if existing is None:
                raise
            if str(existing["source_task_id"]) != source_id:
                raise ValueError("replay request_id is already assigned to another task")
            existing["deduplicated"] = True
            return existing
        for replay_event in replay_events:
            self._archive_event(replay_event)
        persisted = self.get_dead_letter_replay(request_key)
        if persisted is not None and result is not None and result.get("deduplicated"):
            persisted["deduplicated"] = True
        return persisted or result or {}

    def get_dead_letter_replay(self, request_id: str) -> dict[str, object] | None:
        """Read one replay reservation and its bounded audit projection."""
        self._require_engine()
        request_key = self._required_text(request_id, "request_id")[:160]
        self.reconcile_dead_letter_replays(request_id=request_key, limit=1)
        with self._engine.connect() as connection:
            row = connection.execute(
                select(self._dead_letter_replays).where(
                    self._dead_letter_replays.c.request_id == request_key
                )
            ).mappings().first()
        return None if row is None else self._replay_record(row)

    def dead_letter_replays(self, source_task_id: str, limit: int = 20) -> list[dict[str, object]]:
        """Return bounded replay audit records for one source task."""
        self._require_engine()
        bounded = max(1, min(int(limit), 100))
        source_key = self._required_text(source_task_id, "source_task_id")[:160]
        self.reconcile_dead_letter_replays(source_task_id=source_key, limit=bounded)
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(self._dead_letter_replays)
                .where(self._dead_letter_replays.c.source_task_id == source_key)
                .order_by(desc(self._dead_letter_replays.c.created_at))
                .limit(bounded)
            ).mappings().all()
        return [self._replay_record(row) for row in rows]

    def reconcile_dead_letter_replays(
        self,
        *,
        request_id: str | None = None,
        source_task_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, int]:
        """Settle pending replay audits from the durable target-task status."""
        self._require_engine()
        bounded = max(1, min(int(limit), 500))
        request_key = (
            self._required_text(request_id, "request_id")[:160]
            if request_id is not None
            else None
        )
        source_key = (
            self._required_text(source_task_id, "source_task_id")[:160]
            if source_task_id is not None
            else None
        )
        with self._engine.connect() as connection:
            statement = (
                select(self._dead_letter_replays)
                .where(self._dead_letter_replays.c.status.in_(_REPLAY_PENDING_STATUSES))
                .order_by(self._dead_letter_replays.c.updated_at)
                .limit(bounded)
            )
            if request_key is not None:
                statement = statement.where(self._dead_letter_replays.c.request_id == request_key)
            if source_key is not None:
                statement = statement.where(self._dead_letter_replays.c.source_task_id == source_key)
            candidates = connection.execute(statement).mappings().all()

        settlement_events: list[dict[str, object]] = []
        checked = len(candidates)
        settled = 0
        for candidate in candidates:
            with self._engine.begin() as connection:
                current = connection.execute(
                    select(self._dead_letter_replays)
                    .where(self._dead_letter_replays.c.request_id == str(candidate["request_id"]))
                    .with_for_update()
                ).mappings().first()
                if current is None or str(current.get("status")) not in _REPLAY_PENDING_STATUSES:
                    continue
                target = connection.execute(
                    select(self._tasks)
                    .where(self._tasks.c.task_id == str(current["replay_task_id"]))
                    .with_for_update()
                ).mappings().first()
                if target is None:
                    continue
                events = self._settle_dead_letter_replay_for_task(connection, target)
                settled += len(events)
                settlement_events.extend(events)

        for event in settlement_events:
            self._archive_event(event)
        return {
            "checked": checked,
            "settled": settled,
            "events": len(settlement_events),
        }

    def finish_dead_letter_replay(
        self,
        request_id: str,
        *,
        status: str,
        result: Any = None,
        error: Any = None,
    ) -> dict[str, object]:
        """Commit the replay outcome and its immutable source-task audit event."""
        self._require_engine()
        request_key = self._required_text(request_id, "request_id")[:160]
        normalized_status = self._bounded(status, 40)
        if normalized_status not in {"queued", "failed"}:
            raise ValueError("dead-letter replay status must be queued or failed")
        now = _now()
        self.reconcile_dead_letter_replays(request_id=request_key, limit=1)
        replay_events: list[dict[str, object]] = []
        result_record: dict[str, object] | None = None
        with self._engine.begin() as connection:
            current = connection.execute(
                select(self._dead_letter_replays).where(
                    self._dead_letter_replays.c.request_id == request_key
                ).with_for_update()
            ).mappings().first()
            if current is None:
                raise KeyError(request_key)
            if str(current["status"]) == "requested":
                target = connection.execute(
                    select(self._tasks)
                    .where(self._tasks.c.task_id == str(current["replay_task_id"]))
                    .with_for_update()
                ).mappings().first()
                if target is not None and str(target.get("status")) in _TERMINAL_STATUSES:
                    replay_events.extend(
                        self._settle_dead_letter_replay_for_task(connection, target)
                    )
                else:
                    result_value = result if result is not None else {}
                    error_value = error if error is not None else {}
                    effective_status = normalized_status
                    if target is not None:
                        # A task row that already exists is still recoverable
                        # through the outbox. Keep its audit pending instead of
                        # permanently recording a control-plane publish error.
                        effective_status = "queued"
                    values = {
                        "status": effective_status,
                        "result_json": _dump(self._compact_replay_result(result_value)),
                        "error_json": _dump(self._compact_replay_error(error_value)),
                        "updated_at": now,
                    }
                    connection.execute(
                        update(self._dead_letter_replays)
                        .where(self._dead_letter_replays.c.request_id == request_key)
                        .values(**values)
                    )
                    current_values = dict(current)
                    current_values.update(values)
                    replay_events.append(
                        self._insert_event_row(
                            connection,
                            current["source_task_id"],
                            (
                                "dead_letter_replayed"
                                if effective_status == "queued"
                                else "dead_letter_replay_failed"
                            ),
                            status="dead_lettered",
                            message=(
                                "死信任务已重新排队"
                                if effective_status == "queued"
                                else "死信任务重放失败"
                            ),
                            payload=self._replay_event_payload(
                                current_values,
                                status=effective_status,
                                result=result_value,
                                error=error_value,
                            ),
                        )
                    )
            result_record = self._replay_record(
                connection.execute(
                    select(self._dead_letter_replays).where(
                        self._dead_letter_replays.c.request_id == request_key
                    )
                ).mappings().first()
                or current
            )
        for replay_event in replay_events:
            self._archive_event(replay_event)
        return self.get_dead_letter_replay(request_key) or result_record or {}

    def _settle_dead_letter_replay_for_task(
        self,
        connection: Any,
        task: Any,
    ) -> list[dict[str, object]]:
        """Settle every pending audit row that points at one terminal task."""
        task_status = str(task.get("status") or "").strip().lower()
        if task_status not in _TERMINAL_STATUSES:
            return []
        rows = connection.execute(
            select(self._dead_letter_replays)
            .where(self._dead_letter_replays.c.replay_task_id == str(task["task_id"]))
            .where(self._dead_letter_replays.c.status.in_(_REPLAY_PENDING_STATUSES))
            .order_by(self._dead_letter_replays.c.created_at)
            .with_for_update()
        ).mappings().all()
        if not rows:
            return []

        result_summary = self._task_replay_result_summary(task)
        error_summary = self._compact_replay_error(_load(task.get("error_json")))
        message = {
            "completed": "死信重放目标任务已完成",
            "partial": "死信重放目标任务已部分完成",
            "failed": "死信重放目标任务已失败",
            "blocked": "死信重放目标任务已阻断",
            "cancelled": "死信重放目标任务已取消",
            "interrupted": "死信重放目标任务已中断",
            "dead_lettered": "死信重放目标任务再次进入死信",
        }.get(task_status, "死信重放目标任务已结束")
        events: list[dict[str, object]] = []
        for row in rows:
            values = {
                "status": task_status,
                "result_json": _dump(result_summary),
                "error_json": _dump(error_summary),
                "updated_at": _now(),
            }
            connection.execute(
                update(self._dead_letter_replays)
                .where(self._dead_letter_replays.c.request_id == str(row["request_id"]))
                .values(**values)
            )
            current = dict(row)
            current.update(values)
            if self._replay_settlement_event_exists(
                connection,
                source_task_id=str(row["source_task_id"]),
                request_id=str(row["request_id"]),
            ):
                continue
            events.append(
                self._insert_event_row(
                    connection,
                    row["source_task_id"],
                    "dead_letter_replay_settled",
                    status=task_status,
                    message=message,
                    payload=self._replay_event_payload(
                        current,
                        status=task_status,
                        result=result_summary,
                        error=error_summary,
                    ),
                )
            )
        return events

    def _replay_settlement_event_exists(
        self,
        connection: Any,
        *,
        source_task_id: str,
        request_id: str,
    ) -> bool:
        rows = connection.execute(
            select(self._events.c.payload_json)
            .where(self._events.c.task_id == str(source_task_id))
            .where(self._events.c.event_type == "dead_letter_replay_settled")
            .order_by(desc(self._events.c.event_id))
            .limit(500)
        ).all()
        for (payload_json,) in rows:
            payload = _load(payload_json)
            if isinstance(payload, dict) and str(payload.get("request_id")) == request_id:
                return True
        return False

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()

    def _ensure_task_runtime_columns(self) -> None:
        """Add lifecycle and archive columns to databases from earlier slices."""
        if self._engine is None:
            return
        task_definitions = {
            "attempt": "INTEGER NOT NULL DEFAULT 0",
            "max_attempts": "INTEGER NOT NULL DEFAULT 3",
            "worker_id": "VARCHAR(160)",
            "lease_expires_at": "VARCHAR(40)",
            "dead_lettered_at": "VARCHAR(40)",
        }
        existing = {column["name"] for column in inspect(self._engine).get_columns("control_tasks")}
        for name, definition in task_definitions.items():
            if name in existing:
                continue
            try:
                with self._engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE control_tasks ADD COLUMN {name} {definition}"))
            except Exception:
                # Two app processes can initialize the same database together.
                # Ignore only the race where the other process added this exact
                # column; preserve all real migration failures.
                if name not in {column["name"] for column in inspect(self._engine).get_columns("control_tasks")}:
                    raise
        event_definitions = {
            "archive_key": "VARCHAR(500)",
            "archive_sha256": "VARCHAR(64)",
            "archive_bytes": "INTEGER",
            "archive_state": "VARCHAR(20) NOT NULL DEFAULT 'DISABLED'",
            "archive_attempts": "INTEGER NOT NULL DEFAULT 0",
            "archive_error": "VARCHAR(500)",
        }
        event_columns = {column["name"] for column in inspect(self._engine).get_columns("control_task_events")}
        for name, definition in event_definitions.items():
            if name in event_columns:
                continue
            try:
                with self._engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE control_task_events ADD COLUMN {name} {definition}"))
            except Exception:
                if name not in {column["name"] for column in inspect(self._engine).get_columns("control_task_events")}:
                    raise
        dispatch_receipt_definitions = {
            "receipt_state": "VARCHAR(40)",
            "receipt_key": "VARCHAR(320)",
            "receipt_sha256": "VARCHAR(64)",
            "receipt_at": "VARCHAR(40)",
            "publish_attempts": "INTEGER NOT NULL DEFAULT 0",
        }
        dispatch_columns = {
            column["name"]
            for column in inspect(self._engine).get_columns("control_task_dispatch_outbox")
        }
        for name, definition in dispatch_receipt_definitions.items():
            if name in dispatch_columns:
                continue
            try:
                with self._engine.begin() as connection:
                    connection.execute(
                        text(
                            "ALTER TABLE control_task_dispatch_outbox "
                            f"ADD COLUMN {name} {definition}"
                        )
                    )
            except Exception:
                if name not in {
                    column["name"]
                    for column in inspect(self._engine).get_columns("control_task_dispatch_outbox")
                }:
                    raise
        self._ensure_task_runtime_indexes()

    def _ensure_task_runtime_indexes(self) -> None:
        """Create partial uniqueness guards when upgrading an old database."""
        if self._engine is None:
            return
        statements = (
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS {ACTIVE_IDEMPOTENCY_INDEX}
            ON control_tasks (idempotency_key)
            WHERE idempotency_key IS NOT NULL
              AND status IN ('queued', 'running', 'cancelling', 'pending')
            """,
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS {PENDING_DISPATCH_INDEX}
            ON control_task_dispatch_outbox (task_id)
            WHERE status = 'pending'
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_control_task_dispatch_outbox_receipt_state
            ON control_task_dispatch_outbox (receipt_state, updated_at)
            """,
        )
        with self._engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))

    def _require_engine(self) -> None:
        if self._engine is None:
            raise TaskStoreError(self._init_error or "task store is disabled")

    def _backend_name(self) -> str:
        return self.database_url.split(":", 1)[0].lower() if self.database_url else "none"

    @staticmethod
    def _required_text(value: Any, field: str) -> str:
        result = str(value or "").strip()
        if not result:
            raise ValueError(f"{field} must not be empty")
        return result

    @staticmethod
    def _bounded(value: Any, size: int) -> str:
        return str(value or "").strip()[:size]

    @staticmethod
    def _bounded_attempts(value: Any, *, allow_zero: bool = False) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError("task attempts must be an integer") from error
        minimum = 0 if allow_zero else 1
        if parsed < minimum or parsed > 10:
            raise ValueError("task attempts must be between 1 and 10")
        return parsed

    @staticmethod
    def _parse_timestamp(value: Any) -> float | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw).astimezone(UTC).timestamp()
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _dispatch_record(row: Any) -> dict[str, object]:
        status = str(row.get("status") or "").strip().lower()
        receipt_state = str(row.get("receipt_state") or "").strip().upper()
        if not receipt_state:
            receipt_state = "PENDING" if status == "pending" else "LEGACY"
        return {
            "message_id": row["message_id"],
            "task_id": row["task_id"],
            "kind": row["kind"],
            "payload": _load(row["payload_json"]),
            "attempt": int(row.get("attempt") or 1),
            "max_attempts": int(row.get("max_attempts") or 3),
            "status": row["status"],
            "replay_source_task_id": row.get("replay_source_task_id"),
            "replay_request_id": row.get("replay_request_id"),
            "last_error": _load(row["last_error_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "published_at": row.get("published_at"),
            "receipt": {
                "state": receipt_state,
                "receipt_key": str(row.get("receipt_key") or "") or None,
                "sha256": str(row.get("receipt_sha256") or "") or None,
                "observed_at": row.get("receipt_at"),
                "publish_attempts": int(row.get("publish_attempts") or 0),
            },
        }

    @staticmethod
    def _validate_dispatch_envelope(
        dispatch: dict[str, object],
        envelope: dict[str, object],
    ) -> None:
        """Ensure a Redis adapter cannot acknowledge a different delivery."""
        for field in ("message_id", "task_id", "kind"):
            expected = str(dispatch.get(field, "")).strip()
            actual = str(envelope.get(field, "")).strip()
            if not expected or actual != expected:
                raise TaskStoreError(f"task publisher returned a mismatched {field}")

    def _existing_dispatch_envelope(
        self,
        publisher: Any,
        dispatch: dict[str, object],
    ) -> dict[str, object] | None:
        """Confirm a surviving fixed delivery before attempting a publish."""
        inspector = getattr(publisher, "inspect_delivery", None)
        if not callable(inspector):
            return None
        delivery = inspector(
            str(dispatch["task_id"]),
            str(dispatch["message_id"]),
            kind=str(dispatch["kind"]),
            attempt=int(dispatch.get("attempt") or 1),
            max_attempts=int(dispatch.get("max_attempts") or 3),
            limit=500,
        )
        if not isinstance(delivery, dict) or not bool(delivery.get("available")):
            raise TaskStoreError("task delivery inspection was not available")
        if int(delivery.get("mismatched_count", 0) or 0) > 0:
            raise TaskStoreError("Redis contains a mismatched fixed task delivery")
        exact_count = int(delivery.get("exact_count", 0) or 0)
        if exact_count == 0:
            return None
        if exact_count > 1:
            raise TaskStoreError("Redis contains duplicate fixed task deliveries")
        locations = delivery.get("matched_by_location")
        if not isinstance(locations, dict):
            raise TaskStoreError("task delivery inspection omitted locations")
        if int(locations.get("dead_letter", 0) or 0) > 0:
            raise TaskStoreError("fixed task delivery is already in the dead-letter queue")
        if not (
            int(locations.get("queued", 0) or 0)
            or int(locations.get("processing", 0) or 0)
        ):
            raise TaskStoreError("task delivery inspection returned an invalid location")
        return self._dispatch_envelope(dispatch)

    @staticmethod
    def _dispatch_envelope(dispatch: dict[str, object]) -> dict[str, object]:
        """Build the internal fixed-delivery projection without SQL fields."""
        return {
            "message_id": dispatch.get("message_id"),
            "task_id": dispatch.get("task_id"),
            "kind": dispatch.get("kind"),
            "payload": dispatch.get("payload") or {},
            "attempt": int(dispatch.get("attempt") or 1),
            "max_attempts": int(dispatch.get("max_attempts") or 3),
        }

    @staticmethod
    def _record(row: Any) -> dict[str, object]:
        return {
            "task_id": row["task_id"],
            "kind": row["kind"],
            "title": row["title"],
            "status": row["status"],
            "payload": _load(row["payload_json"]),
            "result": _load(row["result_json"]),
            "error": _load(row["error_json"]),
            "progress": _load(row["progress_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "retry_of": row["retry_of"],
            "idempotency_key": row["idempotency_key"],
            "cancel_requested": bool(row["cancel_requested"]),
            "version": int(row["version"] or 0),
            "attempt": int(row.get("attempt") or 0),
            "max_attempts": int(row.get("max_attempts") or 3),
            "worker_id": row.get("worker_id"),
            "lease_expires_at": row.get("lease_expires_at"),
            "dead_lettered_at": row.get("dead_lettered_at"),
        }

    @staticmethod
    def _dead_letter_reference(value: dict[str, object] | None) -> dict[str, object] | None:
        if not isinstance(value, dict):
            return None
        keys = (
            "dead_letter_id",
            "message_id",
            "task_id",
            "kind",
            "attempt",
            "max_attempts",
            "dead_lettered_at",
        )
        reference = {key: value[key] for key in keys if key in value}
        return reference or None

    @staticmethod
    def _compact_archive_reference(value: Any) -> dict[str, object] | None:
        if not isinstance(value, dict):
            return None
        keys = ("contract_version", "key", "sha256", "bytes", "state")
        reference = {key: value[key] for key in keys if key in value}
        return reference or None

    @classmethod
    def _compact_replay_result(cls, value: Any) -> dict[str, object]:
        """Keep replay audit results bounded and free of the task payload."""
        if not isinstance(value, dict):
            return {"summary": str(value)[:160]} if value is not None else {}
        compact: dict[str, object] = {}
        for key, limit in (("status", 40), ("task_id", 160), ("kind", 80)):
            if key in value and value[key] is not None:
                compact[key] = str(value[key])[:limit]
        if "deduplicated" in value:
            compact["deduplicated"] = bool(value["deduplicated"])
        archive = cls._compact_archive_reference(value.get("archive"))
        if archive is not None:
            compact["archive"] = archive
        return compact

    @staticmethod
    def _compact_replay_error(value: Any) -> dict[str, object]:
        """Keep only operator-useful error fields in replay audit state."""
        if not isinstance(value, dict):
            return {"message": str(value)[:500]} if value is not None else {}
        compact: dict[str, object] = {}
        for key, limit in (("kind", 80), ("code", 80), ("message", 500)):
            if key in value and value[key] is not None:
                compact[key] = str(value[key])[:limit]
        if "retryable" in value:
            compact["retryable"] = bool(value["retryable"])
        return compact

    @classmethod
    def _task_replay_result_summary(cls, task: Any) -> dict[str, object]:
        summary: dict[str, object] = {
            "status": str(task.get("status") or "")[:40],
            "task_id": str(task.get("task_id") or "")[:160],
            "version": int(task.get("version") or 0),
            "attempt": int(task.get("attempt") or 0),
        }
        result = _load(task.get("result_json"))
        if isinstance(result, dict):
            archive = cls._compact_archive_reference(result.get("archive"))
            if archive is not None:
                summary["archive"] = archive
        return summary

    @staticmethod
    def _replay_record(row: Any) -> dict[str, object]:
        return {
            "request_id": row["request_id"],
            "source_task_id": row["source_task_id"],
            "replay_task_id": row["replay_task_id"],
            "actor": row["actor"],
            "reason": row["reason"],
            "status": row["status"],
            "source_attempt": int(row.get("source_attempt") or 0),
            "source_max_attempts": int(row.get("source_max_attempts") or 3),
            "result": _load(row["result_json"]),
            "error": _load(row["error_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _replay_event_payload(
        row: Any,
        *,
        status: str,
        result: Any,
        error: Any,
    ) -> dict[str, object]:
        return {
            "request_id": row["request_id"],
            "actor": row["actor"],
            "reason": row["reason"],
            "source_task_id": row["source_task_id"],
            "replay_task_id": row["replay_task_id"],
            "source_attempt": int(row.get("source_attempt") or 0),
            "source_max_attempts": int(row.get("source_max_attempts") or 3),
            "status": status,
            "result": TaskStore._compact_replay_result(result),
            "error": TaskStore._compact_replay_error(error),
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dump(value: Any) -> str:
    if value is None:
        return "{}"
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _load(value: Any) -> Any:
    if not value:
        return {}
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"raw": str(value)}
