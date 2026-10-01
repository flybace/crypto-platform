import pytest

from backend.app.services.strategy_registry import StrategyRegistry


def test_strategy_management_is_persisted_and_applies_to_catalog(tmp_path) -> None:
    path = tmp_path / "strategies.json"
    registry = StrategyRegistry(state_path=path)

    updated = registry.update(
        "sma_cross",
        enabled=False,
        note="暂时停用，等待下一轮参数验证",
    )

    assert updated["enabled"] is False
    assert updated["note"]
    restored = StrategyRegistry(state_path=path)
    assert restored.get("sma_cross")["enabled"] is False
    assert next(item for item in restored.catalog() if item["strategy_id"] == "sma_cross")["enabled"] is False
    with pytest.raises(ValueError, match="disabled"):
        restored.assert_enabled("sma_cross")


def test_strategy_management_rejects_unknown_modes_and_parameters(tmp_path) -> None:
    registry = StrategyRegistry(state_path=tmp_path / "strategies.json")

    with pytest.raises(ValueError, match="unsupported strategy mode"):
        registry.update("sma_cross", modes=["live"])
    with pytest.raises(ValueError, match="unknown strategy parameters"):
        registry.update("sma_cross", default_parameters={"unknown": 1})
