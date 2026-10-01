"""Redis task queue primitives shared by the API, Worker, and Scheduler."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4


QUEUE_NAME = "crypto:control_tasks:v1"
PROCESSING_NAME = "crypto:control_tasks:processing:v1"
DEAD_LETTER_NAME = "crypto:control_tasks:dead:v1"
HEARTBEAT_PREFIX = "crypto:worker:heartbeat:"
CLAIM_PREFIX = "crypto:control_tasks:claim:v1:"
PUBLISHED_PREFIX = "crypto:control_tasks:published:v1:"
PUBLISHED_TTL_SECONDS = 30 * 24 * 60 * 60
RECEIPT_CONTRACT_VERSION = "task-dispatch-receipt-v1"


_PUBLISH_IDEMPOTENT_LUA = """
local accepted = redis.call('SET', KEYS[2], ARGV[3], 'NX', 'EX', ARGV[2])
if not accepted then
  return 0
end
local published = redis.pcall('LPUSH', KEYS[1], ARGV[1])
if type(published) == 'table' and published['err'] then
  -- Do not leave a receipt for a message that never entered the queue.
  redis.call('DEL', KEYS[2])
  return redis.error_reply(published['err'])
end
return published
"""


def _delivery_digest(raw: str) -> str:
    """Hash fixed delivery content while excluding queue-time metadata."""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return sha256(str(raw).encode("utf-8")).hexdigest()
    if not isinstance(value, dict):
        return sha256(str(raw).encode("utf-8")).hexdigest()
    attempt = RedisTaskQueue._json_int(value.get("attempt"), 1)
    max_attempts = max(
        attempt,
        min(RedisTaskQueue._json_int(value.get("max_attempts"), 3), 10),
    )
    stable = {
        "message_id": str(value.get("message_id", "")),
        "task_id": str(value.get("task_id", "")),
        "kind": str(value.get("kind", "")),
        "payload": value.get("payload") if value.get("payload") is not None else {},
        "attempt": max(1, attempt),
        "max_attempts": max_attempts,
    }
    return sha256(_dump(stable).encode("utf-8")).hexdigest()


class TaskQueueError(RuntimeError):
    """Raised when the configured Redis queue cannot be used."""


class RedisTaskQueue:
    def __init__(
        self,
        url: str,
        *,
        queue_name: str = QUEUE_NAME,
        processing_name: str = PROCESSING_NAME,
        dead_letter_name: str = DEAD_LETTER_NAME,
    ) -> None:
        self.url = str(url or "").strip()
        if not self.url:
            raise TaskQueueError("redis task queue requires CRYPTO_REDIS_URL")
        try:
            from redis import Redis

            self._redis = Redis.from_url(
                self.url,
                decode_responses=True,
                socket_connect_timeout=2,
                # The worker uses a blocking list pop. Keep the socket timeout
                # longer than the maximum blocking interval so an idle queue
                # is reported as empty instead of unavailable.
                socket_timeout=65,
            )
        except Exception as error:  # pragma: no cover - driver is runtime-specific
            raise TaskQueueError("redis task queue driver is unavailable") from error
        self.queue_name = queue_name
        self.processing_name = processing_name
        self.dead_letter_name = dead_letter_name

    def enqueue(
        self,
        task_id: str,
        kind: str,
        payload: Any = None,
        *,
        attempt: int = 1,
        max_attempts: int = 3,
        message_id: str | None = None,
        _ensure_present: bool = False,
    ) -> dict[str, object]:
        bounded_attempt = max(1, int(attempt))
        bounded_max_attempts = max(bounded_attempt, min(int(max_attempts), 10))
        normalized_message_id = str(message_id or "").strip() or uuid4().hex
        envelope = {
            "message_id": normalized_message_id,
            "task_id": str(task_id),
            "kind": str(kind),
            "payload": payload if payload is not None else {},
            "attempt": bounded_attempt,
            "max_attempts": bounded_max_attempts,
            "enqueued_at": datetime.now(UTC).isoformat(),
        }
        raw = _dump(envelope)
        receipt_value = self._receipt_value(envelope, raw)
        try:
            if message_id:
                if _ensure_present:
                    self._ensure_present(raw, normalized_message_id, receipt_value)
                else:
                    self._publish_idempotently(raw, normalized_message_id, receipt_value)
            else:
                self._redis.lpush(self.queue_name, raw)
        except TaskQueueError:
            raise
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            raise TaskQueueError("unable to enqueue task") from error
        return envelope

    def ensure_enqueued(
        self,
        task_id: str,
        kind: str,
        payload: Any = None,
        *,
        attempt: int = 1,
        max_attempts: int = 3,
        message_id: str,
    ) -> dict[str, object]:
        """Repair a queued message while preserving its fixed delivery ID.

        A normal idempotent publish uses a receipt key to prevent duplicate
        pushes.  Recovery also needs to handle Redis data restored with the
        receipt but without the list item, so it checks both queue lists and
        only inserts the message when the fixed ID is absent.
        """
        key = str(message_id or "").strip()
        if not key:
            raise TaskQueueError("task delivery repair requires message_id")
        return self.enqueue(
            task_id,
            kind,
            payload,
            attempt=attempt,
            max_attempts=max_attempts,
            message_id=key,
            _ensure_present=True,
        )

    def claim(
        self,
        timeout_seconds: int = 5,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 900,
    ) -> dict[str, object] | None:
        try:
            raw = self._redis.brpoplpush(
                self.queue_name,
                self.processing_name,
                timeout=max(1, min(int(timeout_seconds), 60)),
            )
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            raise TaskQueueError("unable to claim task") from error
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self._remove(raw)
            raise TaskQueueError("redis queue contains invalid task JSON") from error
        if not isinstance(value, dict) or not str(value.get("task_id", "")).strip():
            self._remove(raw)
            raise TaskQueueError("redis queue contains invalid task envelope")
        value["attempt"] = max(1, self._json_int(value.get("attempt"), 1))
        value["max_attempts"] = max(
            int(value["attempt"]),
            min(self._json_int(value.get("max_attempts"), 3), 10),
        )
        claimed_at = datetime.now(UTC).isoformat()
        value["_claimed_at"] = claimed_at
        value["_worker_id"] = str(worker_id or "")
        claimed_value = self._public_envelope(value, raw)
        claimed_value["_claim"] = {
            "claimed_at": claimed_at,
            "worker_id": str(worker_id or ""),
        }
        claimed_raw = _dump(claimed_value)
        try:
            self._replace_processing(raw, claimed_raw)
            value["_raw"] = claimed_raw
            self._write_claim(value, claimed_raw, worker_id=worker_id, lease_seconds=lease_seconds)
        except TaskQueueError:
            # A task must not remain invisible in processing when claim
            # bookkeeping fails. The recovery marker is part of the list item,
            # so a later worker can still find it if this cleanup is interrupted.
            try:
                public_raw = _dump(self._public_envelope(value, claimed_raw))
                self._move_processing(claimed_raw, self.queue_name, public_raw, claim_key=self._claim_key(value))
            except TaskQueueError:
                pass
            raise
        return value

    def ack(self, envelope: dict[str, object]) -> None:
        raw = str(envelope.get("_raw", ""))
        if raw:
            self._remove_processing(raw, claim_key=self._claim_key(envelope))
        else:
            self._delete_claim(envelope)

    def requeue(
        self,
        envelope: dict[str, object],
        *,
        attempt: int | None = None,
        error: dict[str, object] | None = None,
    ) -> None:
        raw = str(envelope.get("_raw", ""))
        if not raw:
            return
        value = self._public_envelope(envelope, raw)
        if attempt is not None:
            value["attempt"] = max(1, int(attempt))
        if error is not None:
            value["last_error"] = error
        value["enqueued_at"] = datetime.now(UTC).isoformat()
        next_raw = _dump(value)
        self._move_processing(raw, self.queue_name, next_raw, claim_key=self._claim_key(envelope))

    def dead_letter(
        self,
        envelope: dict[str, object],
        *,
        error: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Move a permanently failed envelope to the durable Redis DLQ."""
        raw = str(envelope.get("_raw", ""))
        record = self.dead_letter_metadata(envelope, error=error)
        existing = self._find_dead_letter(str(record.get("message_id", "")))
        if existing is not None:
            # SQL is the terminal authority. If Redis already accepted the
            # DLQ record, a later settlement attempt only has to clear a
            # leftover processing claim.
            if raw:
                self._remove_processing(
                    raw,
                    claim_key=self._claim_key(envelope),
                    required=False,
                )
            else:
                self._delete_claim(envelope)
            return existing
        try:
            if raw:
                self._move_processing(
                    raw,
                    self.dead_letter_name,
                    _dump(record),
                    claim_key=self._claim_key(envelope),
                )
            else:
                self._redis.lpush(self.dead_letter_name, _dump(record))
                self._delete_claim(envelope)
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            raise TaskQueueError("unable to persist task dead letter") from error
        return record

    def dead_letter_metadata(
        self,
        envelope: dict[str, object],
        *,
        error: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Build the bounded DLQ reference without mutating Redis."""
        raw = str(envelope.get("_raw", ""))
        value = self._public_envelope(envelope, raw)
        message_id = str(value.get("message_id", "")).strip()
        stable_key = message_id or str(value.get("task_id", "")).strip() or "unknown"
        return {
            "dead_letter_id": f"dlq-{sha256(stable_key.encode('utf-8')).hexdigest()[:32]}",
            "task_id": str(value.get("task_id", "")),
            "kind": str(value.get("kind", "")),
            "message_id": message_id,
            "attempt": max(1, self._json_int(value.get("attempt"), 1)),
            "max_attempts": max(1, self._json_int(value.get("max_attempts"), 3)),
            "error": error or value.get("last_error") or {},
            "dead_lettered_at": datetime.now(UTC).isoformat(),
        }

    def dead_letters(self, limit: int = 100) -> list[dict[str, object]]:
        """Read bounded DLQ metadata without returning the task payload."""
        bounded = max(1, min(int(limit), 500))
        if not hasattr(self._redis, "lrange"):
            return []
        try:
            values = self._redis.lrange(self.dead_letter_name, 0, bounded - 1)
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            raise TaskQueueError("unable to read task dead letters") from error
        items: list[dict[str, object]] = []
        for raw in values or []:
            try:
                value = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                items.append(value)
        return items

    def inspect_delivery(
        self,
        task_id: str,
        message_id: str,
        *,
        kind: str | None = None,
        attempt: int | None = None,
        max_attempts: int | None = None,
        limit: int = 500,
    ) -> dict[str, object]:
        """Inspect one fixed delivery ID across Redis lists and its receipt.

        Only bounded envelope metadata is returned. Payloads are deliberately
        excluded because this projection is consumed by operational status and
        recovery code as well as tests.
        """
        task_key = str(task_id or "").strip()
        message_key = str(message_id or "").strip()
        if not task_key or not message_key:
            raise TaskQueueError("task delivery inspection requires task_id and message_id")
        if not hasattr(self._redis, "lrange") or not hasattr(self._redis, "exists"):
            raise TaskQueueError("redis task delivery inspection is unavailable")
        bounded = max(1, min(int(limit), 500))
        expected_kind = str(kind or "").strip()
        expected_attempt = None if attempt is None else max(1, int(attempt))
        expected_max_attempts = None if max_attempts is None else max(
            expected_attempt or 1,
            min(int(max_attempts), 10),
        )
        occurrences: list[dict[str, object]] = []
        malformed = 0
        try:
            for location, key in (
                ("queued", self.queue_name),
                ("processing", self.processing_name),
                ("dead_letter", self.dead_letter_name),
            ):
                values = self._redis.lrange(key, 0, bounded - 1) or []
                for index, raw in enumerate(values):
                    value = self._decode_envelope(raw)
                    if value is None:
                        malformed += 1
                        continue
                    actual_task_id = str(value.get("task_id", "")).strip()
                    actual_message_id = str(value.get("message_id", "")).strip()
                    if actual_task_id != task_key and actual_message_id != message_key:
                        continue
                    actual_kind = str(value.get("kind", "")).strip()
                    actual_attempt = max(1, self._json_int(value.get("attempt"), 1))
                    actual_max_attempts = max(
                        actual_attempt,
                        min(self._json_int(value.get("max_attempts"), 3), 10),
                    )
                    occurrences.append(
                        {
                            "location": location,
                            "index": index,
                            "task_id": actual_task_id,
                            "message_id": actual_message_id,
                            "kind": actual_kind,
                            "attempt": actual_attempt,
                            "max_attempts": actual_max_attempts,
                            "sha256": _delivery_digest(str(raw)),
                            "matched": bool(
                                actual_task_id == task_key
                                and actual_message_id == message_key
                                and (not expected_kind or actual_kind == expected_kind)
                                and (
                                    expected_attempt is None
                                    or actual_attempt == expected_attempt
                                )
                                and (
                                    expected_max_attempts is None
                                    or actual_max_attempts == expected_max_attempts
                                )
                            ),
                        }
                    )
            marker_key = f"{PUBLISHED_PREFIX}{message_key}"
            marker_value = None
            if hasattr(self._redis, "get"):
                marker_value = self._redis.get(marker_key)
                receipt = bool(marker_value) or bool(self._redis.exists(marker_key))
            else:
                receipt = bool(self._redis.exists(marker_key))
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            raise TaskQueueError("unable to inspect task delivery state") from error
        matched = [item for item in occurrences if item["matched"]]
        mismatched = [item for item in occurrences if not item["matched"]]
        receipt_contract = "UNAVAILABLE"
        receipt_sha256 = None
        receipt_mismatch = False
        if receipt and marker_value is not None:
            marker_text = str(marker_value)
            if marker_text == "1":
                receipt_contract = "LEGACY"
            else:
                try:
                    marker = json.loads(marker_text)
                except (TypeError, ValueError, json.JSONDecodeError):
                    marker = None
                if isinstance(marker, dict):
                    receipt_contract = str(marker.get("contract_version", "")).strip() or "UNKNOWN"
                    receipt_sha256 = str(marker.get("sha256", "")).strip().lower() or None
                    expected_identity = {
                        "message_id": message_key,
                        "task_id": task_key,
                        "kind": expected_kind,
                        "attempt": expected_attempt,
                        "max_attempts": expected_max_attempts,
                    }
                    for field, expected in expected_identity.items():
                        if expected is None:
                            continue
                        actual = marker.get(field)
                        if field in {"attempt", "max_attempts"}:
                            try:
                                actual = int(actual)
                            except (TypeError, ValueError):
                                actual = None
                        if actual != expected:
                            receipt_mismatch = True
                            break
                    if (
                        not receipt_mismatch
                        and len(matched) == 1
                        and receipt_sha256 != matched[0].get("sha256")
                    ):
                        receipt_mismatch = True
                    if receipt_contract != RECEIPT_CONTRACT_VERSION:
                        receipt_mismatch = True
                else:
                    receipt_contract = "INVALID"
                    receipt_mismatch = True
        matched_by_location = {
            location: sum(
                1
                for item in matched
                if str(item.get("location", "")) == location
            )
            for location in ("queued", "processing", "dead_letter")
        }
        return {
            "available": True,
            "receipt": receipt,
            "receipt_contract": receipt_contract,
            "receipt_sha256": receipt_sha256,
            "receipt_mismatch": receipt_mismatch,
            "exact_count": len(matched),
            "mismatched_count": len(mismatched),
            "matched_by_location": matched_by_location,
            "occurrences": occurrences,
            "malformed_count": malformed,
            "scan_limit": bounded,
        }

    def get_delivery_receipt(
        self,
        message_id: str,
        *,
        task_id: str,
        kind: str,
        attempt: int,
        max_attempts: int,
    ) -> dict[str, object]:
        """Return a bounded Redis acceptance receipt without task payload."""
        message_key = str(message_id or "").strip()
        if not message_key:
            raise TaskQueueError("task delivery receipt requires message_id")
        inspection = self.inspect_delivery(
            task_id,
            message_key,
            kind=kind,
            attempt=attempt,
            max_attempts=max_attempts,
            limit=500,
        )
        marker_key = f"{PUBLISHED_PREFIX}{message_key}"
        marker_present = bool(inspection.get("receipt"))
        locations = inspection.get("matched_by_location")
        locations = locations if isinstance(locations, dict) else {}
        exact_count = int(inspection.get("exact_count", 0) or 0)
        mismatched_count = int(inspection.get("mismatched_count", 0) or 0)
        occurrences = inspection.get("occurrences")
        matched_occurrences = (
            [item for item in occurrences if isinstance(item, dict) and item.get("matched")]
            if isinstance(occurrences, list)
            else []
        )
        if mismatched_count or bool(inspection.get("receipt_mismatch")):
            state = "REDIS_MISMATCHED"
        elif not marker_present:
            state = "REDIS_RECEIPT_MISSING"
        elif exact_count == 1:
            state = "REDIS_ACCEPTED"
        else:
            # Redis accepted the fixed ID, but the list item is not currently
            # visible. SQL keeps this receipt so reconciliation can repair it.
            state = "REDIS_RECEIPT_ONLY"
        return {
            "state": state,
            "verified": state == "REDIS_ACCEPTED",
            "receipt_key": marker_key,
            "receipt_contract": inspection.get("receipt_contract", "UNAVAILABLE"),
            "receipt_sha256": inspection.get("receipt_sha256"),
            "queue_name": self.queue_name,
            "observed_at": datetime.now(UTC).isoformat(),
            "sha256": (
                str(matched_occurrences[0].get("sha256"))
                if exact_count == 1 and matched_occurrences
                else None
            ),
            "exact_count": exact_count,
            "mismatched_count": mismatched_count,
            "matched_by_location": {
                location: int(locations.get(location, 0) or 0)
                for location in ("queued", "processing", "dead_letter")
            },
        }

    def replay_dead_letter(
        self,
        task_id: str,
        kind: str,
        payload: Any,
        *,
        replay_task_id: str,
        request_id: str,
        max_attempts: int = 3,
        message_id: str | None = None,
    ) -> dict[str, object]:
        """Enqueue a bounded replay while retaining the original DLQ audit row.

        The Redis dead-letter record intentionally does not contain the task
        payload. The caller supplies the payload recovered from the durable SQL
        ledger, and this method only records the replay reference beside the
        original metadata.
        """
        source_task_id = str(task_id).strip()
        replay_id = str(replay_task_id).strip()
        request_key = str(request_id).strip()
        if not source_task_id or not replay_id or not request_key:
            raise TaskQueueError("dead-letter replay identifiers must not be empty")
        try:
            raw_values = self._redis.lrange(self.dead_letter_name, 0, 499)
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            raise TaskQueueError("unable to inspect task dead letters for replay") from error

        matching_index: int | None = None
        matching_record: dict[str, object] | None = None
        for index, raw in enumerate(raw_values or []):
            try:
                value = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(value, dict) or str(value.get("task_id", "")) != source_task_id:
                continue
            if str(value.get("replay_request_id", "")) == request_key:
                envelope: dict[str, object] = {}
                if message_id:
                    # The audit row proves that this request was already
                    # accepted, but Redis may have lost only the list item.
                    # Reconcile the fixed delivery ID without creating a new
                    # replay request or a duplicate message.
                    envelope = self.ensure_enqueued(
                        replay_id,
                        kind,
                        payload,
                        attempt=1,
                        max_attempts=max_attempts,
                        message_id=message_id,
                    )
                return {
                    "envelope": envelope,
                    "dead_letter": value,
                    "deduplicated": True,
                }
            if matching_record is None:
                matching_index = index
                matching_record = value
        if matching_record is None or matching_index is None:
            raise TaskQueueError("task dead-letter record was not found")

        if message_id:
            envelope = self.ensure_enqueued(
                replay_id,
                kind,
                payload,
                attempt=1,
                max_attempts=max_attempts,
                message_id=message_id,
            )
        else:
            envelope = self.enqueue(
                replay_id,
                kind,
                payload,
                attempt=1,
                max_attempts=max_attempts,
                message_id=None,
            )
        updated_record = {
            **matching_record,
            "replay_request_id": request_key,
            "replay_task_id": replay_id,
            "replayed_at": datetime.now(UTC).isoformat(),
        }
        # Redis 5 supports LSET. Keep the enqueue usable with small adapters
        # that expose list operations but cannot rewrite an existing item.
        if hasattr(self._redis, "lset"):
            try:
                self._redis.lset(self.dead_letter_name, matching_index, _dump(updated_record))
            except Exception:
                pass
        return {
            "envelope": envelope,
            "dead_letter": updated_record,
            "deduplicated": False,
        }

    def stale_claims(
        self,
        *,
        grace_seconds: int = 60,
        exclude_worker_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """Find claims whose worker heartbeat has expired without moving them.

        SQL recovery must happen before the Redis claim is removed. Returning
        the raw processing value to the Worker keeps that ordering explicit;
        this method is internal control-plane metadata and is never exposed by
        an API response.
        """
        if not all(
            hasattr(self._redis, name)
            for name in ("scan_iter", "hgetall", "exists", "lrange")
        ):
            return []
        bounded = max(1, min(int(limit), 500))
        cutoff = datetime.now(UTC).timestamp() - max(30, min(int(grace_seconds), 3600))
        excluded = str(exclude_worker_id or "").strip()
        stale: list[dict[str, object]] = []
        seen_raw: set[str] = set()
        try:
            claim_keys = tuple(self._redis.scan_iter(match=f"{CLAIM_PREFIX}*"))
            for claim_key in claim_keys:
                claim = self._redis.hgetall(claim_key)
                raw = str(claim.get("raw", ""))
                item = self._stale_claim(
                    claim,
                    raw,
                    cutoff=cutoff,
                    excluded_worker_id=excluded,
                    claim_key=str(claim_key),
                )
                if item is None:
                    continue
                seen_raw.add(raw)
                stale.append(item)
                if len(stale) >= bounded:
                    return stale

            for raw in self._redis.lrange(self.processing_name, 0, -1) or []:
                raw_text = str(raw)
                if not raw_text or raw_text in seen_raw:
                    continue
                value = self._decode_envelope(raw_text)
                claim = value.get("_claim") if isinstance(value, dict) else None
                if not isinstance(claim, dict):
                    continue
                item = self._stale_claim(
                    {
                        **claim,
                        "task_id": value.get("task_id", ""),
                        "message_id": value.get("message_id", ""),
                        "attempt": value.get("attempt", 1),
                    },
                    raw_text,
                    cutoff=cutoff,
                    excluded_worker_id=excluded,
                    claim_key="",
                )
                if item is None:
                    continue
                stale.append(item)
                if len(stale) >= bounded:
                    break
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            raise TaskQueueError("unable to inspect stale worker claims") from error
        return stale

    def release_claim(self, claim: dict[str, object]) -> None:
        """Remove a claim after its SQL task has been durably requeued."""
        raw = str(claim.get("_raw", ""))
        if not raw:
            raise TaskQueueError("stale task claim is missing its processing value")
        claim_key = str(claim.get("_claim_key", "")) or self._claim_key(claim)
        self._remove_processing(raw, claim_key=claim_key)

    def renew(self, envelope: dict[str, object], *, lease_seconds: int = 900) -> None:
        """Extend a claim while a bounded research operation is running."""
        message_id = str(envelope.get("message_id", "")).strip()
        if not message_id or not hasattr(self._redis, "hset"):
            return
        claimed_at = datetime.now(UTC).isoformat()
        try:
            raw = str(envelope.get("_raw", ""))
            if raw:
                value = self._public_envelope(envelope, raw)
                value["_claim"] = {
                    "claimed_at": claimed_at,
                    "worker_id": str(envelope.get("_worker_id", "")),
                }
                refreshed_raw = _dump(value)
                self._replace_processing(raw, refreshed_raw)
                envelope["_raw"] = refreshed_raw
            self._redis.hset(
                f"{CLAIM_PREFIX}{message_id}",
                mapping={
                    "raw": str(envelope.get("_raw", "")),
                    "task_id": str(envelope.get("task_id", "")),
                    "worker_id": str(envelope.get("_worker_id", "")),
                    "claimed_at": claimed_at,
                    "attempt": str(envelope.get("attempt", 1)),
                },
            )
            self._expire_claim(f"{CLAIM_PREFIX}{message_id}", lease_seconds)
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            raise TaskQueueError("unable to renew task lease") from error

    def requeue_expired(self, *, lease_seconds: int = 900) -> int:
        """Return expired processing-list messages to the queue after a crash."""
        return len(self.reclaim_expired(lease_seconds=lease_seconds))

    def reclaim_expired(self, *, lease_seconds: int = 900) -> list[dict[str, object]]:
        """Requeue expired claims and return task metadata for SQL recovery."""
        if not hasattr(self._redis, "scan_iter") or not hasattr(self._redis, "hgetall"):
            return []
        cutoff = datetime.now(UTC).timestamp() - max(30, int(lease_seconds))
        reclaimed: list[dict[str, object]] = []
        seen_raw: set[str] = set()
        try:
            claim_keys = tuple(self._redis.scan_iter(match=f"{CLAIM_PREFIX}*"))
            for key in claim_keys:
                claim = self._redis.hgetall(key)
                raw = str(claim.get("raw", ""))
                claimed_at = str(claim.get("claimed_at", ""))
                if not raw or not claimed_at:
                    continue
                try:
                    age = datetime.fromisoformat(claimed_at).timestamp()
                except ValueError:
                    age = 0
                if age > cutoff:
                    continue
                seen_raw.add(raw)
                value = self._decode_envelope(raw)
                if value is None:
                    continue
                moved = self._move_processing(
                    raw,
                    self.queue_name,
                    _dump(self._public_envelope(value, raw)),
                    claim_key=str(key),
                    required=False,
                )
                removed = int(moved)
                if removed:
                    reclaimed.append(
                        {
                            "task_id": str(claim.get("task_id", "")),
                            "message_id": str(key).removeprefix(CLAIM_PREFIX),
                            "attempt": int(self._json_int(claim.get("attempt"), 1)),
                        }
                    )
            # A claim hash can be lost by an older worker or by an operator
            # restoring only the Redis list. New claims also carry a recovery
            # marker in the processing item, so the item remains discoverable.
            if hasattr(self._redis, "lrange"):
                for raw in self._redis.lrange(self.processing_name, 0, -1) or []:
                    raw_text = str(raw)
                    if raw_text in seen_raw:
                        continue
                    value = self._decode_envelope(raw_text)
                    claim = value.get("_claim") if isinstance(value, dict) else None
                    if isinstance(claim, dict):
                        claimed_at = str(claim.get("claimed_at", ""))
                    else:
                        # Envelopes created before recovery markers were added
                        # can only be recovered from their enqueue timestamp.
                        claimed_at = str(value.get("enqueued_at", "")) if isinstance(value, dict) else ""
                    if not claimed_at:
                        continue
                    try:
                        age = datetime.fromisoformat(claimed_at).timestamp()
                    except ValueError:
                        age = 0
                    if not claimed_at or age > cutoff:
                        continue
                    moved = self._move_processing(
                        raw_text,
                        self.queue_name,
                        _dump(self._public_envelope(value, raw_text)),
                        required=False,
                    )
                    if moved:
                        reclaimed.append(
                            {
                                "task_id": str(value.get("task_id", "")),
                                "message_id": str(value.get("message_id", "")),
                                "attempt": int(self._json_int(value.get("attempt"), 1)),
                            }
                        )
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            raise TaskQueueError("unable to reclaim expired task leases") from error
        return reclaimed

    def heartbeat(
        self,
        worker_id: str,
        *,
        ttl_seconds: int = 30,
        state: str = "running",
        active_task_id: str | None = None,
    ) -> dict[str, object]:
        lifecycle_state = str(state or "running").strip().lower()
        if lifecycle_state not in {"running", "draining", "stopped"}:
            raise TaskQueueError("worker heartbeat state is invalid")
        heartbeat = {
            "worker_id": str(worker_id),
            "state": lifecycle_state,
            "active_task_id": str(active_task_id or "") or None,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        try:
            self._redis.set(
                f"{HEARTBEAT_PREFIX}{worker_id}",
                _dump(heartbeat),
                ex=max(5, min(int(ttl_seconds), 300)),
            )
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            raise TaskQueueError("unable to write worker heartbeat") from error
        return heartbeat

    def worker_status(self, worker_id: str) -> dict[str, object] | None:
        """Read one bounded worker lifecycle heartbeat for operations checks."""
        key = f"{HEARTBEAT_PREFIX}{str(worker_id).strip()}"
        if not str(worker_id).strip() or not hasattr(self._redis, "get"):
            return None
        try:
            raw = self._redis.get(key)
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            raise TaskQueueError("unable to read worker heartbeat") from error
        if not raw:
            return None
        try:
            value = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise TaskQueueError("worker heartbeat contains invalid JSON") from error
        return value if isinstance(value, dict) else None

    def size(self) -> dict[str, int]:
        try:
            return {
                "queued": int(self._redis.llen(self.queue_name)),
                "processing": int(self._redis.llen(self.processing_name)),
            }
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            raise TaskQueueError("unable to read queue size") from error

    def status(self) -> dict[str, object]:
        try:
            self._redis.ping()
            sizes = self.size()
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            return {"enabled": True, "ok": False, "status": "unreachable", "message": str(error)}
        dead_letter_count = 0
        try:
            dead_letter_count = int(self._redis.llen(self.dead_letter_name))
        except Exception:
            pass
        return {"enabled": True, "ok": True, "status": "ready", "dead_lettered": dead_letter_count, **sizes}

    @staticmethod
    def _json_int(value: object, default: int) -> int:
        try:
            return int(value or default)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _public_envelope(cls, envelope: dict[str, object], raw: str) -> dict[str, object]:
        value: dict[str, object]
        try:
            loaded = json.loads(raw) if raw else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            loaded = {}
        value = dict(loaded) if isinstance(loaded, dict) else {}
        if not value:
            value = {str(key): item for key, item in envelope.items() if not str(key).startswith("_")}
        for key in tuple(value):
            if str(key).startswith("_"):
                value.pop(key, None)
        return value

    @staticmethod
    def _receipt_value(envelope: dict[str, object], raw: str) -> str:
        """Build the payload-free Redis receipt written beside one envelope."""
        return _dump(
            {
                "contract_version": RECEIPT_CONTRACT_VERSION,
                "message_id": str(envelope.get("message_id", "")),
                "task_id": str(envelope.get("task_id", "")),
                "kind": str(envelope.get("kind", "")),
                "attempt": max(1, RedisTaskQueue._json_int(envelope.get("attempt"), 1)),
                "max_attempts": max(
                    max(1, RedisTaskQueue._json_int(envelope.get("attempt"), 1)),
                    min(RedisTaskQueue._json_int(envelope.get("max_attempts"), 3), 10),
                ),
                "sha256": _delivery_digest(raw),
            }
        )

    def _publish_idempotently(self, raw: str, message_id: str, receipt_value: str) -> None:
        """Publish one outbox message once, even if the SQL acknowledgement is retried."""
        marker_key = f"{PUBLISHED_PREFIX}{message_id}"
        if hasattr(self._redis, "eval"):
            self._redis.eval(
                _PUBLISH_IDEMPOTENT_LUA,
                2,
                self.queue_name,
                marker_key,
                raw,
                str(PUBLISHED_TTL_SECONDS),
                receipt_value,
            )
            return
        if hasattr(self._redis, "setnx"):
            accepted = bool(self._redis.setnx(marker_key, receipt_value))
            if not accepted:
                return
            try:
                if hasattr(self._redis, "expire"):
                    self._redis.expire(marker_key, PUBLISHED_TTL_SECONDS)
                self._redis.lpush(self.queue_name, raw)
            except Exception:
                if hasattr(self._redis, "delete"):
                    self._redis.delete(marker_key)
                raise
            return
        # Small test doubles and legacy adapters may not expose an atomic API.
        # They still receive the fixed message ID, while real Redis always uses
        # the Lua branch above.
        self._redis.lpush(self.queue_name, raw)

    def _ensure_present(self, raw: str, message_id: str, receipt_value: str) -> None:
        """Ensure a fixed-ID message exists in Redis after partial recovery."""
        marker_key = f"{PUBLISHED_PREFIX}{message_id}"
        value = self._decode_envelope(raw)
        if not isinstance(value, dict):
            raise TaskQueueError("task delivery repair received an invalid envelope")
        task_id = str(value.get("task_id", "")).strip()
        kind = str(value.get("kind", "")).strip()
        attempt = max(1, self._json_int(value.get("attempt"), 1))
        max_attempts = max(
            attempt,
            min(self._json_int(value.get("max_attempts"), 3), 10),
        )
        if not task_id or not kind:
            raise TaskQueueError("task delivery repair received an incomplete envelope")
        message_needle = f'"message_id":"{message_id}"'
        task_needle = f'"task_id":"{task_id}"'
        kind_needle = f'"kind":"{kind}"'
        attempt_needle = f'"attempt":{attempt}'
        max_attempts_needle = f'"max_attempts":{max_attempts}'
        if hasattr(self._redis, "eval"):
            script = """
            local function matching_state(key, message_needle, task_needle, kind_needle, attempt_needle, max_attempts_needle)
              local values = redis.call('LRANGE', key, 0, -1)
              for _, value in ipairs(values) do
                if string.find(value, message_needle, 1, true) then
                  if string.find(value, task_needle, 1, true)
                    and string.find(value, kind_needle, 1, true)
                    and string.find(value, attempt_needle, 1, true)
                    and string.find(value, max_attempts_needle, 1, true) then
                    return 1
                  end
                  return -1
                end
              end
              return 0
            end
            local queued = matching_state(KEYS[1], ARGV[2], ARGV[3], ARGV[4], ARGV[5], ARGV[6])
            local processing = matching_state(KEYS[2], ARGV[2], ARGV[3], ARGV[4], ARGV[5], ARGV[6])
            if queued == -1 or processing == -1 then
              return -1
            end
            local marker = redis.call('GET', KEYS[3])
            if marker and marker ~= '1' and marker ~= ARGV[7] then
              return -2
            end
            if queued == 1 or processing == 1 then
              redis.call('SET', KEYS[3], ARGV[7], 'EX', ARGV[8])
              return 0
            end
            redis.call('LPUSH', KEYS[1], ARGV[1])
            redis.call('SET', KEYS[3], ARGV[7], 'EX', ARGV[8])
            return 1
            """
            try:
                result = int(self._redis.eval(
                    script,
                    3,
                    self.queue_name,
                    self.processing_name,
                    marker_key,
                    raw,
                    message_needle,
                    task_needle,
                    kind_needle,
                    attempt_needle,
                    max_attempts_needle,
                    receipt_value,
                    str(PUBLISHED_TTL_SECONDS),
                ) or 0)
                if result < 0:
                    raise TaskQueueError("task delivery ID conflicts with an existing envelope")
                return
            except Exception as error:  # pragma: no cover - depends on Redis runtime
                if isinstance(error, TaskQueueError):
                    raise
                raise TaskQueueError("unable to repair task delivery") from error

        try:
            values: list[object] = []
            if hasattr(self._redis, "lrange"):
                values.extend(self._redis.lrange(self.queue_name, 0, -1) or [])
                values.extend(self._redis.lrange(self.processing_name, 0, -1) or [])
            for candidate in values:
                candidate_value = self._decode_envelope(candidate)
                if not isinstance(candidate_value, dict):
                    continue
                if str(candidate_value.get("message_id", "")).strip() != message_id:
                    continue
                candidate_attempt = max(1, self._json_int(candidate_value.get("attempt"), 1))
                candidate_max_attempts = max(
                    candidate_attempt,
                    min(self._json_int(candidate_value.get("max_attempts"), 3), 10),
                )
                if (
                    str(candidate_value.get("task_id", "")).strip() != task_id
                    or str(candidate_value.get("kind", "")).strip() != kind
                    or candidate_attempt != attempt
                    or candidate_max_attempts != max_attempts
                ):
                    raise TaskQueueError(
                        "task delivery ID conflicts with an existing envelope"
                    )
                self._set_delivery_marker(marker_key, receipt_value)
                return
            self._redis.lpush(self.queue_name, raw)
            self._set_delivery_marker(marker_key, receipt_value)
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            if isinstance(error, TaskQueueError):
                raise
            raise TaskQueueError("unable to repair task delivery") from error

    def _set_delivery_marker(self, marker_key: str, value: str = "1") -> None:
        if not hasattr(self._redis, "setnx"):
            return
        self._redis.setnx(marker_key, value)
        if hasattr(self._redis, "expire"):
            self._redis.expire(marker_key, PUBLISHED_TTL_SECONDS)

    def _remove(self, raw: str) -> None:
        try:
            self._redis.lrem(self.processing_name, 1, raw)
        except Exception:
            return

    def _find_dead_letter(self, message_id: str) -> dict[str, object] | None:
        key = str(message_id or "").strip()
        if not key or not hasattr(self._redis, "lrange"):
            return None
        try:
            values = self._redis.lrange(self.dead_letter_name, 0, 499) or []
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            raise TaskQueueError("unable to inspect task dead letters") from error
        for raw in values:
            value = self._decode_envelope(raw)
            if isinstance(value, dict) and str(value.get("message_id", "")).strip() == key:
                return value
        return None

    def _replace_processing(self, raw: str, replacement: str) -> None:
        """Attach recovery metadata without exposing a partially claimed item."""
        if not raw:
            raise TaskQueueError("task claim is missing its processing item")
        if hasattr(self._redis, "eval"):
            script = """
            local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
            if removed == 1 then
              redis.call('LPUSH', KEYS[1], ARGV[2])
            end
            return removed
            """
            try:
                removed = int(
                    self._redis.eval(
                        script,
                        1,
                        self.processing_name,
                        raw,
                        replacement,
                    )
                    or 0
                )
            except Exception as error:  # pragma: no cover - depends on Redis runtime
                raise TaskQueueError("unable to persist task claim marker") from error
        else:
            try:
                removed = int(self._redis.lrem(self.processing_name, 1, raw) or 0)
                if removed:
                    self._redis.lpush(self.processing_name, replacement)
            except Exception as error:  # pragma: no cover - depends on Redis runtime
                raise TaskQueueError("unable to persist task claim marker") from error
        if removed != 1:
            raise TaskQueueError("task claim disappeared from processing queue")

    def _move_processing(
        self,
        raw: str,
        destination: str,
        replacement: str,
        *,
        claim_key: str = "",
        required: bool = True,
    ) -> int:
        """Atomically remove a processing item before publishing its next state."""
        if hasattr(self._redis, "eval"):
            script = """
            local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
            if removed == 1 then
              if ARGV[3] ~= '' then
                redis.call('LPUSH', KEYS[2], ARGV[2])
              end
              if KEYS[3] ~= '' then
                redis.call('DEL', KEYS[3])
              end
            end
            return removed
            """
            try:
                removed = int(
                    self._redis.eval(
                        script,
                        3,
                        self.processing_name,
                        destination,
                        claim_key,
                        raw,
                        replacement,
                        "1",
                    )
                    or 0
                )
            except Exception as error:  # pragma: no cover - depends on Redis runtime
                raise TaskQueueError("unable to move task processing item") from error
        else:
            try:
                removed = int(self._redis.lrem(self.processing_name, 1, raw) or 0)
                if removed:
                    self._redis.lpush(destination, replacement)
                    if claim_key:
                        self._redis.delete(claim_key)
            except Exception as error:  # pragma: no cover - depends on Redis runtime
                raise TaskQueueError("unable to move task processing item") from error
        if required and removed != 1:
            raise TaskQueueError("task processing item was already acknowledged")
        return removed

    def _remove_processing(
        self,
        raw: str,
        *,
        claim_key: str = "",
        required: bool = True,
    ) -> int:
        """Acknowledge a claim and remove its recovery marker atomically."""
        if hasattr(self._redis, "eval"):
            script = """
            local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
            if KEYS[2] ~= '' and (removed == 1 or ARGV[2] == '0') then
              redis.call('DEL', KEYS[2])
            end
            return removed
            """
            try:
                removed = int(
                    self._redis.eval(
                        script,
                        2,
                        self.processing_name,
                        claim_key,
                        raw,
                        "1" if required else "0",
                    )
                    or 0
                )
            except Exception as error:
                # Redis EVAL is atomic, but a connection error leaves the
                # result unknown. Preserve both processing and claim evidence
                # so the normal recovery scan can decide what happened.
                raise TaskQueueError("unable to acknowledge task processing item") from error
            if removed != 1:
                if required:
                    raise TaskQueueError("task processing item was not present for acknowledgement")
            return removed
        try:
            removed = int(self._redis.lrem(self.processing_name, 1, raw) or 0)
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            raise TaskQueueError("unable to acknowledge task processing item") from error
        if removed != 1:
            if required:
                raise TaskQueueError("task processing item was not present for acknowledgement")
            if claim_key:
                try:
                    self._redis.delete(claim_key)
                except Exception as error:  # pragma: no cover - depends on Redis runtime
                    raise TaskQueueError("unable to remove task claim marker") from error
            return removed
        if claim_key:
            try:
                self._redis.delete(claim_key)
            except Exception as error:  # pragma: no cover - depends on Redis runtime
                raise TaskQueueError("unable to remove task claim marker") from error
        return removed

    @staticmethod
    def _decode_envelope(raw: object) -> dict[str, object] | None:
        try:
            value = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _stale_claim(
        self,
        claim: dict[str, object],
        raw: str,
        *,
        cutoff: float,
        excluded_worker_id: str,
        claim_key: str,
    ) -> dict[str, object] | None:
        if not raw:
            return None
        worker_id = str(claim.get("worker_id", "")).strip()
        if not worker_id or worker_id == excluded_worker_id:
            return None
        if bool(self._redis.exists(f"{HEARTBEAT_PREFIX}{worker_id}")):
            return None
        claimed_at = str(claim.get("claimed_at", ""))
        try:
            claimed_timestamp = datetime.fromisoformat(claimed_at).timestamp()
        except ValueError:
            return None
        if claimed_timestamp > cutoff:
            return None
        value = self._decode_envelope(raw)
        if not isinstance(value, dict) or not str(value.get("task_id", "")).strip():
            return None
        message_id = str(value.get("message_id", claim.get("message_id", ""))).strip()
        return {
            "task_id": str(value.get("task_id", "")),
            "message_id": message_id,
            "attempt": self._json_int(value.get("attempt", claim.get("attempt")), 1),
            "worker_id": worker_id,
            "_raw": raw,
            "_claim_key": claim_key,
        }

    @staticmethod
    def _claim_key(envelope: dict[str, object]) -> str:
        message_id = str(envelope.get("message_id", "")).strip()
        return f"{CLAIM_PREFIX}{message_id}" if message_id else ""

    def _write_claim(
        self,
        envelope: dict[str, object],
        raw: str,
        *,
        worker_id: str | None,
        lease_seconds: int,
    ) -> None:
        if not hasattr(self._redis, "hset"):
            return
        message_id = str(envelope.get("message_id", "")).strip()
        if not message_id:
            return
        key = f"{CLAIM_PREFIX}{message_id}"
        try:
            self._redis.hset(
                key,
                mapping={
                    "raw": raw,
                    "task_id": str(envelope.get("task_id", "")),
                    "worker_id": str(worker_id or ""),
                    "claimed_at": str(envelope.get("_claimed_at", "")),
                    "attempt": str(envelope.get("attempt", 1)),
                },
            )
            self._expire_claim(key, lease_seconds)
        except Exception as error:  # pragma: no cover - depends on Redis runtime
            raise TaskQueueError("unable to record task lease") from error

    def _delete_claim(self, envelope: dict[str, object]) -> None:
        message_id = str(envelope.get("message_id", "")).strip()
        if message_id and hasattr(self._redis, "delete"):
            try:
                self._redis.delete(f"{CLAIM_PREFIX}{message_id}")
            except Exception:
                return

    def _expire_claim(self, key: str, lease_seconds: int) -> None:
        """Bound claim-marker lifetime so crashed workers leave recoverable state."""
        if not key or not hasattr(self._redis, "expire"):
            return
        seconds = max(30, int(lease_seconds))
        if not self._redis.expire(key, seconds):
            raise TaskQueueError("unable to refresh task lease expiry")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
