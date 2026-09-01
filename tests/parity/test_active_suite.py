from __future__ import annotations

import asyncio
from types import SimpleNamespace

from hermes_claude_agent_sdk.parity import active_suite as active_suite_module
from hermes_claude_agent_sdk.parity.active_suite import (
    ACTIVE_SOURCE_IDS,
    LiveTurn,
    _normalize_event_tool_name,
    _inventory_matches,
    _source_docs_contract,
    _subagent_contract,
    active_execution_ids,
)
from hermes_claude_agent_sdk.parity.executors import EXECUTORS
from hermes_claude_agent_sdk.parity.native_sandbox import NativeSandboxHost, tool_schemas
from hermes_claude_agent_sdk.parity.hashing import sha256_value
from hermes_claude_agent_sdk.parity.results import ExecutionClassification
from hermes_claude_agent_sdk.parity.tool_inventory import declared_tool_schemas
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


def test_event_tool_names_normalize_one_provider_namespace_only() -> None:
    assert _normalize_event_tool_name("mcp__hermes-tools__read") == "read"
    assert _normalize_event_tool_name("Agent") == "Agent"
    assert (
        _normalize_event_tool_name("mcp__hermes-tools__mcp__server__tool")
        == "mcp__server__tool"
    )


def test_inventory_matches_requires_an_exact_unique_well_formed_inventory() -> None:
    complete_schemas = declared_tool_schemas()
    complete_rows = tuple(
        {
            "name": schema["function"]["name"],
            "schema_hash": sha256_value(schema["function"]["parameters"]),
        }
        for schema in complete_schemas
    )
    read_schema = tool_schemas(("read",))[0]

    def matches(rows, requested=(read_schema,)) -> bool:
        return _inventory_matches(
            SimpleNamespace(inventory_tools=tuple(rows)), requested
        )

    assert matches(complete_rows) is True
    assert matches(complete_rows, requested=()) is True
    assert matches(
        (*complete_rows, {"name": "extra", "schema_hash": "a" * 64})
    ) is False
    assert matches(()) is False
    assert matches(complete_rows[:-1]) is False
    assert matches((*complete_rows, complete_rows[0])) is False
    assert matches(({"name": "read"},)) is False
    drifted_rows = (
        {**complete_rows[0], "schema_hash": "b" * 64},
        *complete_rows[1:],
    )
    assert matches(drifted_rows) is False
    assert matches(("malformed",)) is False
    assert matches(complete_rows, requested=(read_schema, read_schema)) is False
    assert matches(
        complete_rows, requested=({"function": {"name": "read"}},)
    ) is False
    assert matches(
        complete_rows,
        requested=(
            {
                "type": "function",
                "function": {
                    "name": "unknown",
                    "parameters": {"type": "object"},
                },
            },
        ),
    ) is False


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


def test_source_docs_contract_uses_host_receipts_when_projection_is_deduplicated(
    tmp_path,
) -> None:
    def turn(text: str, tool_names: tuple[str, ...]) -> LiveTurn:
        return LiveTurn(
            terminal="completed",
            failure_code=None,
            billing="subscription_included",
            final_text=text,
            final_hash=sha256_value(text),
            state=None,
            state_hash="a" * 64,
            tool_names=tool_names,
            compaction_phases=(),
            background_hashes=(),
            event_hash="b" * 64,
            silent_fallback=False,
        )

    host = NativeSandboxHost(tmp_path, (), deny_first=False)
    host.denial_observed = True
    host.recovery_observed = True
    host.successful_calls = 2
    source = turn("SOURCE_QUARTZ_7319 SOURCE_STAGE_PASS", ())
    docs = turn(
        "SOURCE_QUARTZ_7319 DOCS_EMBER_4826 SOURCE_DOCS_PASS",
        ("read",),
    )

    ok, reason, extra = _source_docs_contract(source, docs, host)

    assert ok is True
    assert reason == "active_behavior_or_trace_failed"
    assert extra["projected_read_count"] == 1
    assert extra["host_successful_calls"] == 2


def test_subagent_contract_reports_parent_marker_gap_without_content(tmp_path) -> None:
    host = NativeSandboxHost(tmp_path, (), deny_first=False)
    host.denial_observed = True
    host.recovery_observed = True
    turn = LiveTurn(
        terminal="completed",
        failure_code=None,
        billing="subscription_included",
        final_text="synthetic synthesis without contract markers",
        final_hash=sha256_value("synthetic synthesis without contract markers"),
        state=None,
        state_hash="a" * 64,
        tool_names=("read", "read", "Agent", "Agent"),
        compaction_phases=(),
        background_hashes=(),
        event_hash="b" * 64,
        silent_fallback=False,
    )

    ok, reason, extra = _subagent_contract(
        (turn,),
        host,
        count=2,
        final_marker="FANOUT_PASS",
    )

    assert ok is False
    assert reason == "active_subagent_context_marker_missing"
    assert extra == {
        "agent_calls": 2,
        "denial": True,
        "recovery": True,
        "context_marker": False,
        "final_marker": False,
        "provider_turns": 1,
    }
