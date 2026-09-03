from __future__ import annotations

from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.v4_fixture_materializer import (
    V4FixtureMaterializer,
    V4FixtureMaterializerViolation,
)

ROOT = Path(__file__).parents[2]


def _materializer() -> V4FixtureMaterializer:
    return V4FixtureMaterializer(map_path=ROOT / "qa" / "parity-v4-live-execution-map.yaml")


def test_materializes_every_declared_trial_and_turn_without_prompt_receipt_content() -> None:
    materializer = _materializer()
    values = materializer.materialize_all()
    assert len(values) == sum(len(row["trial_indexes"]) * row.turn_count for row in materializer.fixtures)
    assert len({(item.row_key, item.trial_index, item.turn_index) for item in values}) == len(values)
    assert all(item.prompt.byte_count > 0 and len(item.prompt.sha256) == 64 for item in values)
    receipt = values[0].to_receipt()
    assert set(receipt) == {"schema_version", "row_key", "root", "trial_index", "turn_index", "path", "mechanism_class", "prompt", "expected"}
    assert set(receipt["prompt"]) == {"byte_count", "sha256"}
    assert values[0].prompt.ephemeral_text not in repr(receipt)


def test_special_recipes_have_distinct_labels_and_one_positive_parent_call() -> None:
    materializer = _materializer()
    expected = {
        "openclaw_active/source-docs-discovery-report": (2, ("source", "docs")),
        "openclaw_active/memory-recall": (2, ("store", "recall")),
        "openclaw_active/thread-memory-isolation": (4, ("seed", "isolate", "probe", "close")),
        "openclaw_active/config-restart-capability-flip": (2, ("before_restart", "after_restart")),
    }
    for row_key, (turn_count, labels) in expected.items():
        values = [materializer.materialize(row_key, trial_index=1, turn_index=turn) for turn in range(1, turn_count + 1)]
        assert tuple(item.host.turn_label for item in values) == labels
        assert all(item.host.expected_parent_provider_calls == 1 for item in values)


def test_children_approval_recovery_and_external_delivery_are_closed_expectations() -> None:
    materializer = _materializer()
    fanout = materializer.materialize("v2_non_soak/ORCH-05", trial_index=1, turn_index=1)
    assert fanout.host.expected_child_count == 2 and fanout.host.allowed_tool_names == ("mcp__hermes-tools__delegate_task",)
    assert fanout.host.expected_child_ordinals == (1, 2)
    approval = materializer.materialize("clawprobench_native/constraints_23_external_approval_boundary_live", trial_index=2, turn_index=1)
    assert approval.host.approval_choice == "deny" and approval.host.allowed_tool_names == ("mcp__hermes-tools__v4_fixture_local_state",)
    assert approval.host.approval_sequence == ("deny", "safe_recovery")
    assert approval.host.expected_parent_provider_calls == 1
    assert approval.host.external_delivery_allowed is False and "direct or alternate provider calls" in approval.prompt.ephemeral_text
    assert "delivery_boundary" in approval.host.required_observations


@pytest.mark.parametrize(
    "kwargs",
    [
        {"row_key": "unknown/row", "trial_index": 1, "turn_index": 1},
        {"row_key": "v2_non_soak/AUTH-01", "trial_index": 9, "turn_index": 1},
        {"row_key": "v2_non_soak/AUTH-01", "trial_index": 1, "turn_index": 2},
        {"row_key": "v2_non_soak/AUTH-01", "trial_index": 1, "turn_index": 1, "root": "unsafe/root"},
    ],
)
def test_rejects_unknown_identity_or_root(kwargs: dict[str, object]) -> None:
    with pytest.raises(V4FixtureMaterializerViolation):
        _materializer().materialize(**kwargs)


def test_materializer_rejects_unsafe_fixture_mapping() -> None:
    fixture = _materializer().fixtures[0].to_dict()
    fixture["raw_prompt"] = "must not enter the materializer"
    with pytest.raises(V4FixtureMaterializerViolation):
        _materializer().materialize(fixture, trial_index=1, turn_index=1)
