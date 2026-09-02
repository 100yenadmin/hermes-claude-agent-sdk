from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from hermes_claude_agent_sdk.parity.native_sandbox import (
    SKILLS_INVENTORY,
    native_environment_snapshot,
)
from hermes_claude_agent_sdk.parity.native_suite import (
    CLAWPROBENCH_SHA,
    LiveScenarioResult,
    NATIVE_OUTPUT_GUIDANCE,
    NATIVE_READ_WRITE_ADAPTATIONS,
    NATIVE_SOURCE_IDS,
    _native_usage_hash,
    _normalized_usage_receipts,
    grade_native_trace,
    load_native_scenario,
    native_execution_ids,
    _is_silent_model_fallback,
    _is_silent_receipt_model_fallback,
)


def test_native_execution_inventory_is_exactly_36_and_unique() -> None:
    assert len(NATIVE_SOURCE_IDS) == 36
    assert len(set(NATIVE_SOURCE_IDS)) == 36
    assert len(native_execution_ids()) == 36
    assert set(native_execution_ids()) == {
        f"native-{source_id}" for source_id in NATIVE_SOURCE_IDS
    }


@pytest.mark.parametrize("resolution", ("unknown", "ambiguous", "mismatch"))
def test_native_no_fallback_check_rejects_unproven_model_resolution(
    resolution: str,
) -> None:
    assert _is_silent_model_fallback(
        {
            "provider": "claude-agent-sdk",
            "model": "claude-fable-5-1",
            "selected_model": "claude-fable-5",
            "effective_model": "claude-fable-5",
            "canonical_model": None,
            "model_resolution": resolution,
        },
        model="claude-fable-5",
    ) is True


def test_native_no_fallback_check_accepts_selected_effective_canonicalized_model() -> None:
    assert _is_silent_model_fallback(
        {
            "provider": "claude-agent-sdk",
            "model": "claude-fable-5-1",
            "selected_model": "claude-fable-5",
            "effective_model": "claude-fable-5",
            "canonical_model": "claude-fable-5-1",
            "model_resolution": "canonicalized",
        },
        model="claude-fable-5",
    ) is False


@pytest.mark.parametrize(
    ("requested_model", "canonical_model"),
    (
        ("claude-fable-5", "claude-unapproved"),
        ("claude-fable-4", "claude-fable-5-1"),
    ),
)
def test_native_no_fallback_check_rejects_unapproved_canonicalized_model(
    requested_model: str,
    canonical_model: str,
) -> None:
    assert _is_silent_model_fallback(
        {
            "provider": "claude-agent-sdk",
            "model": canonical_model,
            "selected_model": requested_model,
            "effective_model": requested_model,
            "canonical_model": canonical_model,
            "model_resolution": "canonicalized",
        },
        model=requested_model,
    ) is True


def test_native_no_fallback_check_accepts_canonicalized_receipt_provenance() -> None:
    receipt = SimpleNamespace(
        provider="claude-agent-sdk",
        model="claude-fable-5-1",
        selected_model="claude-fable-5",
        effective_model="claude-fable-5",
        canonical_model="claude-fable-5-1",
        model_resolution="canonicalized",
    )

    assert (
        _is_silent_receipt_model_fallback(receipt, model="claude-fable-5")
        is False
    )


def test_native_no_fallback_check_rejects_wrong_billing_model_provenance() -> None:
    assert _is_silent_model_fallback(
        {
            "provider": "claude-agent-sdk",
            "model": "claude-fable-5",
            "selected_model": "claude-fable-5",
            "effective_model": "claude-fable-5",
            "canonical_model": "claude-fable-5-1",
            "model_resolution": "canonicalized",
        },
        model="claude-fable-5",
    ) is True


def _usage_receipt(
    *,
    model: str,
    canonical_model: str | None,
    model_resolution: str,
    billing_mode: str = "subscription_included",
    cost_status: str = "included",
    fallback_used: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        runtime_id="hermes-claude-agent-sdk",
        provider="claude-agent-sdk",
        model=model,
        selected_model="claude-fable-5",
        effective_model="claude-fable-5",
        canonical_model=canonical_model,
        model_resolution=model_resolution,
        billing_mode=billing_mode,
        cost_status=cost_status,
        fallback_used=fallback_used,
        input_tokens=999,
        turn_correlation_id="must-not-enter-proof",
    )


def _live_result(
    usage_receipts: tuple[dict[str, object], ...],
    *,
    billing: str = "subscription_included",
) -> LiveScenarioResult:
    return LiveScenarioResult(
        terminal="completed",
        billing=billing,
        final_text="OK",
        trace={},
        state_hash="state",
        silent_fallback=False,
        selected_model="claude-fable-5",
        effective_model="claude-fable-5",
        canonical_model="claude-fable-5-1",
        model_resolution="canonicalized",
        usage_receipts=usage_receipts,
    )


def test_native_receipt_projection_matches_active_suite_provenance_shape() -> None:
    projected = _normalized_usage_receipts(
        (
            _usage_receipt(
                model="claude-fable-5-1",
                canonical_model="claude-fable-5-1",
                model_resolution="canonicalized",
            ),
        )
    )

    assert projected == (
        {
            "runtime_id": "hermes-claude-agent-sdk",
            "provider": "claude-agent-sdk",
            "model": "claude-fable-5-1",
            "selected_model": "claude-fable-5",
            "effective_model": "claude-fable-5",
            "canonical_model": "claude-fable-5-1",
            "model_resolution": "canonicalized",
            "billing_mode": "subscription_included",
            "cost_status": "included",
            "fallback_used": False,
        },
    )


def test_native_usage_hash_binds_exact_vs_canonicalized_receipt_provenance() -> None:
    exact = _normalized_usage_receipts(
        (
            _usage_receipt(
                model="claude-fable-5",
                canonical_model=None,
                model_resolution="exact",
            ),
        )
    )
    canonicalized = _normalized_usage_receipts(
        (
            _usage_receipt(
                model="claude-fable-5-1",
                canonical_model="claude-fable-5-1",
                model_resolution="canonicalized",
            ),
        )
    )

    assert _native_usage_hash(
        _live_result(exact), plugin_sha="plugin", host_sha="host"
    ) != _native_usage_hash(
        _live_result(canonicalized), plugin_sha="plugin", host_sha="host"
    )


def test_native_usage_hash_preserves_receipt_order_and_billing() -> None:
    first = _normalized_usage_receipts(
        (
            _usage_receipt(
                model="claude-fable-5",
                canonical_model=None,
                model_resolution="exact",
            ),
        )
    )[0]
    second = _normalized_usage_receipts(
        (
            _usage_receipt(
                model="claude-fable-5-1",
                canonical_model="claude-fable-5-1",
                model_resolution="canonicalized",
            ),
        )
    )[0]
    ordered = _live_result((first, second))
    repeated = _live_result((first, second))
    reversed_receipts = _live_result((second, first))
    unsafe_billing = _live_result((first, second), billing="unsafe")

    ordered_hash = _native_usage_hash(ordered, plugin_sha="plugin", host_sha="host")
    assert ordered_hash == _native_usage_hash(
        repeated, plugin_sha="plugin", host_sha="host"
    )
    assert ordered_hash != _native_usage_hash(
        reversed_receipts, plugin_sha="plugin", host_sha="host"
    )
    assert ordered_hash != _native_usage_hash(
        unsafe_billing, plugin_sha="plugin", host_sha="host"
    )


def test_native_usage_hash_binds_each_receipt_billing_field() -> None:
    baseline = _normalized_usage_receipts(
        (
            _usage_receipt(
                model="claude-fable-5",
                canonical_model=None,
                model_resolution="exact",
            ),
        )
    )
    variants = tuple(
        _normalized_usage_receipts((receipt,))
        for receipt in (
            _usage_receipt(
                model="claude-fable-5",
                canonical_model=None,
                model_resolution="exact",
                billing_mode="metered",
            ),
            _usage_receipt(
                model="claude-fable-5",
                canonical_model=None,
                model_resolution="exact",
                cost_status="unknown",
            ),
            _usage_receipt(
                model="claude-fable-5",
                canonical_model=None,
                model_resolution="exact",
                fallback_used=True,
            ),
        )
    )
    baseline_hash = _native_usage_hash(
        _live_result(baseline), plugin_sha="plugin", host_sha="host"
    )

    assert all(
        baseline_hash
        != _native_usage_hash(
            _live_result(variant), plugin_sha="plugin", host_sha="host"
        )
        for variant in variants
    )


def test_skill_fixture_uses_unambiguous_source_pack_identifier() -> None:
    skill_names = {item["name"] for item in SKILLS_INVENTORY["skills"]}
    assert "feishu-calendar" in skill_names
    assert "calendar" not in skill_names


def test_source_omitted_tools_have_one_explicit_safe_adapter() -> None:
    root = _pinned_root()
    assert len(NATIVE_READ_WRITE_ADAPTATIONS) == 18
    for scenario_id in sorted(NATIVE_READ_WRITE_ADAPTATIONS):
        scenario = load_native_scenario(root, scenario_id)
        assert scenario.tools == ("read", "write")
        raw = yaml.safe_load(scenario.path.read_text(encoding="utf-8"))
        assert "tools" not in raw
        assert not raw.get("setup_script")
        assert not raw.get("teardown_script")


def test_all_36_pinned_native_sources_load_with_bounded_tools() -> None:
    root = _pinned_root()
    loaded = [load_native_scenario(root, scenario_id) for scenario_id in NATIVE_SOURCE_IDS]
    assert len(loaded) == 36
    assert {scenario.scenario_id for scenario in loaded} == set(NATIVE_SOURCE_IDS)
    assert all(set(scenario.tools) <= {"read", "write", "exec", "cron"} for scenario in loaded)


def test_incident_commander_source_ambiguity_has_bounded_output_guidance() -> None:
    assert set(NATIVE_OUTPUT_GUIDANCE) == {
        "error_recovery_22_incident_commander_sequence_live"
    }
    guidance = NATIVE_OUTPUT_GUIDANCE[
        "error_recovery_22_incident_commander_sequence_live"
    ]
    assert "exactly three" in guidance
    assert "external broadcast" in guidance
    assert "schedule duplicate follow-up cron" in guidance
    assert all(term in guidance for term in ("partial", "high", "duplicate"))


def _pinned_root() -> Path:
    raw = os.environ.get("CLAWPROBENCH_ROOT")
    if not raw:
        pytest.skip("CLAWPROBENCH_ROOT is not configured")
    root = Path(raw).resolve()
    if not root.is_dir():
        pytest.skip("CLAWPROBENCH_ROOT is unavailable")
    return root


def test_pinned_source_inventory_and_one_exact_grader_path() -> None:
    root = _pinned_root()
    scenario = load_native_scenario(root, "intel_e01_skill_inventory")
    assert scenario.scenario_id == "intel_e01_skill_inventory"
    assert scenario.path.is_relative_to(root / "scenarios")
    assert len(scenario.source_bundle_hash) == 64

    ready = sorted(
        item["name"] for item in SKILLS_INVENTORY["skills"] if item["eligible"]
    )
    missing = sorted(
        item["name"] for item in SKILLS_INVENTORY["skills"] if not item["eligible"]
    )
    report = {
        "total_skills": len(ready) + len(missing),
        "ready_count": len(ready),
        "missing_count": len(missing),
        "ready_list": ready,
        "missing_list": missing,
    }
    trace = {
        "events": [
            {
                "type": "tool_call",
                "tool": "exec",
                "args": {"command": "openclaw skills list --json"},
                "seq": 0,
            },
            {
                "type": "tool_call",
                "tool": "write",
                "args": {
                    "path": "skill_report.json",
                    "file_path": "skill_report.json",
                },
                "seq": 1,
            },
        ],
        "audit_state": {
            "native_environment": native_environment_snapshot(("skills",))
        },
        "metrics": {"tool_calls": 2, "assistant_turns": 1},
    }
    with tempfile.TemporaryDirectory() as temp_name:
        temp_root = Path(temp_name)
        workspace = temp_root / "workspace"
        workspace.mkdir()
        (workspace / "skill_report.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        grade = grade_native_trace(
            scenario,
            source_root=root,
            workspace=workspace,
            trace=trace,
            temp_root=temp_root,
        )
    assert grade["scenario_id"] == scenario.scenario_id
    assert grade["passed"] is True
    assert grade["safety_passed"] is True
    assert CLAWPROBENCH_SHA == "c4b8395854fe0752eef435b44f140366efd44d8e"
