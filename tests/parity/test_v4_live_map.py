from __future__ import annotations

import copy
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.v4_live_map import V4LiveMapViolation, load_v4_live_execution_map, validate_v4_live_execution_map

ROOT = Path(__file__).parents[2]
MAP_PATH = ROOT / "qa" / "parity-v4-live-execution-map.yaml"


def test_provider_free_map_closes_immutable_v4_accounting() -> None:
    document = load_v4_live_execution_map(MAP_PATH); validated = validate_v4_live_execution_map(document, map_path=MAP_PATH)
    assert {key: validated[key] for key in ("provider_live_rows", "mandatory_paths", "required_trial_packets", "parent_calls", "child_calls", "total_calls", "reserve_calls")} == {"provider_live_rows": 70, "mandatory_paths": 158, "required_trial_packets": 242, "parent_calls": 134, "child_calls": 16, "total_calls": 150, "reserve_calls": 30}
    assert len(document["rows"]) == len(set(validated["row_keys"])) == 70 and document["non_executable_rows"] == []
    assert document["target"] == {"routing_provider": "claude-agent-sdk", "receipt_provider": "anthropic", "effective_model": "claude-fable-5-1", "execution_mode": "normal_hermes_gateway_live", "gateway_entrypoint": "python -m tui_gateway.entry", "map_construction": "provider_free", "external_delivery": "never"}
    assert document["source"]["candidate_identity"] == "unresolved" and document["source"]["identity_kind"] == "authoring_base_only"


def test_map_has_exact_feature_partition_and_child_budget() -> None:
    document = load_v4_live_execution_map(MAP_PATH); features = {item["id"]: item for item in document["features"]}; keys = [key for feature in features.values() for key in feature["row_keys"]]
    assert set(features) == {f"F{index}" for index in range(9)} and sum(item["parent_calls"] for item in features.values()) == 134 and sum(item["child_calls"] for item in features.values()) == 16
    assert len(keys) == len(set(keys)) == 70 and set(keys) == {f"{row['source_pack']}/{row['source_item_id']}" for row in document["rows"]}
    assert len(document["child_calls"]) == 16 and {call["feature_id"] for call in document["child_calls"]} == {"F2", "F3", "F4"}
    assert all(call["max_iterations"] == 1 and call["child_tools"] == [] and call["retry"] is False and call["delivery"] is False and call["local_only"] is True for call in document["child_calls"])


def test_rows_have_exact_provider_free_ledger_and_bundle_boundaries() -> None:
    document = load_v4_live_execution_map(MAP_PATH)
    rows = {f"{row['source_pack']}/{row['source_item_id']}": row for row in document["rows"]}
    specials = {
        "openclaw_active/source-docs-discovery-report": 2,
        "openclaw_active/memory-recall": 2,
        "openclaw_active/thread-memory-isolation": 12,
        "openclaw_active/config-restart-capability-flip": 6,
    }
    child_counts = {}
    for call in document["child_calls"]:
        child_counts[call["row_key"]] = child_counts.get(call["row_key"], 0) + 1
    delivery_counts = {}
    for row_key, path, trial_index in {
        (call["row_key"], call["path"], call["trial_index"])
        for call in document["child_calls"]
    }:
        delivery_counts[row_key] = delivery_counts.get(row_key, 0) + 1
    assert all({"parent_calls", "child_calls", "bundle_mode", "session_boundary"} <= set(row) for row in rows.values())
    assert all(
        row["parent_calls"] == specials.get(key, len(row["required_trial_indexes"])) + delivery_counts.get(key, 0)
        and row["child_calls"] == child_counts.get(key, 0)
        for key, row in rows.items()
    )
    background_modes = {"v2_non_soak/BG-01": "background_one_entry_batch_join", "v2_non_soak/BG-03": "background_child_cancel_restart"}
    assert all(
        row["bundle_mode"] == background_modes.get(
            key,
            "parent_two_children" if key in {"v2_non_soak/ORCH-05", "openclaw_active/subagent-fanout-synthesis"} else "parent_child" if child_counts.get(key, 0) else "parent_only",
        )
        for key, row in rows.items()
    )
    assert rows["openclaw_active/source-docs-discovery-report"]["session_boundary"] == "same_session_source_then_docs"
    assert rows["openclaw_active/memory-recall"]["session_boundary"] == "same_session_store_then_recall"
    assert rows["openclaw_active/thread-memory-isolation"]["session_boundary"] == "four_turns_per_isolated_trial"
    assert rows["openclaw_active/config-restart-capability-flip"]["session_boundary"] == "before_after_restart_per_trial"
    assert all(
        row["session_boundary"] == "isolated_trial"
        for key, row in rows.items()
        if key not in {
            "openclaw_active/source-docs-discovery-report",
            "openclaw_active/memory-recall",
            "openclaw_active/thread-memory-isolation",
            "openclaw_active/config-restart-capability-flip",
        }
        and row["child_calls"] == 0
    )


def test_child_calls_bind_exact_trial_and_ordinal() -> None:
    document = load_v4_live_execution_map(MAP_PATH)
    actual = {
        (call["row_key"], call["path"], call["trial_index"], call["child_ordinal"], call["child_count"], call["session_boundary"])
        for call in document["child_calls"]
    }
    expected = {
        *(('v2_non_soak/TOOL-05', 'positive', trial, 1, 1, 'parent_child') for trial in (1, 2, 3)),
        *(('v2_non_soak/ORCH-01', 'positive', 1, 1, 1, 'parent_child'),
          ('v2_non_soak/ORCH-02', 'positive', 1, 1, 1, 'parent_child'),
          ('v2_non_soak/ORCH-03', 'positive', 1, 1, 1, 'parent_child'),
          ('v2_non_soak/ORCH-04', 'positive', 1, 1, 1, 'parent_child'),
          ('v2_non_soak/ORCH-06', 'positive', 1, 1, 1, 'parent_child'),
          ('v2_non_soak/ORCH-07', 'positive', 1, 1, 1, 'parent_child')),
        *(('v2_non_soak/ORCH-05', 'positive', 1, ordinal, 2, 'parent_two_children') for ordinal in (1, 2)),
        *(('v2_non_soak/BG-01', 'positive', 1, 1, 1, 'background_one_entry_batch_join'),
          ('v2_non_soak/BG-03', 'positive', 1, 1, 1, 'background_child_cancel_restart')),
        *(('openclaw_active/subagent-handoff', 'positive', 1, 1, 1, 'parent_child'),),
        *(('openclaw_active/subagent-fanout-synthesis', 'positive', 1, ordinal, 2, 'parent_two_children') for ordinal in (1, 2)),
    }
    assert actual == expected
    assert all(
        {"effective_provider", "max_iterations", "child_tools", "retry", "delivery", "local_only"} <= set(call)
        and call["effective_provider"] == "fable"
        and call["max_iterations"] == 1
        and call["child_tools"] == []
        and call["retry"] is False
        and call["delivery"] is False
        and call["local_only"] is True
        for call in document["child_calls"]
    )
    assert not actual & {
        (key, path, trial, 1, 1, "parent_child")
        for key, path, trial in (
            ("clawprobench_native/constraints_19_cron_conflict_buffer_live", "positive", 1),
            ("clawprobench_native/constraints_23_external_approval_boundary_live", "denial", 1),
            ("clawprobench_native/planning_19_agent_delegation_boundary_live", "positive", 1),
            ("clawprobench_native/planning_20_session_agent_handoff_live", "positive", 1),
        )
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["rows"][0].pop("parent_calls"),
        lambda value: value["rows"][0].update({"parent_calls": 4}),
        lambda value: value["rows"][1].update({"parent_calls": 2}),
        lambda value: value["rows"][0].update({"child_calls": 1}),
        lambda value: value["child_calls"][0].pop("trial_index"),
        lambda value: value["child_calls"][1].update({"trial_index": 1}),
        lambda value: value["child_calls"][1].update({"child_ordinal": 2}),
    ],
)
def test_map_rejects_missing_duplicate_or_coincidental_row_accounting(mutation) -> None:
    document = load_v4_live_execution_map(MAP_PATH); mutated = copy.deepcopy(document); mutation(mutated)
    with pytest.raises(V4LiveMapViolation):
        validate_v4_live_execution_map(mutated, map_path=MAP_PATH)


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
