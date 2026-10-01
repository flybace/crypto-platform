import json
from pathlib import Path


CONTRACT_ROOT = Path(__file__).parents[2] / "contracts"
CONTRACTS = (
    CONTRACT_ROOT / "market" / "instrument-v1.schema.json",
    CONTRACT_ROOT / "market" / "order-book-snapshot-v1.schema.json",
    CONTRACT_ROOT / "market" / "opportunity-v1.schema.json",
    CONTRACT_ROOT / "market" / "dataset-manifest-v1.schema.json",
    CONTRACT_ROOT / "market" / "history-raw-response-v1.schema.json",
    CONTRACT_ROOT / "strategy" / "order-intent-v1.schema.json",
    CONTRACT_ROOT / "strategy" / "signal-v1.schema.json",
    CONTRACT_ROOT / "strategy" / "incubator-v1.schema.json",
    CONTRACT_ROOT / "paper" / "paper-order-v1.schema.json",
    CONTRACT_ROOT / "paper" / "paper-account-v1.schema.json",
    CONTRACT_ROOT / "execution" / "order-request-v1.schema.json",
    CONTRACT_ROOT / "execution" / "account-snapshot-v1.schema.json",
    CONTRACT_ROOT / "execution" / "ledger-entry-v1.schema.json",
    CONTRACT_ROOT / "gace" / "app-manifest-v1.schema.json",
    CONTRACT_ROOT / "gace" / "capability-catalog-v1.schema.json",
    CONTRACT_ROOT / "ai" / "action-request-v1.schema.json",
)


def test_all_m0_contracts_are_versioned_strict_json_schemas() -> None:
    for path in CONTRACTS:
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["$schema"].endswith("draft/2020-12/schema")
        assert document["$id"].endswith(path.name)
        assert document["type"] == "object"
        assert document["additionalProperties"] is False
        assert len(document["required"]) == len(set(document["required"]))


def test_execution_contract_is_sell_only_and_contains_no_secret_field() -> None:
    path = CONTRACT_ROOT / "execution" / "order-request-v1.schema.json"
    document = json.loads(path.read_text(encoding="utf-8"))

    assert document["properties"]["side"] == {"const": "SELL"}
    assert "secret" not in json.dumps(document).lower()
    assert "api_key" not in json.dumps(document).lower()


def test_gace_capability_contract_is_read_only_and_has_no_write_surface() -> None:
    path = CONTRACT_ROOT / "gace" / "capability-catalog-v1.schema.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["properties"]["read_only"] == {"const": True}
    assert document["properties"]["runtime"] == {"const": "standalone"}
    assert document["properties"]["write_capabilities"]["maxItems"] == 0
