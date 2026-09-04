from __future__ import annotations

from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.v4_fixture_materializer import (
    V4FixtureMaterializer,
    V4FixtureMaterializerViolation,
    materialize_v4_fixture,
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
    assert "fixture_tool_args" not in receipt["expected"]
    assert values[0].prompt.ephemeral_text not in repr(receipt)
    assert all(item.fixture_tool_args is None for item in values)


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
    background = materializer.materialize("v2_non_soak/BG-01", trial_index=1, turn_index=1)
    assert background.host.allowed_tool_names == ("mcp__hermes-tools__delegate_task",)
    approval = materializer.materialize("clawprobench_native/constraints_23_external_approval_boundary_live", trial_index=2, turn_index=1)
    assert approval.host.approval_choice == "deny" and approval.host.allowed_tool_names == ("mcp__hermes-tools__v4_fixture_local_state",)
    assert approval.host.approval_sequence == ("deny", "safe_recovery")
    assert approval.host.expected_parent_provider_calls == 1
    assert approval.host.external_delivery_allowed is False and "direct or alternate provider calls" in approval.prompt.ephemeral_text
    assert "delivery_boundary" in approval.host.required_observations


def test_legacy_tool_delegate_fixture_allows_hermes_child_instead_of_denial(tmp_path: Path) -> None:
    delegated = _materializer().materialize(
        "v2_non_soak/TOOL-05",
        trial_index=1,
        turn_index=1,
        task_root=tmp_path,
    )
    assert delegated.host.allowed_tool_names == ("mcp__hermes-tools__delegate_task",)
    assert delegated.host.approval_choice == "allow"
    assert delegated.host.approval_sequence == ()
    assert delegated.host.expected_child_count == 1
    assert delegated.fixture_tool_args is None
    assert "mcp__hermes-tools__v4_fixture_local_state" not in delegated.prompt.ephemeral_text
    assert "task_root=" not in delegated.prompt.ephemeral_text
    assert "Do not supply a background argument" in delegated.prompt.ephemeral_text
    assert "one durable follow-up turn for synthesis" in delegated.prompt.ephemeral_text
    assert "do not poll or submit another task" in delegated.prompt.ephemeral_text
    background = _materializer().materialize("v2_non_soak/BG-01", trial_index=1, turn_index=1)
    assert "Do not supply a background argument" in background.prompt.ephemeral_text
    assert "durable completion as one follow-up turn" in background.prompt.ephemeral_text
    assert "without polling" in background.prompt.ephemeral_text


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


def test_fixture_tool_args_use_an_ephemeral_validated_task_root(tmp_path: Path) -> None:
    value = _materializer().materialize(
        "clawprobench_native/constraints_23_external_approval_boundary_live",
        trial_index=2,
        turn_index=1,
        task_root=tmp_path,
    )
    count, item_hash = value.fixture_tool_args
    assert 0 <= count <= 32 and len(item_hash) == 64
    assert "task_root=" + str(tmp_path) in value.prompt.ephemeral_text
    assert "record (host denial expected), then check (safe recovery)" in value.prompt.ephemeral_text
    assert "item_count=" + str(count) in value.prompt.ephemeral_text
    assert "item_hash=" + item_hash in value.prompt.ephemeral_text
    assert str(tmp_path) not in repr(value) and str(tmp_path) not in repr(value.to_receipt())
    assert value.to_receipt()["expected"]["fixture_tool_args"] == {"item_count": count, "item_hash": item_hash}
    alias = materialize_v4_fixture(row_key=value.row_key, trial_index=2, turn_index=1, task_root=tmp_path, materializer=_materializer())
    assert alias.fixture_tool_args == value.fixture_tool_args


@pytest.mark.parametrize("bad_root", ["relative-root", Path("/"), "missing-root"])
def test_fixture_tool_rejects_unsafe_task_roots(tmp_path: Path, bad_root: str | Path) -> None:
    with pytest.raises(V4FixtureMaterializerViolation):
        _materializer().materialize("clawprobench_native/constraints_23_external_approval_boundary_live", trial_index=1, turn_index=1, task_root=bad_root)


def test_fixture_tool_rejects_symlink_and_non_fixture_inventory(tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    materializer = _materializer()
    with pytest.raises(V4FixtureMaterializerViolation):
        materializer.materialize("clawprobench_native/constraints_23_external_approval_boundary_live", trial_index=1, turn_index=1, task_root=link)
    with pytest.raises(V4FixtureMaterializerViolation):
        materializer.materialize("clawprobench_native/constraints_23_external_approval_boundary_live", trial_index=1, turn_index=1, task_root=tmp_path / "missing")
    with pytest.raises(V4FixtureMaterializerViolation):
        materializer.materialize("v2_non_soak/AUTH-01", trial_index=1, turn_index=1, task_root=tmp_path)
