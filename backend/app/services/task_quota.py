"""Server-side budgets for bounded background task execution."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any


class TaskQuotaExceeded(RuntimeError):
    """Raised when a task exceeds a control-plane resource budget."""

    def __init__(self, kind: str, message: str) -> None:
        self.kind = str(kind)
        super().__init__(str(message))

    def as_error(self) -> dict[str, str]:
        return {"kind": self.kind, "message": str(self)}


@dataclass(frozen=True, slots=True)
class TaskQuota:
    """Small, conservative limits shared by API dispatch and workers."""

    max_runtime_seconds: int = 3600
    max_payload_bytes: int = 262_144
    max_result_bytes: int = 2_097_152
    max_items: int = 100

    def __post_init__(self) -> None:
        for name in (
            "max_runtime_seconds",
            "max_payload_bytes",
            "max_result_bytes",
            "max_items",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")

    @classmethod
    def from_settings(cls, settings: Any) -> "TaskQuota":
        return cls(
            max_runtime_seconds=int(getattr(settings, "task_max_runtime_seconds", cls.max_runtime_seconds)),
            max_payload_bytes=int(getattr(settings, "task_max_payload_bytes", cls.max_payload_bytes)),
            max_result_bytes=int(getattr(settings, "task_max_result_bytes", cls.max_result_bytes)),
            max_items=int(getattr(settings, "task_max_items", cls.max_items)),
        )

    def validate_payload(self, payload: Any) -> None:
        size = _json_size(payload)
        if size > self.max_payload_bytes:
            raise TaskQuotaExceeded(
                "TASK_PAYLOAD_TOO_LARGE",
                f"task payload is {size} bytes; limit is {self.max_payload_bytes}",
            )

    def validate_items(self, total: int) -> None:
        bounded = max(0, int(total))
        if bounded > self.max_items:
            raise TaskQuotaExceeded(
                "TASK_ITEM_LIMIT_EXCEEDED",
                f"task requests {bounded} items; limit is {self.max_items}",
            )

    def validate_result(self, result: Any) -> None:
        size = _json_size(result)
        if size > self.max_result_bytes:
            raise TaskQuotaExceeded(
                "TASK_RESULT_TOO_LARGE",
                f"task result is {size} bytes; limit is {self.max_result_bytes}",
            )

    def budget(self) -> "TaskBudget":
        return TaskBudget(self)


class TaskBudget:
    """Cooperative runtime budget used at worker checkpoints."""

    def __init__(self, quota: TaskQuota) -> None:
        self.quota = quota
        self.started_at = time.monotonic()

    def check(self) -> None:
        elapsed = time.monotonic() - self.started_at
        if elapsed > self.quota.max_runtime_seconds:
            raise TaskQuotaExceeded(
                "TASK_RUNTIME_LIMIT_EXCEEDED",
                f"task ran for {elapsed:.1f}s; limit is {self.quota.max_runtime_seconds}s",
            )


def _json_size(value: Any) -> int:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return len(encoded)
