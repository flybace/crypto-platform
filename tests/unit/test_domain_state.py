import json

from adapters.standalone.state_store import SqlStateStore
from backend.app.services.strategy_registry import StrategyRegistry
from backend.app.services.task_store import TaskStore


def _database_url(path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_domain_snapshot_migrates_legacy_json_and_keeps_sql_authoritative(tmp_path) -> None:
    database = tmp_path / "control-plane.sqlite3"
    legacy = tmp_path / "strategies.json"
    legacy.write_text(
        json.dumps({"version": 1, "strategies": {"sma_cross": {"enabled": False}}}),
        encoding="utf-8",
    )

    first_store = TaskStore(_database_url(database), required=True)
    first = SqlStateStore(first_store, "crypto.domain.strategies", legacy_path=legacy)
    assert first.load({"version": 1, "strategies": {}})["strategies"]["sma_cross"]["enabled"] is False
    assert first_store.domain_snapshot_status()["snapshot_count"] == 1

    first.save({"version": 1, "strategies": {"sma_cross": {"enabled": True}}})
    assert json.loads(legacy.read_text(encoding="utf-8"))["strategies"]["sma_cross"]["enabled"] is True
    first_store.close()

    second_store = TaskStore(_database_url(database), required=True)
    second = SqlStateStore(second_store, "crypto.domain.strategies", legacy_path=legacy)
    assert second.load({"version": 1, "strategies": {}})["strategies"]["sma_cross"]["enabled"] is True
    metadata = second_store.list_domain_snapshots()
    assert metadata[0]["snapshot_key"] == "crypto.domain.strategies"
    assert metadata[0]["version"] == 2
    second_store.close()


def test_strategy_registry_reads_updates_written_by_another_process(tmp_path) -> None:
    database = tmp_path / "control-plane.sqlite3"
    first_store = TaskStore(_database_url(database), required=True)
    first = StrategyRegistry(
        state_store=SqlStateStore(first_store, "crypto.domain.strategies")
    )
    first.update("sma_cross", enabled=False)

    second_store = TaskStore(_database_url(database), required=True)
    second = StrategyRegistry(
        state_store=SqlStateStore(second_store, "crypto.domain.strategies")
    )
    assert second.get("sma_cross")["enabled"] is False
    assert second_store.domain_snapshot_status()["snapshot_count"] == 1
    first_store.close()
    second_store.close()
