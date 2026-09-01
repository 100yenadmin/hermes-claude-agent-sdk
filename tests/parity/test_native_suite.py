from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from hermes_claude_agent_sdk.parity.native_sandbox import (
    SKILLS_INVENTORY,
    native_environment_snapshot,
)
from hermes_claude_agent_sdk.parity.native_suite import (
    CLAWPROBENCH_SHA,
    NATIVE_OUTPUT_GUIDANCE,
    NATIVE_READ_WRITE_ADAPTATIONS,
    NATIVE_SOURCE_IDS,
    LiveScenarioResult,
    NativeScenario,
    _live_pregrade_failure,
    _run_live_turn,
    grade_native_trace,
    load_native_scenario,
    native_execution_ids,
    native_scenario_suite,
)
from hermes_claude_agent_sdk.parity.results import ExecutionClassification
from hermes_claude_agent_sdk.parity.runner import ExecutionContext


def test_native_execution_inventory_is_exactly_36_and_unique() -> None:
    assert len(NATIVE_SOURCE_IDS) == 36
    assert len(set(NATIVE_SOURCE_IDS)) == 36
    assert len(native_execution_ids()) == 36
    assert set(native_execution_ids()) == {
        f"native-{source_id}" for source_id in NATIVE_SOURCE_IDS
    }


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


def test_ambiguous_native_sources_have_bounded_output_guidance() -> None:
    assert set(NATIVE_OUTPUT_GUIDANCE) == {
        "error_recovery_22_incident_commander_sequence_live",
        "planning_19_agent_delegation_boundary_live",
    }
    assert "required_local_context" in NATIVE_OUTPUT_GUIDANCE[
        "planning_19_agent_delegation_boundary_live"
    ]
    incident_guidance = NATIVE_OUTPUT_GUIDANCE[
        "error_recovery_22_incident_commander_sequence_live"
    ]
    for disclosed_answer in (
        "record partial recovery from browser status",
        "avoid overloaded session and start fresh commander thread",
        "review existing cron checks before scheduling anything new",
        "external broadcast",
        "schedule duplicate follow-up cron",
    ):
        assert disclosed_answer not in incident_guidance


def test_live_pregrade_gate_accepts_only_complete_subscription_execution(
    tmp_path: Path,
) -> None:
    from hermes_claude_agent_sdk.parity.native_sandbox import NativeSandboxHost

    host = NativeSandboxHost(tmp_path, (), deny_first=False)
    host.denial_observed = True
    host.recovery_observed = True
    host.successful_calls = 1
    live = LiveScenarioResult(
        terminal="completed",
        billing="subscription_included",
        final_text="",
        trace={},
        state_hash="0" * 64,
        silent_fallback=False,
    )
    assert _live_pregrade_failure(live, host, turn_count=1) is None

    unsafe = LiveScenarioResult(
        terminal="completed",
        billing="unsafe",
        final_text="",
        trace={},
        state_hash="0" * 64,
        silent_fallback=False,
    )
    failure = _live_pregrade_failure(unsafe, host, turn_count=1)
    assert failure is not None
    assert {item.reason_code for item in failure.outcomes.values()} == {"unsafe_billing"}


def test_repair_turn_keeps_schema_stable_and_exposes_only_check_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent.runtime_api import RuntimeCompletedEvent, RuntimeUsageEvent, RuntimeUsageReceipt
    from hermes_claude_agent_sdk.parity.native_sandbox import NativeSandboxHost

    class FakeRuntime:
        def __init__(self) -> None:
            self.requests: list[Any] = []

        async def run_turn(self, request: Any, host: Any):
            del host
            self.requests.append(request)
            yield RuntimeUsageEvent(
                receipt=RuntimeUsageReceipt(
                    runtime_id="claude-agent-sdk",
                    provider="claude-agent-sdk",
                    model="claude-fable-5",
                    billing_mode="subscription_included",
                    cost_status="included",
                )
            )
            yield RuntimeCompletedEvent(
                result={
                    "text": "",
                    "provider": "claude-agent-sdk",
                    "model": "claude-fable-5",
                }
            )

    monkeypatch.setenv("HERMES_PARITY_MODEL", "claude-fable-5")
    scenario = load_native_scenario(
        _pinned_root(), "planning_19_agent_delegation_boundary_live"
    )
    scenario = replace(scenario, tools=("read", "write", "cron"))
    host = NativeSandboxHost(tmp_path, (), deny_first=False)
    runtime = FakeRuntime()
    import asyncio

    result = asyncio.run(
        _run_live_turn(
            scenario,
            workspace=tmp_path,
            host=host,
            runtime=runtime,
            turn_index=2,
            repair_check_ids=("selected_agent_is_correct",),
        )
    )
    assert result.billing == "subscription_included"
    assert len(runtime.requests) == 1
    request = runtime.requests[0]
    prompt = request.messages[0]["content"]
    assert "selected_agent_is_correct" in prompt
    assert "required_local_context" in prompt
    assert "detail_hash" not in prompt
    assert "same declared tools" in prompt
    assert "same read/write tools" not in prompt
    assert request.correlation_id.endswith("turn-2")


def _native_context(catalog, capability_id: str) -> ExecutionContext:
    return ExecutionContext(
        capability=catalog.by_id[capability_id],
        path="positive",
        trial_index=1,
        profile_id="fable-v3-isolated",
        profile_hash="3" * 64,
        plugin_sha="1" * 40,
        host_sha="2" * 40,
        sdk_version="0.2.144",
        runner_version="3.0.0",
        inventory_hash="4" * 64,
        contract_hash=catalog.contract_hash,
        catalog_hash=catalog.catalog_hash,
        remaining_turn_budget=180,
        repo_root="/synthetic/repo",
    )


def _synthetic_native_scenario() -> NativeScenario:
    return NativeScenario(
        scenario_id="intel_e01_skill_inventory",
        path=Path("/synthetic/scenario.yaml"),
        prompt="synthetic prompt",
        tools=("read",),
        surfaces=(),
        custom_check=Path("/synthetic/check.py"),
        seed_dir=None,
        source_bundle_hash="5" * 64,
        fixture_hash="6" * 64,
    )


def _admit_synthetic_native_executor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from hermes_claude_agent_sdk.parity import native_suite

    host_root = tmp_path / "host"
    source_root = tmp_path / "source"
    host_root.mkdir()
    source_root.mkdir()
    monkeypatch.setenv("HERMES_PARITY_LIVE", "1")
    monkeypatch.setenv("HERMES_AGENT_HOST_ROOT", str(host_root))
    monkeypatch.setenv("CLAWPROBENCH_ROOT", str(source_root))
    monkeypatch.setattr(native_suite, "_exact_source_preflight", lambda *_: None)
    monkeypatch.setattr(native_suite, "_exact_git_checkout", lambda *_: True)
    monkeypatch.setattr(native_suite, "load_native_scenario", lambda *_: _synthetic_native_scenario())
    monkeypatch.setattr(native_suite, "_inventory_matches", lambda *_: True)


def test_fixture_copy_failure_returns_a_structured_failure(
    catalog,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from hermes_claude_agent_sdk.parity import native_suite

    _admit_synthetic_native_executor(monkeypatch, tmp_path)
    monkeypatch.setattr(
        native_suite,
        "_copy_seed",
        lambda *_: (_ for _ in ()).throw(OSError("synthetic fixture failure")),
    )

    result = __import__("asyncio").run(
        native_scenario_suite(
            _native_context(catalog, "native:intel_e01_skill_inventory")
        )
    )

    assert result.turn_count == 0
    assert all(
        outcome.classification is ExecutionClassification.VERIFIED_FAILURE
        for outcome in result.outcomes.values()
    )
    assert {
        outcome.reason_code for outcome in result.outcomes.values()
    } == {"native_fixture_staging_failed"}


def test_repair_exception_never_reuses_the_prior_turn_billing(
    catalog,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from hermes_claude_agent_sdk import runtime as runtime_module
    from hermes_claude_agent_sdk.parity import native_suite

    class FakeRuntime:
        def __init__(self, **_: Any) -> None:
            pass

        async def close(self) -> None:
            pass

    _admit_synthetic_native_executor(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_module, "ClaudeAgentSDKRuntime", FakeRuntime)
    monkeypatch.setattr(native_suite, "_copy_seed", lambda *_: ())
    first = LiveScenarioResult(
        terminal="completed",
        billing="subscription_included",
        final_text="",
        trace={},
        state_hash="7" * 64,
        silent_fallback=False,
    )
    calls = 0

    async def run_turn(*_args: Any, **_kwargs: Any) -> LiveScenarioResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            return first
        raise ValueError("synthetic repair failure")

    monkeypatch.setattr(native_suite, "_run_live_turn", run_turn)
    monkeypatch.setattr(native_suite, "_live_pregrade_failure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        native_suite,
        "grade_native_trace",
        lambda *_args, **_kwargs: {
            "passed": False,
            "safety_passed": True,
            "checks": [{"check_id": "synthetic-check", "passed": False}],
        },
    )

    result = __import__("asyncio").run(
        native_scenario_suite(
            _native_context(catalog, "native:intel_e01_skill_inventory")
        )
    )

    assert result.turn_count == 2
    assert all(
        outcome.classification is ExecutionClassification.VERIFIED_FAILURE
        for outcome in result.outcomes.values()
    )
    assert {outcome.billing_classification for outcome in result.outcomes.values()} == {
        "none"
    }


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
