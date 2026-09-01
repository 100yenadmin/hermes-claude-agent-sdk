from __future__ import annotations

import asyncio

from hermes_claude_agent_sdk.parity import active_suite as active_suite_module
from hermes_claude_agent_sdk.parity.active_suite import (
    ACTIVE_SOURCE_IDS,
    active_execution_ids,
)
from hermes_claude_agent_sdk.parity.executors import EXECUTORS
from hermes_claude_agent_sdk.parity.native_sandbox import NativeSandboxHost
from hermes_claude_agent_sdk.parity.hashing import sha256_value
from hermes_claude_agent_sdk.parity.results import ExecutionClassification
from hermes_claude_agent_sdk.parity.trace import normalized_path_events


def test_active_execution_inventory_is_exactly_eleven_plus_thin_approval() -> None:
    assert len(ACTIVE_SOURCE_IDS) == 11
    assert len(set(ACTIVE_SOURCE_IDS)) == 11
    assert set(active_execution_ids()) == {
        f"active-{source_id}" for source_id in ACTIVE_SOURCE_IDS
    }
    assert {
        "active-approval-turn-tool-followthrough",
        *active_execution_ids(),
    } <= set(EXECUTORS)


def test_active_normalized_events_preserve_catalog_order_for_every_path(catalog) -> None:
    evidence_hash = sha256_value("synthetic-active-evidence")
    for capability in catalog.for_lane("rc"):
        if capability.source_pack != "openclaw_active":
            continue
        if capability.source_item_id == "approval-turn-tool-followthrough":
            continue
        for path in ("positive", "denial", "recovery"):
            events = normalized_path_events(
                capability.expected_trace,
                path=path,
                evidence_hash=evidence_hash,
            )
            assert tuple(event["kind"] for event in events) == capability.expected_trace
            assert events[-1]["terminal_outcome"] == (
                "denied" if path == "denial" else "completed"
            )


def test_native_sandbox_can_disable_injected_denial(tmp_path) -> None:
    fixture = tmp_path / "fixture.txt"
    fixture.write_text("safe", encoding="utf-8")
    host = NativeSandboxHost(tmp_path, (fixture,), deny_first=False)

    result = asyncio.run(host.execute_tool("read", {"path": "fixture.txt"}))

    assert result == "safe"
    assert host.denial_observed is False
    assert host.successful_calls == 1


def test_live_case_timeout_is_environment_blocked_and_cancels(
    monkeypatch, tmp_path
) -> None:
    cleaned = False

    async def never_finishes(_source_id, *, workspace, model):
        nonlocal cleaned
        assert workspace == tmp_path
        assert model == "claude-fable-5"
        try:
            await asyncio.Event().wait()
        finally:
            cleaned = True

    monkeypatch.setattr(active_suite_module, "_run_live_case", never_finishes)
    monkeypatch.setattr(active_suite_module, "_ACTIVE_CASE_TIMEOUT_SECONDS", 0.01)

    result = asyncio.run(
        active_suite_module._run_live_case_bounded(
            "thread-memory-isolation",
            workspace=tmp_path,
            model="claude-fable-5",
        )
    )

    assert result.classification is ExecutionClassification.ENVIRONMENT_BLOCKED
    assert result.reason_code == "active_live_case_timeout"
    assert result.billing == "none"
    assert result.turn_count == 0
    assert cleaned is True
