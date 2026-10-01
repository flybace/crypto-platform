"""Bounded Redis/SQL task-delivery auditing and safe recovery."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


CONTRACT_VERSION = "task-dispatch-consistency-v1"
STATES = (
    "PENDING",
    "QUEUED",
    "PROCESSING",
    "SETTLED",
    "DLQ",
    "MISSING",
    "DUPLICATE",
    "MISMATCHED",
    "UNKNOWN",
)
_UNSAFE_STATES = frozenset({"DLQ", "DUPLICATE", "MISMATCHED", "UNKNOWN"})
_NOT_READY_STATES = _UNSAFE_STATES | {"PENDING", "MISSING"}
_TERMINAL_TASK_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "blocked", "interrupted", "partial"}
)


class TaskDispatchConsistency:
    """Audit fixed SQL outbox deliveries without exposing task payloads."""

    def __init__(self, store: Any, queue: Any) -> None:
        self.store = store
        self.queue = queue

    def audit(self, *, limit: int = 100, repair: bool = False) -> dict[str, object]:
        bounded = max(1, min(int(limit), 500))
        try:
            candidates = self.store.dispatch_consistency_candidates(bounded)
        except Exception as error:
            return self._failed_report(repair=repair, error=error)

        items: list[dict[str, object]] = []
        repair_summary = {
            "requested": bool(repair),
            "attempted": 0,
            "repaired": 0,
            "skipped": 0,
            "failed": 0,
        }
        for candidate in candidates:
            item = self._inspect_candidate(candidate)
            if repair:
                item = self._repair_candidate(candidate, item, repair_summary)
            items.append(item)

        counts = {state: 0 for state in STATES}
        receipt_counts = {
            "VERIFIED": 0,
            "LEGACY": 0,
            "PENDING": 0,
            "SETTLED": 0,
            "UNVERIFIED": 0,
            "MISMATCHED": 0,
        }
        for item in items:
            state = str(item.get("state", "UNKNOWN"))
            counts[state if state in counts else "UNKNOWN"] += 1
            receipt_state = str(item.get("receipt_state", "LEGACY"))
            # Published deliveries must be judged against the current Redis
            # marker. SQL keeps the last accepted receipt, so it can be stale
            # after a Redis restore or partial key loss.
            if (
                str(item.get("outbox_status", "")).lower() == "published"
                and str(item.get("state", "")).upper() != "SETTLED"
            ):
                receipt_state = str(item.get("redis_receipt_state", receipt_state))
            receipt_counts[self._receipt_bucket(receipt_state)] += 1
        has_unknown = counts["UNKNOWN"] > 0
        receipt_not_ready = receipt_counts["UNVERIFIED"] > 0 or receipt_counts["MISMATCHED"] > 0
        not_ready = any(counts[state] > 0 for state in _NOT_READY_STATES) or receipt_not_ready
        status = "blocked" if has_unknown else "degraded" if not_ready else "ready"
        return {
            "contract_version": CONTRACT_VERSION,
            "enabled": True,
            "ok": not not_ready,
            "status": status,
            "checked": len(items),
            "counts": counts,
            "receipt_counts": receipt_counts,
            "repair": repair_summary,
            "items": items,
        }

    def audit_and_repair(self, *, limit: int = 100) -> dict[str, object]:
        """Run the worker-safe repair subset and return a bounded report."""
        return self.audit(limit=limit, repair=True)

    def _inspect_candidate(self, candidate: Mapping[str, object]) -> dict[str, object]:
        task_id = str(candidate.get("task_id", "")).strip()
        task_status = str(candidate.get("status", "")).strip().lower()
        kind = str(candidate.get("kind", "")).strip()
        dispatch = candidate.get("dispatch")
        item: dict[str, object] = {
            "task_id": task_id,
            "kind": kind,
            "task_status": task_status,
            "message_id": None,
            "outbox_status": None,
            "state": "MISSING",
            "reason": "sql_outbox_missing",
            "redis": None,
        }
        if not isinstance(dispatch, Mapping):
            return item
        item["receipt"] = self._safe_receipt(dispatch)
        item["receipt_state"] = (
            str(item["receipt"].get("state", "LEGACY"))
            if isinstance(item["receipt"], Mapping)
            else "LEGACY"
        )
        message_id = str(dispatch.get("message_id", "")).strip()
        outbox_status = str(dispatch.get("status", "")).strip().lower()
        item["message_id"] = message_id or None
        item["outbox_status"] = outbox_status or None
        try:
            delivery = self.queue.inspect_delivery(
                task_id,
                message_id,
                kind=kind,
                attempt=int(dispatch.get("attempt") or 1),
                max_attempts=int(dispatch.get("max_attempts") or 3),
                limit=500,
            )
        except Exception as error:
            item["state"] = "UNKNOWN"
            item["reason"] = "redis_inspection_failed"
            item["error_kind"] = self._error_kind(error)
            return item
        item["redis"] = self._safe_delivery(delivery)
        item["redis_receipt_state"] = self._redis_receipt_state(delivery)
        item["state"], item["reason"] = self._classify(
            task_status,
            outbox_status,
            delivery,
        )
        return item

    def _repair_candidate(
        self,
        candidate: Mapping[str, object],
        item: dict[str, object],
        summary: dict[str, int | bool],
    ) -> dict[str, object]:
        state = str(item.get("state", "UNKNOWN"))
        task_id = str(candidate.get("task_id", "")).strip()
        dispatch = candidate.get("dispatch")
        delivery = item.get("redis")
        repair: dict[str, object] = {"action": "none", "status": "not_needed"}

        if state in _UNSAFE_STATES:
            repair["status"] = "skipped_unsafe_state"
            summary["skipped"] = int(summary["skipped"]) + 1
        elif state == "PENDING" and isinstance(dispatch, Mapping):
            if self._delivery_is_unsafe(delivery):
                repair["status"] = "skipped_unsafe_delivery"
                summary["skipped"] = int(summary["skipped"]) + 1
            else:
                repair["action"] = "publish_pending_outbox"
                self._publish_pending(task_id, item, dispatch, repair, summary)
        elif state == "MISSING" and str(candidate.get("status", "")).lower() == "queued":
            if isinstance(dispatch, Mapping) and str(dispatch.get("status", "")).lower() == "published":
                if self._delivery_is_unsafe(delivery):
                    repair["status"] = "skipped_unsafe_delivery"
                    summary["skipped"] = int(summary["skipped"]) + 1
                else:
                    repair["action"] = "ensure_enqueued"
                    self._ensure_enqueued(candidate, item, dispatch, repair, summary)
            elif dispatch is None:
                repair["action"] = "stage_and_publish_outbox"
                self._stage_and_publish(task_id, item, repair, summary)
            else:
                repair["status"] = "skipped_unpublished_state"
                summary["skipped"] = int(summary["skipped"]) + 1
        elif (
            state in {"QUEUED", "PROCESSING"}
            and isinstance(dispatch, Mapping)
            and str(item.get("redis_receipt_state", "")).upper() != "REDIS_ACCEPTED"
        ):
            if self._delivery_has_unique_safe_envelope(delivery):
                repair["action"] = "refresh_delivery_receipt"
                self._ensure_enqueued(candidate, item, dispatch, repair, summary)
            else:
                repair["status"] = "skipped_unsafe_delivery"
                summary["skipped"] = int(summary["skipped"]) + 1
        else:
            repair["status"] = "skipped_not_safe_to_repair"
            summary["skipped"] = int(summary["skipped"]) + 1

        item["repair"] = repair
        if repair.get("status") != "repaired":
            return item

        refreshed = dict(candidate)
        if isinstance(dispatch, Mapping):
            refreshed["dispatch"] = dict(dispatch)
        else:
            refreshed["dispatch"] = self._latest_dispatch(task_id)
        post = self._inspect_candidate(refreshed)
        item["pre_state"] = state
        item["state"] = post.get("state", "UNKNOWN")
        item["reason"] = post.get("reason", "")
        item["message_id"] = post.get("message_id")
        item["outbox_status"] = post.get("outbox_status")
        item["redis"] = post.get("redis")
        item["receipt"] = post.get("receipt")
        item["receipt_state"] = post.get("receipt_state", "LEGACY")
        item["redis_receipt_state"] = post.get("redis_receipt_state", "UNKNOWN")
        if post.get("error_kind"):
            item["error_kind"] = post["error_kind"]
        return item

    def _publish_pending(
        self,
        task_id: str,
        item: Mapping[str, object],
        dispatch: Mapping[str, object],
        repair: dict[str, object],
        summary: dict[str, int | bool],
    ) -> None:
        summary["attempted"] = int(summary["attempted"]) + 1
        try:
            outcome = self.store.publish_pending_dispatches(self.queue, task_id=task_id, limit=1)
            published = outcome.get("published") if isinstance(outcome, Mapping) else None
            if not isinstance(published, list) or not published:
                repair["status"] = "failed"
                repair["error_kind"] = "PUBLISH_NOT_CONFIRMED"
                summary["failed"] = int(summary["failed"]) + 1
                return
            if isinstance(dispatch, dict):
                dispatch["status"] = "published"
                published_entry = published[0] if isinstance(published[0], Mapping) else None
                if published_entry is not None and bool(published_entry.get("settled")):
                    dispatch["receipt"] = {"state": "SETTLED_NO_PUBLISH"}
            repair["status"] = "repaired"
            summary["repaired"] = int(summary["repaired"]) + 1
        except Exception as error:
            repair["status"] = "failed"
            repair["error_kind"] = self._error_kind(error)
            summary["failed"] = int(summary["failed"]) + 1

    def _stage_and_publish(
        self,
        task_id: str,
        item: Mapping[str, object],
        repair: dict[str, object],
        summary: dict[str, int | bool],
    ) -> None:
        summary["attempted"] = int(summary["attempted"]) + 1
        try:
            staged = self.store.stage_dispatch_for_task(task_id)
            if not isinstance(staged, Mapping) or str(staged.get("status", "")) != "pending":
                repair["status"] = "failed"
                repair["error_kind"] = "OUTBOX_STAGE_NOT_CONFIRMED"
                summary["failed"] = int(summary["failed"]) + 1
                return
            outcome = self.store.publish_pending_dispatches(self.queue, task_id=task_id, limit=1)
            published = outcome.get("published") if isinstance(outcome, Mapping) else None
            if not isinstance(published, list) or not published:
                repair["status"] = "failed"
                repair["error_kind"] = "PUBLISH_NOT_CONFIRMED"
                summary["failed"] = int(summary["failed"]) + 1
                return
            repair["status"] = "repaired"
            summary["repaired"] = int(summary["repaired"]) + 1
        except Exception as error:
            repair["status"] = "failed"
            repair["error_kind"] = self._error_kind(error)
            summary["failed"] = int(summary["failed"]) + 1

    def _ensure_enqueued(
        self,
        candidate: Mapping[str, object],
        item: Mapping[str, object],
        dispatch: Mapping[str, object],
        repair: dict[str, object],
        summary: dict[str, int | bool],
    ) -> None:
        summary["attempted"] = int(summary["attempted"]) + 1
        ensure = getattr(self.queue, "ensure_enqueued", None)
        if not callable(ensure):
            repair["status"] = "failed"
            repair["error_kind"] = "QUEUE_REPAIR_UNAVAILABLE"
            summary["failed"] = int(summary["failed"]) + 1
            return
        try:
            envelope = ensure(
                str(candidate.get("task_id", "")),
                str(candidate.get("kind", "")),
                candidate.get("payload"),
                attempt=max(1, int(dispatch.get("attempt") or 1)),
                max_attempts=max(1, int(dispatch.get("max_attempts") or 3)),
                message_id=str(dispatch.get("message_id", "")),
            )
            if not isinstance(envelope, Mapping):
                raise RuntimeError("queue repair did not return an envelope")
            for field in ("message_id", "task_id", "kind"):
                if str(envelope.get(field, "")).strip() != str(dispatch.get(field, "")).strip():
                    raise RuntimeError(f"queue repair returned mismatched {field}")
            getter = getattr(self.queue, "get_delivery_receipt", None)
            refresh = getattr(self.store, "update_dispatch_receipt", None)
            if callable(getter) and callable(refresh):
                receipt = getter(
                    str(dispatch.get("message_id", "")),
                    task_id=str(candidate.get("task_id", "")),
                    kind=str(candidate.get("kind", "")),
                    attempt=max(1, int(dispatch.get("attempt") or 1)),
                    max_attempts=max(1, int(dispatch.get("max_attempts") or 3)),
                )
                if not isinstance(receipt, Mapping):
                    raise RuntimeError("queue repair did not return a delivery receipt")
                receipt_state = str(receipt.get("state", "")).upper()
                if receipt_state != "REDIS_ACCEPTED":
                    raise RuntimeError("queue repair did not verify the Redis delivery receipt")
                if not refresh(str(dispatch.get("message_id", "")), dict(receipt)):
                    raise RuntimeError("SQL delivery receipt refresh was not committed")
            repair["status"] = "repaired"
            summary["repaired"] = int(summary["repaired"]) + 1
        except Exception as error:
            repair["status"] = "failed"
            repair["error_kind"] = self._error_kind(error)
            summary["failed"] = int(summary["failed"]) + 1

    def _latest_dispatch(self, task_id: str) -> Mapping[str, object] | None:
        try:
            candidates = self.store.dispatch_consistency_candidates(limit=500)
        except Exception:
            return None
        for candidate in candidates:
            if str(candidate.get("task_id", "")) == task_id:
                dispatch = candidate.get("dispatch")
                return dispatch if isinstance(dispatch, Mapping) else None
        return None

    @staticmethod
    def _classify(
        task_status: str,
        outbox_status: str,
        delivery: Mapping[str, object],
    ) -> tuple[str, str]:
        if outbox_status == "pending":
            return "PENDING", "sql_outbox_pending"
        if outbox_status != "published":
            return "UNKNOWN", "sql_outbox_state_unknown"
        mismatched = int(delivery.get("mismatched_count", 0) or 0)
        if mismatched or bool(delivery.get("receipt_mismatch")):
            return "MISMATCHED", "redis_envelope_does_not_match_fixed_delivery"
        locations = delivery.get("matched_by_location")
        locations = locations if isinstance(locations, Mapping) else {}
        dead_count = int(locations.get("dead_letter", 0) or 0)
        exact_count = int(delivery.get("exact_count", 0) or 0)
        if dead_count:
            return "DLQ", "redis_dead_letter_contains_delivery"
        if exact_count > 1:
            return "DUPLICATE", "fixed_delivery_occurs_more_than_once"
        if int(locations.get("queued", 0) or 0):
            return "QUEUED", "redis_queue_contains_delivery"
        if int(locations.get("processing", 0) or 0):
            return "PROCESSING", "redis_processing_contains_delivery"
        if task_status in _TERMINAL_TASK_STATUSES:
            return "SETTLED", "terminal_task_has_no_live_redis_delivery"
        if task_status == "running":
            return "MISSING", "running_task_has_no_redis_delivery"
        return "MISSING", "published_delivery_is_absent_from_redis"

    @staticmethod
    def _delivery_is_unsafe(delivery: object) -> bool:
        if not isinstance(delivery, Mapping):
            return True
        if int(delivery.get("mismatched_count", 0) or 0) > 0:
            return True
        if bool(delivery.get("receipt_mismatch")):
            return True
        if int(delivery.get("exact_count", 0) or 0) > 1:
            return True
        locations = delivery.get("matched_by_location")
        return isinstance(locations, Mapping) and int(locations.get("dead_letter", 0) or 0) > 0

    @classmethod
    def _delivery_has_unique_safe_envelope(cls, delivery: object) -> bool:
        if not isinstance(delivery, Mapping):
            return False
        return (
            int(delivery.get("exact_count", 0) or 0) == 1
            and not cls._delivery_is_unsafe(delivery)
        )

    @staticmethod
    def _redis_receipt_state(delivery: object) -> str:
        if not isinstance(delivery, Mapping):
            return "UNAVAILABLE"
        if (
            int(delivery.get("mismatched_count", 0) or 0) > 0
            or bool(delivery.get("receipt_mismatch"))
        ):
            return "REDIS_MISMATCHED"
        if not bool(delivery.get("receipt")):
            return "REDIS_RECEIPT_MISSING"
        if int(delivery.get("exact_count", 0) or 0) == 1:
            return "REDIS_ACCEPTED"
        return "REDIS_RECEIPT_ONLY"

    @staticmethod
    def _safe_delivery(delivery: object) -> dict[str, object] | None:
        if not isinstance(delivery, Mapping):
            return None
        locations = delivery.get("matched_by_location")
        safe_locations = {
            key: int(locations.get(key, 0) or 0)
            for key in ("queued", "processing", "dead_letter")
        } if isinstance(locations, Mapping) else {
            "queued": 0,
            "processing": 0,
            "dead_letter": 0,
        }
        return {
            "available": bool(delivery.get("available")),
            "receipt": delivery.get("receipt"),
            "receipt_contract": delivery.get("receipt_contract", "UNAVAILABLE"),
            "receipt_mismatch": bool(delivery.get("receipt_mismatch")),
            "exact_count": int(delivery.get("exact_count", 0) or 0),
            "mismatched_count": int(delivery.get("mismatched_count", 0) or 0),
            "matched_by_location": safe_locations,
            "malformed_count": int(delivery.get("malformed_count", 0) or 0),
        }

    @staticmethod
    def _safe_receipt(dispatch: object) -> dict[str, object] | None:
        if not isinstance(dispatch, Mapping):
            return None
        receipt = dispatch.get("receipt")
        if not isinstance(receipt, Mapping):
            return {"state": "LEGACY", "verified": False}
        state = str(receipt.get("state") or "LEGACY").strip().upper()[:40] or "LEGACY"
        digest = str(receipt.get("sha256") or "").strip().lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            digest = None
        return {
            "state": state,
            "verified": state == "REDIS_ACCEPTED",
            "sha256": digest,
            "observed_at": receipt.get("observed_at"),
            "publish_attempts": int(receipt.get("publish_attempts", 0) or 0),
        }

    @staticmethod
    def _receipt_bucket(state: str) -> str:
        normalized = str(state or "LEGACY").strip().upper()
        if normalized == "REDIS_ACCEPTED":
            return "VERIFIED"
        if normalized == "PENDING":
            return "PENDING"
        if normalized == "SETTLED_NO_PUBLISH":
            return "SETTLED"
        if normalized in {"REDIS_MISMATCHED", "MISMATCHED"}:
            return "MISMATCHED"
        if normalized in {"REDIS_RECEIPT_ONLY", "REDIS_RECEIPT_MISSING", "UNVERIFIED"}:
            return "UNVERIFIED"
        return "LEGACY"

    @staticmethod
    def _error_kind(error: BaseException) -> str:
        return str(error.__class__.__name__).upper()[:80] or "UNKNOWN_ERROR"

    def _failed_report(self, *, repair: bool, error: BaseException) -> dict[str, object]:
        counts = {state: 0 for state in STATES}
        counts["UNKNOWN"] = 1
        return {
            "contract_version": CONTRACT_VERSION,
            "enabled": True,
            "ok": False,
            "status": "blocked",
            "checked": 0,
            "counts": counts,
            "receipt_counts": {
                "VERIFIED": 0,
                "LEGACY": 0,
                "PENDING": 0,
                "SETTLED": 0,
                "UNVERIFIED": 0,
                "MISMATCHED": 0,
            },
            "repair": {
                "requested": bool(repair),
                "attempted": 0,
                "repaired": 0,
                "skipped": 0,
                "failed": 1 if repair else 0,
            },
            "error_kind": self._error_kind(error),
            "items": [],
        }
