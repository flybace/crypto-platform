import pytest

from backend.app.services.task_quota import TaskQuota, TaskQuotaExceeded


def test_task_quota_rejects_large_payloads_and_item_counts() -> None:
    quota = TaskQuota(
        max_runtime_seconds=10,
        max_payload_bytes=20,
        max_result_bytes=20,
        max_items=2,
    )

    with pytest.raises(TaskQuotaExceeded) as payload_error:
        quota.validate_payload({"value": "x" * 40})
    assert payload_error.value.kind == "TASK_PAYLOAD_TOO_LARGE"

    with pytest.raises(TaskQuotaExceeded) as item_error:
        quota.validate_items(3)
    assert item_error.value.kind == "TASK_ITEM_LIMIT_EXCEEDED"


def test_task_quota_rejects_oversized_results() -> None:
    quota = TaskQuota(max_result_bytes=10)

    with pytest.raises(TaskQuotaExceeded) as error:
        quota.validate_result({"result": "x" * 20})
    assert error.value.as_error()["kind"] == "TASK_RESULT_TOO_LARGE"
