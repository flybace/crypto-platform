from datetime import UTC, datetime, timedelta
import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from backend.app.services.task_log_archive import TaskLogArchive
from backend.app.services.task_log_lifecycle import (
    PURGE_CONFIRMATION,
    TaskLogArchiveLifecycle,
    TaskLogLifecycleError,
)


def _seed(archive: TaskLogArchive, task_id: str) -> list[dict[str, object]]:
    return [
        archive.write(
            {
                "task_id": task_id,
                "event_id": event_id,
                "event_type": event_type,
                "status": status,
                "payload": {"completed": event_id},
                "created_at": f"2026-09-18T00:00:0{event_id}+00:00",
            }
        )
        for event_id, event_type, status in (
            (1, "started", "running"),
            (2, "completed", "completed"),
        )
    ]


def _task_directory(archive: TaskLogArchive, task_id: str) -> Path:
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    return archive.root / digest[:2] / digest


def _manifest_path(archive: TaskLogArchive, task_id: str) -> Path:
    return _task_directory(archive, task_id) / "manifest.json"


def _write_orphan(archive: TaskLogArchive, task_id: str, event_id: int = 99) -> Path:
    document = {
        "contract_version": "task-log-v1",
        "task_id": task_id,
        "event_id": event_id,
        "event_type": "orphan",
        "status": "completed",
        "message": "orphan object",
        "payload": {},
        "created_at": "2026-09-18T00:00:00+00:00",
    }
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    content_digest = hashlib.sha256(encoded).hexdigest()
    path = _task_directory(archive, task_id) / f"{event_id:020d}-{content_digest}.json"
    path.write_bytes(encoded)
    return path


def _write_tar(path: Path, members: list[tuple[str, bytes]]) -> None:
    with tarfile.open(path, mode="w") as bundle:
        for name, encoded in members:
            info = tarfile.TarInfo(name)
            info.size = len(encoded)
            bundle.addfile(info, io.BytesIO(encoded))


def test_inspect_reports_a_verified_archive_inventory(tmp_path: Path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")
    task_id = "research:lifecycle-ready"
    _seed(archive, task_id)
    lifecycle = TaskLogArchiveLifecycle(archive)

    inventory = lifecycle.inspect(include_files=True)

    assert inventory["status"] == "READY"
    assert inventory["ok"] is True
    assert inventory["backup_ready"] is True
    assert inventory["task_count"] == 1
    assert inventory["event_object_count"] == 2
    assert inventory["manifest_count"] == 1
    assert inventory["orphan_object_count"] == 0
    assert len(inventory["files"]) == 3


def test_inspect_blocks_tampered_and_incomplete_archives(tmp_path: Path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")
    task_id = "research:lifecycle-tampered"
    references = _seed(archive, task_id)
    event_path = archive.root / str(references[0]["key"])
    event_path.write_text("{\"tampered\":true}", encoding="utf-8")
    lifecycle = TaskLogArchiveLifecycle(archive)

    tampered = lifecycle.inspect()

    assert tampered["status"] == "CORRUPT"
    assert tampered["backup_ready"] is False
    assert tampered["corrupt_count"] >= 1
    with pytest.raises(TaskLogLifecycleError, match="not safe to back up"):
        lifecycle.export_backup(tmp_path / "tampered.tar")

    archive = TaskLogArchive(tmp_path / "missing-manifest")
    _seed(archive, "research:lifecycle-missing-manifest")
    missing_task_id = "research:lifecycle-missing-manifest"
    _manifest_path(archive, missing_task_id).unlink()

    missing = TaskLogArchiveLifecycle(archive).inspect()

    assert missing["status"] == "CORRUPT"
    assert missing["backup_ready"] is False
    assert missing["missing_manifest_count"] == 1


def test_orphans_and_transient_files_are_not_backup_ready(tmp_path: Path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")
    task_id = "research:lifecycle-orphan"
    _seed(archive, task_id)
    orphan = _write_orphan(archive, task_id)
    (archive.root / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    (orphan.parent / "partial.tmp").write_text("partial", encoding="utf-8")

    inventory = TaskLogArchiveLifecycle(archive).inspect()

    assert inventory["status"] == "CORRUPT"
    assert inventory["ok"] is False
    assert inventory["backup_ready"] is False
    assert inventory["orphan_object_count"] == 1
    assert inventory["unexpected_file_count"] == 1
    assert inventory["transient_file_count"] == 1


def test_export_verify_and_restore_use_a_verified_isolated_copy(tmp_path: Path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")
    task_id = "research:lifecycle-backup"
    _seed(archive, task_id)
    lifecycle = TaskLogArchiveLifecycle(archive)
    backup = tmp_path / "backup" / "task-logs.tar"

    exported = lifecycle.export_backup(backup)
    verified = lifecycle.verify_backup(backup)
    restored = lifecycle.restore_backup(backup, tmp_path / "restored-logs")

    assert exported["state"] == "READY"
    assert exported["sha256"]
    assert verified["state"] == "READY"
    assert verified["inventory_sha256"] == exported["inventory_sha256"]
    assert restored["state"] == "READY"
    restored_inventory = TaskLogArchiveLifecycle(tmp_path / "restored-logs").inspect()
    assert restored_inventory["status"] == "READY"
    assert restored_inventory["inventory_sha256"] == exported["inventory_sha256"]

    with pytest.raises(TaskLogLifecycleError, match="already exists"):
        lifecycle.export_backup(backup)
    with pytest.raises(TaskLogLifecycleError, match="outside the archive root"):
        lifecycle.export_backup(archive.root / "nested-backup.tar")


def test_backup_validation_rejects_malicious_paths_and_bad_counts(tmp_path: Path) -> None:
    archive = TaskLogArchive(tmp_path / "logs")
    lifecycle = TaskLogArchiveLifecycle(archive)
    malicious = tmp_path / "malicious.tar"
    _write_tar(malicious, [("../outside.txt", b"must not be extracted")])

    with pytest.raises(TaskLogLifecycleError, match="unsafe path"):
        lifecycle.verify_backup(malicious)
    with pytest.raises(TaskLogLifecycleError, match="unsafe path"):
        lifecycle.restore_backup(malicious, tmp_path / "malicious-restore")

    archive = TaskLogArchive(tmp_path / "valid-logs")
    _seed(archive, "research:lifecycle-counts")
    valid_backup = tmp_path / "valid.tar"
    TaskLogArchiveLifecycle(archive).export_backup(valid_backup)
    with tarfile.open(valid_backup, mode="r") as bundle:
        members = {member.name: bundle.extractfile(member).read() for member in bundle.getmembers()}
    manifest = json.loads(members["backup-manifest.json"].decode("utf-8"))
    manifest["event_object_count"] = 0
    body = dict(manifest)
    body.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    members["backup-manifest.json"] = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    bad_counts = tmp_path / "bad-counts.tar"
    _write_tar(bad_counts, list(members.items()))

    with pytest.raises(TaskLogLifecycleError, match="event count mismatch"):
        TaskLogArchiveLifecycle(archive).verify_backup(bad_counts)


def test_retention_plan_is_read_only_until_explicitly_purged(tmp_path: Path) -> None:
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    archive = TaskLogArchive(tmp_path / "logs")
    old_task = "research:lifecycle-old"
    new_task = "research:lifecycle-new"
    _seed(archive, old_task)
    _seed(archive, new_task)
    lifecycle = TaskLogArchiveLifecycle(archive, retention_days=30, clock=lambda: now)
    records = [
        {"task_id": old_task, "status": "completed", "finished_at": (now - timedelta(days=31)).isoformat()},
        {"task_id": new_task, "status": "completed", "finished_at": (now - timedelta(days=2)).isoformat()},
    ]

    before = lifecycle.inspect()
    plan = lifecycle.plan_retention(records, now=now)
    after = lifecycle.inspect()

    assert plan["state"] == "PLANNED"
    assert plan["candidate_count"] == 1
    assert plan["candidates"][0]["task_id"] == old_task
    assert before["inventory_sha256"] == after["inventory_sha256"]
    assert _task_directory(archive, old_task).exists()

    backup = tmp_path / "retention.tar"
    lifecycle.export_backup(backup)
    with pytest.raises(TaskLogLifecycleError, match="explicit confirmation"):
        lifecycle.purge_retention_plan(plan, backup=backup, confirmation="YES")
    assert _task_directory(archive, old_task).exists()

    other_archive = TaskLogArchive(tmp_path / "other-logs")
    _seed(other_archive, "research:other")
    other_backup = tmp_path / "other.tar"
    TaskLogArchiveLifecycle(other_archive).export_backup(other_backup)
    with pytest.raises(TaskLogLifecycleError, match="does not match"):
        lifecycle.purge_retention_plan(plan, backup=other_backup, confirmation=PURGE_CONFIRMATION)
    assert _task_directory(archive, old_task).exists()

    archive.write({"task_id": new_task, "event_id": 3, "event_type": "progress", "status": "running"})
    with pytest.raises(TaskLogLifecycleError, match="changed after retention planning"):
        lifecycle.purge_retention_plan(plan, backup=backup, confirmation=PURGE_CONFIRMATION)
    assert _task_directory(archive, old_task).exists()


def test_retention_purge_removes_only_the_verified_terminal_candidate(tmp_path: Path) -> None:
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    archive = TaskLogArchive(tmp_path / "logs")
    old_task = "research:lifecycle-purge-old"
    active_task = "research:lifecycle-purge-active"
    _seed(archive, old_task)
    _seed(archive, active_task)
    lifecycle = TaskLogArchiveLifecycle(archive, retention_days=30, clock=lambda: now)
    plan = lifecycle.plan_retention(
        [
            {"task_id": old_task, "status": "failed", "finished_at": (now - timedelta(days=31)).isoformat()},
            {"task_id": active_task, "status": "running", "updated_at": (now - timedelta(days=90)).isoformat()},
        ],
        now=now,
    )
    backup = tmp_path / "purge.tar"
    lifecycle.export_backup(backup)

    result = lifecycle.purge_retention_plan(
        plan,
        backup=backup,
        confirmation=PURGE_CONFIRMATION,
    )

    assert result["state"] == "PURGED"
    assert result["removed_task_ids"] == [old_task]
    assert not _task_directory(archive, old_task).exists()
    assert _task_directory(archive, active_task).exists()
    assert result["inventory"]["status"] == "READY"
