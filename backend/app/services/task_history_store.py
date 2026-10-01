"""Formal history metadata persistence used by the task store.

The mixin keeps history files, raw-response references, and Parquet mirror
state outside the task-control repository without changing its SQL table
ownership or transaction boundaries.
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import delete, insert, select, update

from .task_store_codec import dump as _dump
from .task_store_codec import now as _now


class TaskHistoryStoreMixin:
    """History metadata operations backed by tables owned by ``TaskStore``."""

    def sync_history_datasets(self, datasets: Iterable[dict[str, Any]]) -> dict[str, object]:
        """Reconcile verified files into the formal history metadata repository."""
        self._require_engine()
        by_storage_key: dict[str, dict[str, object]] = {}
        for dataset in datasets:
            row = self._history_dataset_values(dataset)
            by_storage_key[str(row["storage_key"])] = row
        rows = list(by_storage_key.values())
        keys = tuple(str(row["storage_key"]) for row in rows)
        with self._engine.begin() as connection:
            existing = {
                str(row["storage_key"]): dict(row)
                for row in connection.execute(select(self._history_records)).mappings()
            }
            for row in rows:
                storage_key = str(row["storage_key"])
                previous = existing.get(storage_key, {})
                record = self._history_record_values(
                    row,
                    raw_response_state=str(previous.get("raw_response_state") or "UNKNOWN"),
                    raw_response_count=int(previous.get("raw_response_count") or 0),
                    parquet_state=str(previous.get("parquet_state") or "UNKNOWN"),
                    parquet_key=previous.get("parquet_key"),
                    parquet_sha256=previous.get("parquet_sha256"),
                    last_error=previous.get("last_error"),
                    created_at=str(previous.get("created_at") or _now()),
                )
                self._upsert_history_record(connection, record)
                self._upsert_history_legacy(connection, row)
            if keys:
                connection.execute(delete(self._history_records).where(self._history_records.c.storage_key.not_in(keys)))
                connection.execute(
                    delete(self._history_datasets).where(self._history_datasets.c.storage_key.not_in(keys))
                )
            else:
                connection.execute(delete(self._history_records))
                connection.execute(delete(self._history_datasets))
        return {
            "status": "ready",
            "dataset_count": len(rows),
            "synced_count": len(rows),
        }

    def commit_history_dataset(
        self,
        dataset: dict[str, Any],
        *,
        raw_responses: Iterable[dict[str, Any]] = (),
        raw_response_state: str = "UNKNOWN",
        parquet_state: str = "PENDING",
    ) -> dict[str, object]:
        """Commit dataset metadata and raw-response references in one SQL transaction."""
        self._require_engine()
        row = self._history_dataset_values(dataset)
        storage_key = str(row["storage_key"])
        raw_rows = [
            self._history_raw_response_values(value, dataset_id=str(row["dataset_id"]), storage_key=storage_key)
            for value in raw_responses
        ]
        now = _now()
        with self._engine.begin() as connection:
            previous = connection.execute(
                select(self._history_records.c.created_at)
                .where(self._history_records.c.storage_key == storage_key)
            ).mappings().first()
            record = self._history_record_values(
                row,
                raw_response_state=raw_response_state,
                raw_response_count=len(raw_rows),
                parquet_state=parquet_state,
                parquet_key=None,
                parquet_sha256=None,
                last_error=None,
                created_at=str(previous["created_at"] if previous else now),
            )
            self._upsert_history_record(connection, record)
            self._upsert_history_legacy(connection, row)
            for raw_row in raw_rows:
                self._upsert_history_raw_response(connection, raw_row)
        return self._history_record_projection(record)

    def sync_history_archive(self, archive: dict[str, Any]) -> dict[str, object]:
        """Persist Parquet outcome without hiding a failed optional mirror."""
        self._require_engine()
        raw_items = archive.get("items") if isinstance(archive, dict) else []
        items = [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
        status = str(archive.get("status", "FAILED")).strip().upper()
        message = str(archive.get("error") or archive.get("message") or "")[:1000]
        updates: dict[str, dict[str, object]] = {}
        for item in items:
            storage_key = str(item.get("storage_key") or "").strip()
            if not storage_key:
                continue
            state = str(item.get("state") or "").strip().upper()
            if state == "ARCHIVED":
                state = "READY"
            if state not in {"READY", "STALE", "MISSING", "FAILED"}:
                state = "FAILED"
            updates[storage_key] = {
                "parquet_state": state,
                "parquet_key": str(item.get("archive_key") or "") or None,
                "parquet_sha256": self._optional_digest(item.get("archive_sha256")),
                "last_error": None if state == "READY" else message or state,
            }
        with self._engine.begin() as connection:
            if not updates and status in {"FAILED", "PARTIAL"}:
                pending = connection.execute(
                    select(self._history_records.c.storage_key)
                    .where(self._history_records.c.parquet_state == "PENDING")
                ).mappings().all()
                updates = {
                    str(row["storage_key"]): {
                        "parquet_state": "FAILED",
                        "parquet_key": None,
                        "parquet_sha256": None,
                        "last_error": message or "Parquet archive failed",
                    }
                    for row in pending
                }
            updated_count = 0
            for storage_key, values in updates.items():
                current = connection.execute(
                    select(self._history_records)
                    .where(self._history_records.c.storage_key == storage_key)
                ).mappings().first()
                if current is None:
                    continue
                record_status = self._history_record_status(
                    raw_response_state=str(current["raw_response_state"]),
                    parquet_state=str(values["parquet_state"]),
                )
                connection.execute(
                    update(self._history_records)
                    .where(self._history_records.c.storage_key == storage_key)
                    .values(
                        parquet_state=values["parquet_state"],
                        parquet_key=values["parquet_key"],
                        parquet_sha256=values["parquet_sha256"],
                        last_error=values["last_error"],
                        record_status=record_status,
                        updated_at=_now(),
                    )
                )
                updated_count += 1
        return {
            "status": "ready" if status == "COMPLETED" else "degraded",
            "updated_count": updated_count,
            "requested_count": len(items),
        }

    def history_metadata_status(self) -> dict[str, object]:
        """Return the formal history metadata repository health and state counts."""
        if self._engine is None:
            return {
                "enabled": self.enabled,
                "required": self.required,
                "ok": False,
                "status": "unavailable",
                "backend": self._backend_name(),
                "dataset_count": 0,
                "updated_at": None,
            }
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    select(
                        self._history_records.c.record_status,
                        self._history_records.c.raw_response_count,
                        self._history_records.c.raw_response_state,
                        self._history_records.c.parquet_state,
                        self._history_records.c.updated_at,
                    )
                ).mappings().all()
        except Exception as error:  # pragma: no cover - depends on runtime state
            return {
                "enabled": self.enabled,
                "required": self.required,
                "ok": False,
                "status": "unreachable",
                "backend": self._backend_name(),
                "dataset_count": 0,
                "updated_at": None,
                "message": str(error),
            }
        updated_values = [str(row["updated_at"]) for row in rows if row.get("updated_at")]
        ready_count = sum(1 for row in rows if str(row["record_status"]) == "READY")
        degraded_count = sum(1 for row in rows if str(row["record_status"]) == "DEGRADED")
        raw_response_count = sum(int(row["raw_response_count"] or 0) for row in rows)
        return {
            "enabled": self.enabled,
            "required": self.required,
            "ok": True,
            "status": "degraded" if degraded_count else "ready" if rows else "empty",
            "backend": self._backend_name(),
            "dataset_count": len(rows),
            "ready_count": ready_count,
            "degraded_count": degraded_count,
            "raw_response_count": raw_response_count,
            "updated_at": max(updated_values) if updated_values else None,
        }

    @classmethod
    def _history_dataset_values(cls, dataset: dict[str, Any]) -> dict[str, object]:
        if not isinstance(dataset, dict):
            raise ValueError("history dataset metadata must be an object")
        storage_key = cls._bounded(dataset.get("storage_key"), 240)
        dataset_id = cls._bounded(dataset.get("dataset_id"), 320)
        if not storage_key or not dataset_id:
            raise ValueError("history dataset metadata requires storage_key and dataset_id")
        try:
            counts = {
                "row_count": int(dataset.get("row_count", 0)),
                "gap_count": int(dataset.get("gap_count", 0)),
                "duplicate_count": int(dataset.get("duplicate_count", 0)),
            }
        except (TypeError, ValueError) as error:
            raise ValueError("history dataset counts must be integers") from error
        if any(value < 0 for value in counts.values()):
            raise ValueError("history dataset counts must not be negative")
        digest = cls._bounded(dataset.get("content_sha256"), 64).lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("history dataset content_sha256 is invalid")
        return {
            "storage_key": storage_key,
            "dataset_id": dataset_id,
            "venue_id": cls._bounded(dataset.get("venue_id"), 80),
            "market_type": cls._bounded(dataset.get("market_type"), 40),
            "instrument_key": cls._bounded(dataset.get("instrument_key"), 180),
            "data_level": cls._bounded(dataset.get("data_level"), 40),
            "interval": cls._bounded(dataset.get("interval"), 20),
            "start_at": cls._bounded(dataset.get("start_at"), 40),
            "end_at": cls._bounded(dataset.get("end_at"), 40),
            "file_format": cls._bounded(dataset.get("file_format"), 20),
            "source": cls._bounded(dataset.get("source"), 500),
            **counts,
            "content_sha256": digest,
            "updated_at": _now(),
        }

    @classmethod
    def _history_record_values(
        cls,
        dataset: dict[str, object],
        *,
        raw_response_state: str,
        raw_response_count: int,
        parquet_state: str,
        parquet_key: object,
        parquet_sha256: object,
        last_error: object,
        created_at: str,
    ) -> dict[str, object]:
        raw_state = cls._state(raw_response_state, "raw_response_state")
        archive_state = cls._state(parquet_state, "parquet_state")
        count = int(raw_response_count)
        if count < 0:
            raise ValueError("raw_response_count must not be negative")
        return {
            **dataset,
            "record_status": cls._history_record_status(raw_response_state=raw_state, parquet_state=archive_state),
            "storage_state": "VERIFIED",
            "manifest_state": "VERIFIED",
            "raw_response_state": raw_state,
            "raw_response_count": count,
            "parquet_state": archive_state,
            "parquet_key": cls._bounded(parquet_key, 500) or None,
            "parquet_sha256": cls._optional_digest(parquet_sha256),
            "last_error": cls._bounded(last_error, 1000) or None,
            "created_at": str(created_at),
            "verified_at": str(dataset.get("updated_at") or _now()),
            "updated_at": _now(),
        }

    @staticmethod
    def _history_record_status(*, raw_response_state: str, parquet_state: str) -> str:
        if raw_response_state in {"FAILED", "MISSING", "STALE"} or parquet_state in {"FAILED", "MISSING", "STALE"}:
            return "DEGRADED"
        return "READY"

    @staticmethod
    def _state(value: object, field: str) -> str:
        normalized = str(value or "UNKNOWN").strip().upper()
        allowed = {"UNKNOWN", "NOT_CONFIGURED", "PENDING", "READY", "VERIFIED", "FAILED", "MISSING", "STALE"}
        if normalized not in allowed:
            raise ValueError(f"{field} has an unsupported state")
        return normalized

    @staticmethod
    def _optional_digest(value: object) -> str | None:
        digest = str(value or "").strip().lower()
        if not digest:
            return None
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("history archive digest is invalid")
        return digest

    @staticmethod
    def _history_record_projection(record: dict[str, object]) -> dict[str, object]:
        return {
            "storage_key": record["storage_key"],
            "dataset_id": record["dataset_id"],
            "record_status": record["record_status"],
            "storage_state": record["storage_state"],
            "manifest_state": record["manifest_state"],
            "raw_response_state": record["raw_response_state"],
            "raw_response_count": record["raw_response_count"],
            "parquet_state": record["parquet_state"],
            "updated_at": record["updated_at"],
        }

    def _upsert_history_record(self, connection: Any, record: dict[str, object]) -> None:
        storage_key = str(record["storage_key"])
        current = connection.execute(
            select(self._history_records.c.storage_key)
            .where(self._history_records.c.storage_key == storage_key)
        ).first()
        if current is None:
            connection.execute(insert(self._history_records).values(**record))
            return
        values = dict(record)
        values.pop("created_at", None)
        connection.execute(
            update(self._history_records)
            .where(self._history_records.c.storage_key == storage_key)
            .values(**values)
        )

    def _upsert_history_legacy(self, connection: Any, row: dict[str, object]) -> None:
        storage_key = str(row["storage_key"])
        values = dict(row)
        values.pop("storage_key", None)
        current = connection.execute(
            select(self._history_datasets.c.storage_key)
            .where(self._history_datasets.c.storage_key == storage_key)
        ).first()
        if current is None:
            connection.execute(insert(self._history_datasets).values(storage_key=storage_key, **values))
        else:
            connection.execute(
                update(self._history_datasets)
                .where(self._history_datasets.c.storage_key == storage_key)
                .values(**values)
            )

    def _upsert_history_raw_response(self, connection: Any, row: dict[str, object]) -> None:
        response_id = str(row["response_id"])
        current = connection.execute(
            select(self._history_raw_responses.c.response_id)
            .where(self._history_raw_responses.c.response_id == response_id)
        ).first()
        if current is None:
            connection.execute(insert(self._history_raw_responses).values(**row))
        else:
            values = dict(row)
            values.pop("created_at", None)
            connection.execute(
                update(self._history_raw_responses)
                .where(self._history_raw_responses.c.response_id == response_id)
                .values(**values)
            )

    @classmethod
    def _history_raw_response_values(
        cls,
        response: dict[str, Any],
        *,
        dataset_id: str,
        storage_key: str,
    ) -> dict[str, object]:
        if not isinstance(response, dict):
            raise ValueError("history raw response metadata must be an object")
        response_id = cls._required_text(response.get("response_id"), "response_id")[:160]
        archive_key = cls._required_text(response.get("archive_key"), "archive_key")[:500]
        request_path = cls._required_text(response.get("request_path"), "request_path")[:240]
        params = response.get("request_params", {})
        if not isinstance(params, dict):
            raise ValueError("history raw response request_params must be an object")
        payload_sha256 = cls._required_digest(response.get("payload_sha256"), "payload_sha256")
        content_sha256 = cls._required_digest(response.get("content_sha256"), "content_sha256")
        try:
            page_index = int(response.get("page_index", 0))
            size_bytes = int(response.get("size_bytes", 0))
        except (TypeError, ValueError) as error:
            raise ValueError("history raw response numeric fields are invalid") from error
        if page_index <= 0 or size_bytes < 0:
            raise ValueError("history raw response numeric fields are out of range")
        return {
            "response_id": response_id,
            "dataset_id": cls._required_text(dataset_id, "dataset_id")[:320],
            "storage_key": cls._required_text(storage_key, "storage_key")[:240],
            "venue_id": cls._bounded(response.get("venue_id"), 80),
            "market_type": cls._bounded(response.get("market_type"), 40),
            "instrument_key": cls._bounded(response.get("instrument_key"), 180),
            "native_symbol": cls._bounded(response.get("native_symbol"), 120),
            "interval": cls._bounded(response.get("interval"), 20),
            "page_index": page_index,
            "request_path": request_path,
            "request_params_json": _dump(params),
            "archive_key": archive_key,
            "payload_sha256": payload_sha256,
            "content_sha256": content_sha256,
            "size_bytes": size_bytes,
            "received_at": cls._required_text(response.get("received_at"), "received_at")[:40],
            "state": cls._state(response.get("state", "READY"), "raw_response_state"),
            "created_at": _now(),
        }

    @staticmethod
    def _required_digest(value: object, field: str) -> str:
        digest = str(value or "").strip().lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"{field} is invalid")
        return digest
