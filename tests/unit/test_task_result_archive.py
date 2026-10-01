from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from backend.app.services.task_result_archive import TaskResultArchive, TaskResultArchiveError
from backend.app.services.task_log_archive import TaskLogArchive, TaskLogArchiveError


def test_task_result_archive_writes_and_verifies_content_addressed_object(tmp_path) -> None:
    archive = TaskResultArchive(tmp_path / "results")

    reference = archive.write("research:run-1", {"status": "completed", "rows": 3})

    assert reference["contract_version"] == "task-result-v1"
    assert reference["state"] == "READY"
    assert archive.read(reference, expected_task_id="research:run-1") == {
        "status": "completed",
        "rows": 3,
    }


def test_task_result_archive_rejects_tampered_object(tmp_path) -> None:
    archive = TaskResultArchive(tmp_path / "results")
    reference = archive.write("research:run-2", {"rows": 4})
    path = archive.root / str(reference["key"])
    path.write_text(json.dumps({"tampered": True}), encoding="utf-8")

    with pytest.raises(TaskResultArchiveError, match="checksum mismatch"):
        archive.read(reference, expected_task_id="research:run-2")


def test_task_result_archive_rejects_path_escape(tmp_path) -> None:
    archive = TaskResultArchive(tmp_path / "results")

    with pytest.raises(TaskResultArchiveError, match="path escaped its root"):
        archive.read({
            "contract_version": "task-result-v1",
            "key": "../outside.json",
            "sha256": "0" * 64,
            "bytes": 0,
        })


def test_task_log_archive_is_atomic_content_addressed_and_redacts_secrets(tmp_path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")
    event = archive.write(
        {
            "task_id": "research:run-log-1",
            "event_id": 7,
            "event_type": "completed",
            "status": "completed",
            "message": "password=should-not-be-stored",
            "payload": {
                "summary": {"status": "completed"},
                "api_key": "key-value",
                "api_signature": "signature-value",
                "nested": [{"authorization": "Bearer secret"}],
            },
            "created_at": "2026-09-17T00:00:00+00:00",
        }
    )

    restored = archive.read(event, expected_task_id="research:run-log-1", expected_event_id=7)

    assert event["contract_version"] == "task-log-v1"
    assert event["state"] == "READY"
    assert restored["payload"] == {
        "summary": {"status": "completed"},
        "api_key": "[REDACTED]",
        "api_signature": "[REDACTED]",
        "nested": [{"authorization": "[REDACTED]"}],
    }
    assert "should-not-be-stored" not in str(restored)
    assert archive.list("research:run-log-1")[0]["event_id"] == 7


def test_task_log_archive_rejects_path_escape(tmp_path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")

    with pytest.raises(TaskLogArchiveError, match="path escaped its root"):
        archive.read(
            {
                "contract_version": "task-log-v1",
                "key": "../outside.json",
                "sha256": "0" * 64,
                "bytes": 0,
                "event_id": 1,
            }
        )


def test_task_log_archive_rejects_cross_task_and_event_reference_reuse(tmp_path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")
    first = archive.write(
        {
            "task_id": "research:run-log-a",
            "event_id": 3,
            "event_type": "progress",
            "payload": {"completed": 1},
        }
    )

    with pytest.raises(TaskLogArchiveError, match="task path mismatch"):
        archive.read(first, expected_task_id="research:run-log-b")

    wrong_event = {**first, "event_id": 4}
    with pytest.raises(TaskLogArchiveError, match="key digest is invalid"):
        archive.read(wrong_event, expected_task_id="research:run-log-a")


def test_task_log_archive_writes_and_verifies_a_task_manifest(tmp_path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")
    task_id = "research:manifest-1"

    first = archive.write({"task_id": task_id, "event_id": 1, "event_type": "started"})
    second = archive.write({"task_id": task_id, "event_id": 2, "event_type": "completed"})

    manifest = archive.read_manifest(task_id)
    assert manifest is not None
    assert manifest["contract_version"] == "task-log-manifest-v1"
    assert manifest["event_count"] == 2
    assert [item["event_id"] for item in manifest["events"]] == [1, 2]
    assert archive.list(task_id) == [
        archive.read(first, expected_task_id=task_id),
        archive.read(second, expected_task_id=task_id),
    ]
    reference = archive.manifest_reference(task_id)
    assert reference is not None
    assert reference["contract_version"] == "task-log-manifest-v1"
    assert archive.verify_task(task_id, expected_references=[first, second])["ok"] is True


def test_task_log_archive_detects_and_repairs_a_corrupt_manifest(tmp_path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")
    task_id = "research:manifest-repair"
    event = archive.write({"task_id": task_id, "event_id": 1, "event_type": "progress"})
    manifest_path = archive.root / str(archive.manifest_reference(task_id)["key"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manifest_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    checked = archive.verify_task(task_id, expected_references=[event])
    assert checked["ok"] is False
    assert "checksum mismatch" in str(checked["error"])

    repaired = archive.ensure_manifest(task_id, expected_references=[event])
    assert repaired is not None
    assert archive.verify_task(task_id, expected_references=[event])["ok"] is True


def test_task_log_archive_detects_a_half_written_event_object(tmp_path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")
    task_id = "research:manifest-half-write"
    event = archive.write({"task_id": task_id, "event_id": 1, "event_type": "progress"})
    (archive.root / str(event["key"])).write_text('{"contract_version":"task-log-v1"', encoding="utf-8")

    checked = archive.verify_task(task_id, expected_references=[event])
    assert checked["ok"] is False
    assert "checksum mismatch" in str(checked["error"])


def test_task_log_archive_serializes_concurrent_manifest_updates(tmp_path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")
    task_id = "research:manifest-concurrent"

    def write_event(event_id: int) -> dict[str, object]:
        return archive.write(
            {
                "task_id": task_id,
                "event_id": event_id,
                "event_type": "progress",
                "payload": {"completed": event_id},
            }
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        references = list(executor.map(write_event, range(1, 21)))

    manifest = archive.read_manifest(task_id)
    assert manifest is not None
    assert manifest["event_count"] == 20
    assert [item["event_id"] for item in manifest["events"]] == list(range(1, 21))
    assert archive.verify_task(task_id, expected_references=references)["ok"] is True
