"""SQL persistence for bounded Redis task-delivery receipts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update

from .task_store_codec import dump as _dump


class TaskDispatchReceiptMixin:
    """Keep publication acknowledgements and Redis receipt metadata together."""

    def mark_dispatch_published(
        self,
        message_id: str,
        *,
        receipt: dict[str, object] | None = None,
    ) -> bool:
        """Persist SQL publication state and the bounded Redis receipt together."""
        self._require_engine()
        key = self._required_text(message_id, "message_id")[:64]
        now = _now()
        receipt_values = self._normalized_dispatch_receipt(receipt, observed_at=now)
        with self._engine.begin() as connection:
            result = connection.execute(
                update(self._task_dispatch_outbox)
                .where(self._task_dispatch_outbox.c.message_id == key)
                .where(self._task_dispatch_outbox.c.status == "pending")
                .values(
                    status="published",
                    published_at=now,
                    updated_at=now,
                    receipt_state=receipt_values["state"],
                    receipt_key=receipt_values["receipt_key"],
                    receipt_sha256=receipt_values["sha256"],
                    receipt_at=receipt_values["observed_at"],
                )
            )
        return bool(result.rowcount)

    def update_dispatch_receipt(
        self,
        message_id: str,
        receipt: dict[str, object],
    ) -> bool:
        """Refresh a published row after Redis list repair or verification."""
        self._require_engine()
        key = self._required_text(message_id, "message_id")[:64]
        now = _now()
        receipt_values = self._normalized_dispatch_receipt(receipt, observed_at=now)
        with self._engine.begin() as connection:
            result = connection.execute(
                update(self._task_dispatch_outbox)
                .where(self._task_dispatch_outbox.c.message_id == key)
                .where(self._task_dispatch_outbox.c.status == "published")
                .values(
                    receipt_state=receipt_values["state"],
                    receipt_key=receipt_values["receipt_key"],
                    receipt_sha256=receipt_values["sha256"],
                    receipt_at=receipt_values["observed_at"],
                    updated_at=now,
                )
            )
        return bool(result.rowcount)

    def mark_dispatch_failed(self, message_id: str, error: Any) -> bool:
        """Keep a delivery intent pending while recording a bounded failure."""
        self._require_engine()
        key = self._required_text(message_id, "message_id")[:64]
        now = _now()
        with self._engine.begin() as connection:
            result = connection.execute(
                update(self._task_dispatch_outbox)
                .where(self._task_dispatch_outbox.c.message_id == key)
                .where(self._task_dispatch_outbox.c.status == "pending")
                .values(
                    status="pending",
                    last_error_json=_dump(error),
                    publish_attempts=self._task_dispatch_outbox.c.publish_attempts + 1,
                    updated_at=now,
                )
            )
        return bool(result.rowcount)

    @staticmethod
    def _normalized_dispatch_receipt(
        receipt: dict[str, object] | None,
        *,
        observed_at: str,
    ) -> dict[str, object]:
        """Keep Redis receipt metadata bounded and free from task payload."""
        value = receipt if isinstance(receipt, dict) else {}
        state = str(value.get("state") or "LEGACY").strip().upper()[:40] or "LEGACY"
        receipt_key = str(value.get("receipt_key") or "").strip()[:320] or None
        digest = str(value.get("sha256") or value.get("receipt_sha256") or "").strip().lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            digest = None
        raw_observed = str(value.get("observed_at") or observed_at).strip()[:40]
        return {
            "state": state,
            "receipt_key": receipt_key,
            "sha256": digest,
            "observed_at": raw_observed or observed_at,
        }

    def _publisher_delivery_receipt(
        self,
        publisher: Any,
        dispatch: dict[str, object],
    ) -> dict[str, object] | None:
        getter = getattr(publisher, "get_delivery_receipt", None)
        if not callable(getter):
            return None
        receipt = getter(
            str(dispatch["message_id"]),
            task_id=str(dispatch["task_id"]),
            kind=str(dispatch["kind"]),
            attempt=int(dispatch.get("attempt") or 1),
            max_attempts=int(dispatch.get("max_attempts") or 3),
        )
        if not isinstance(receipt, dict):
            raise self._dispatch_store_error("task publisher returned an invalid delivery receipt")
        if str(receipt.get("state", "")).upper() != "REDIS_ACCEPTED":
            raise self._dispatch_store_error("Redis delivery receipt was not fully accepted")
        envelope_sha256 = str(receipt.get("sha256") or "").strip().lower()
        marker_sha256 = str(receipt.get("receipt_sha256") or "").strip().lower()
        if marker_sha256 and envelope_sha256 and marker_sha256 != envelope_sha256:
            raise self._dispatch_store_error("Redis delivery receipt hash does not match the envelope")
        contract = str(receipt.get("receipt_contract") or "").strip().upper()
        if contract in {"INVALID", "UNKNOWN"}:
            raise self._dispatch_store_error("Redis delivery receipt contract is invalid")
        return receipt

    @staticmethod
    def _dispatch_store_error(message: str) -> RuntimeError:
        # Avoid a module cycle while preserving TaskStoreError at runtime.
        from .task_store import TaskStoreError

        return TaskStoreError(message)


def _now() -> str:
    return datetime.now(UTC).isoformat()
