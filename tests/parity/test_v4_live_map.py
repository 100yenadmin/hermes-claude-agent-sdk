from __future__ import annotations

import copy
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.v4_live_map import V4LiveMapViolation, load_v4_live_execution_map, validate_v4_live_execution_map

ROOT = Path(__file__).parents[2]
MAP_PATH = ROOT / "qa" / "parity-v4-live-execution-map.yaml"


def test_provider_free_map_closes_immutable_v4_accounting() -> None:
    document = load_v4_live_execution_map(MAP_PATH); validated = validate_v4_live_execution_map(document, map_path=MAP_PATH)
    assert {key: validated[key] for key in ("provider_live_rows", "mandatory_paths", "required_trial_packets", "parent_calls", "child_calls", "total_calls", "reserve_calls")} == {"provider_live_rows": 70, "mandatory_paths": 158, "required_trial_packets": 242, "parent_calls": 120, "child_calls": 16, "total_calls": 136, "reserve_calls": 44}
    assert len(document["rows"]) == len(set(validated["row_keys"])) == 70 and document["non_executable_rows"] == []
    assert document["target"] == {"routing_provider": "claude-agent-sdk", "receipt_provider": "anthropic", "effective_model": "claude-fable-5-1", "execution_mode": "normal_hermes_gateway_live", "gateway_entrypoint": "python -m tui_gateway.entry", "map_construction": "provider_free", "external_delivery": "never"}
    assert document["source"]["candidate_identity"] == "unresolved" and document["source"]["identity_kind"] == "authoring_base_only"


def test_map_has_exact_feature_partition_and_child_budget() -> None:
    document = load_v4_live_execution_map(MAP_PATH); features = {item["id"]: item for item in document["features"]}; keys = [key for feature in features.values() for key in feature["row_keys"]]
    assert set(features) == {f"F{index}" for index in range(9)} and sum(item["parent_calls"] for item in features.values()) == 120 and sum(item["child_calls"] for item in features.values()) == 16
    assert len(keys) == len(set(keys)) == 70 and set(keys) == {f"{row['source_pack']}/{row['source_item_id']}" for row in document["rows"]}
    assert len(document["child_calls"]) == 16 and {call["feature_id"] for call in document["child_calls"]} == {"F2", "F3", "F4"}
    assert all(call["max_iterations"] == 1 and call["child_tools"] == [] and call["retry"] is False and call["delivery"] is False and call["local_only"] is True for call in document["child_calls"])


def test_aliases_keep_fable_and_external_rows_never_deliver() -> None:
    document = load_v4_live_execution_map(MAP_PATH)
    assert {item["alias_id"] for item in document["semantic_aliases"]} == {"codex-luna", "codex-sol", "opencode-free"}
    assert all(item["effective_model"] == "claude-fable-5-1" and item["execution"] == "alias_metadata_only" for item in document["semantic_aliases"])
    assert all(item["live_call_accounting"] == "feature_budgeted" and "provider_calls" not in item for item in document["mechanism_classes"].values())
    assert "map_validation_used_zero_provider_auth_gateway_calls" in document["proof_boundary"]
    external = set(document["external_recipient_policy"]["row_keys"]); assert external and document["external_recipient_policy"]["delivery"] == "never"
    assert all(row["delivery_mode"] == "host_denial_local_recovery" for row in document["rows"] if f"{row['source_pack']}/{row['source_item_id']}" in external)


@pytest.mark.parametrize("mutation", [lambda value: value["rows"].pop(), lambda value: value["rows"][0]["mandatory_paths"].append("denial"), lambda value: value["rows"][0].update({"predecessor_execution_id": "wrong-id"}), lambda value: value["rows"][0].update({"required_trial_indexes": [1]}), lambda value: value["child_calls"][0].update({"retry": True}), lambda value: value["target"].update({"routing_provider": "alternate"})])
def test_map_rejects_drift_or_unsafe_execution(mutation) -> None:
    document = load_v4_live_execution_map(MAP_PATH); mutated = copy.deepcopy(document); mutation(mutated)
    with pytest.raises(V4LiveMapViolation):
        validate_v4_live_execution_map(mutated, map_path=MAP_PATH)
