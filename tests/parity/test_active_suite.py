from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_claude_agent_sdk.parity import active_suite as active_suite_module
from hermes_claude_agent_sdk.parity.active_suite import (
    ACTIVE_SOURCE_IDS,
    LiveTurn,
    _normalize_event_tool_name,
    _inventory_matches,
    _is_silent_model_fallback,
    _is_silent_receipt_model_fallback,
    _source_docs_contract,
    active_agentic_suite,
    active_execution_ids,
)
from hermes_claude_agent_sdk.parity.executors import EXECUTORS
from hermes_claude_agent_sdk.parity.native_sandbox import NativeSandboxHost, tool_schemas
from hermes_claude_agent_sdk.parity.hashing import sha256_value
from hermes_claude_agent_sdk.parity.results import ExecutionClassification
from hermes_claude_agent_sdk.parity.runner import ExecutionContext
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


def test_delegation_prompt_is_not_defined_in_the_direct_suite() -> None:
    assert not hasattr(active_suite_module, "_FANOUT_DELEGATION_PROMPT")


def test_focused_environment_replaces_ambient_pythonpath(
    monkeypatch, tmp_path
) -> None:
    plugin_root = tmp_path / "plugin"
    host_root = tmp_path / "host"
    monkeypatch.setenv("PYTHONPATH", "/untrusted/ambient/path")
    monkeypatch.setenv("LANG", "en_US.UTF-8")

    environment = active_suite_module._safe_environment(
        plugin_root=plugin_root,
        host_root=host_root,
    )

    assert environment["PYTHONPATH"] == os.pathsep.join(
        (str(plugin_root / "src"), str(host_root))
    )
    assert "/untrusted/ambient/path" not in environment["PYTHONPATH"]
    assert environment["LANG"] == "en_US.UTF-8"


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
    assert _normalize_event_tool_name("read") == "read"
    assert (
        _normalize_event_tool_name("mcp__hermes-tools__mcp__server__tool")
        == "mcp__server__tool"
    )


def test_active_trace_uses_host_receipts_without_sdk_tool_observations(
    tmp_path,
) -> None:
    host = NativeSandboxHost(tmp_path, (), deny_first=False)

    async def scenario() -> None:
        await host.execute_tool("read", {"path": "fixture.txt"})

    trace_start = len(host.trace_events)
    asyncio.run(scenario())
    host_names = active_suite_module._host_tool_names_from_receipts(host, trace_start)

    assert host_names == ("read",)
    assert all(type(name) is str for name in host_names)
    assert not hasattr(active_suite_module, "_agent_tool_names_from_observations")


@pytest.mark.parametrize("resolution", ("unknown", "ambiguous", "mismatch"))
def test_active_no_fallback_check_rejects_unproven_model_resolution(
    resolution: str,
) -> None:
    assert _is_silent_model_fallback(
        {
            "provider": "anthropic",
            "model": "claude-fable-5-1",
            "selected_model": "claude-fable-5",
            "effective_model": "claude-fable-5",
            "canonical_model": None,
            "model_resolution": resolution,
        },
        model="claude-fable-5",
    ) is True


def test_active_no_fallback_check_accepts_selected_effective_canonicalized_model() -> None:
    assert _is_silent_model_fallback(
        {
            "provider": "anthropic",
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
def test_active_no_fallback_check_rejects_unapproved_canonicalized_model(
    requested_model: str,
    canonical_model: str,
) -> None:
    assert _is_silent_model_fallback(
        {
            "provider": "anthropic",
            "model": canonical_model,
            "selected_model": requested_model,
            "effective_model": requested_model,
            "canonical_model": canonical_model,
            "model_resolution": "canonicalized",
        },
        model=requested_model,
    ) is True


def test_active_no_fallback_check_accepts_canonicalized_receipt_provenance() -> None:
    receipt = SimpleNamespace(
        provider="anthropic",
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


def test_active_no_fallback_check_rejects_wrong_billing_model_provenance() -> None:
    assert _is_silent_model_fallback(
        {
            "provider": "anthropic",
            "model": "claude-fable-5",
            "selected_model": "claude-fable-5",
            "effective_model": "claude-fable-5",
            "canonical_model": "claude-fable-5-1",
            "model_resolution": "canonicalized",
        },
        model="claude-fable-5",
    ) is True


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


def test_live_case_auth_rejection_is_environment_blocked_without_billing(
    monkeypatch, tmp_path
) -> None:
    async def rejected_turn(*_args, **_kwargs) -> LiveTurn:
        return LiveTurn(
            terminal="failed",
            failure_code="claude_subscription_auth_rejected",
            billing="none",
            final_text="",
            final_hash=sha256_value(""),
            state=None,
            state_hash="a" * 64,
            tool_names=(),
            compaction_phases=(),
            event_hash="b" * 64,
            silent_fallback=False,
        )

    monkeypatch.setattr(active_suite_module, "_run_turn", rejected_turn)

    result = asyncio.run(
        active_suite_module._run_live_case(
            "instruction-followthrough-repo-contract",
            workspace=tmp_path,
            model="claude-fable-5",
        )
    )

    assert result.classification is ExecutionClassification.ENVIRONMENT_BLOCKED
    assert result.reason_code == "active_subscription_auth_unavailable"
    assert result.billing == "none"
    assert result.turn_count == 0
    assert result.evidence_hash is not None
    assert result.state_hash is not None


def test_live_case_auth_rejection_aggregates_multi_host_paths(
    monkeypatch, tmp_path
) -> None:
    async def rejected_turn(*_args, **_kwargs) -> LiveTurn:
        return LiveTurn(
            terminal="failed",
            failure_code="claude_subscription_auth_rejected",
            billing="none",
            final_text="",
            final_hash=sha256_value(""),
            state=None,
            state_hash="a" * 64,
            tool_names=(),
            compaction_phases=(),
            event_hash="b" * 64,
            silent_fallback=False,
        )

    monkeypatch.setattr(active_suite_module, "_run_turn", rejected_turn)

    for source_id in (
        "thread-memory-isolation",
        "config-restart-capability-flip",
    ):
        workspace = tmp_path / source_id
        workspace.mkdir()
        result = asyncio.run(
            active_suite_module._run_live_case(
                source_id,
                workspace=workspace,
                model="claude-fable-5",
            )
        )
        assert result.classification is ExecutionClassification.ENVIRONMENT_BLOCKED
        assert result.reason_code == "active_subscription_auth_unavailable"
        assert result.billing == "none"
        assert result.turn_count == 0


def test_live_case_pre_usage_product_failure_is_not_subscription_labeled(
    monkeypatch, tmp_path
) -> None:
    async def failed_turn(*_args, **_kwargs) -> LiveTurn:
        return LiveTurn(
            terminal="failed",
            failure_code="claude_runtime_failed",
            billing="none",
            final_text="",
            final_hash=sha256_value(""),
            state=None,
            state_hash="a" * 64,
            tool_names=(),
            compaction_phases=(),
            event_hash="b" * 64,
            silent_fallback=False,
        )

    monkeypatch.setattr(active_suite_module, "_run_turn", failed_turn)

    result = asyncio.run(
        active_suite_module._run_live_case(
            "instruction-followthrough-repo-contract",
            workspace=tmp_path,
            model="claude-fable-5",
        )
    )

    assert result.classification is ExecutionClassification.VERIFIED_FAILURE
    assert result.reason_code == "active_behavior_or_trace_failed"
    assert result.billing == "none"
    assert result.turn_count == 0


def test_live_case_auth_code_with_tool_evidence_remains_verified_failure(
    monkeypatch, tmp_path
) -> None:
    async def rejected_after_tool(*_args, **_kwargs) -> LiveTurn:
        return LiveTurn(
            terminal="failed",
            failure_code="claude_subscription_auth_rejected",
            billing="none",
            final_text="",
            final_hash=sha256_value(""),
            state=None,
            state_hash="a" * 64,
            tool_names=("read",),
            compaction_phases=(),
            event_hash="b" * 64,
            silent_fallback=False,
        )

    monkeypatch.setattr(active_suite_module, "_run_turn", rejected_after_tool)

    result = asyncio.run(
        active_suite_module._run_live_case(
            "instruction-followthrough-repo-contract",
            workspace=tmp_path,
            model="claude-fable-5",
        )
    )

    assert result.classification is ExecutionClassification.VERIFIED_FAILURE
    assert result.billing == "none"
    assert result.turn_count == 0


def test_live_image_expected_denial_and_subscription_recovery_remain_complete(
    monkeypatch, tmp_path
) -> None:
    turns = iter(
        (
            LiveTurn(
                terminal="failed",
                failure_code="claude_runtime_image_invalid",
                billing="none",
                final_text="",
                final_hash=sha256_value(""),
                state=None,
                state_hash="a" * 64,
                tool_names=(),
                compaction_phases=(),
                event_hash="b" * 64,
                silent_fallback=False,
            ),
            LiveTurn(
                terminal="completed",
                failure_code=None,
                billing="subscription_included",
                final_text="BLUE BLUE_IMAGE_PASS",
                final_hash=sha256_value("BLUE BLUE_IMAGE_PASS"),
                state=None,
                state_hash="c" * 64,
                tool_names=(),
                compaction_phases=(),
                event_hash="d" * 64,
                silent_fallback=False,
            ),
        )
    )

    async def next_turn(*_args, **_kwargs) -> LiveTurn:
        return next(turns)

    monkeypatch.setattr(active_suite_module, "_run_turn", next_turn)

    result = asyncio.run(
        active_suite_module._run_live_case(
            "image-understanding-attachment",
            workspace=tmp_path,
            model="claude-fable-5",
        )
    )

    assert result.classification is ExecutionClassification.COMPLETE
    assert result.reason_code is None
    assert result.billing == "subscription_included"
    assert result.turn_count == 1


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


def test_source_docs_contract_reports_missing_tool_evidence_before_marker_gap(
    tmp_path,
) -> None:
    def turn(text: str) -> LiveTurn:
        return LiveTurn(
            terminal="completed",
            failure_code=None,
            billing="subscription_included",
            final_text=text,
            final_hash=sha256_value(text),
            state=None,
            state_hash="a" * 64,
            tool_names=(),
            compaction_phases=(),
            event_hash="b" * 64,
            silent_fallback=False,
        )

    host = NativeSandboxHost(tmp_path, (), deny_first=False)
    source = turn("")
    docs = turn("")

    ok, reason, extra = _source_docs_contract(source, docs, host)

    assert ok is False
    assert reason == "active_source_docs_tool_trace_incomplete"
    assert extra["source_stage_ok"] is False
    assert extra["docs_stage_ok"] is False
    assert extra["host_successful_calls"] == 0
    assert extra["projected_read_count"] == 0


@pytest.mark.parametrize(
    ("terminal", "failure_code", "billing", "silent_fallback", "expected_reason"),
    (
        (
            "failed",
            "sdk_stream_failed",
            "subscription_included",
            False,
            "active_source_terminal_transport_failed",
        ),
        (
            "failed",
            "sdk_result_failed",
            "subscription_included",
            False,
            "active_source_terminal_query_failed",
        ),
        (
            "failed",
            "sdk_turn_timeout",
            "subscription_included",
            False,
            "active_source_terminal_timeout",
        ),
        (
            "failed",
            "sdk_api_rate_limit_429",
            "subscription_included",
            False,
            "active_source_terminal_capacity_failed",
        ),
        (
            "failed",
            "claude_subscription_auth_rejected",
            "subscription_included",
            False,
            "active_source_terminal_auth_failed",
        ),
        (
            "failed",
            "sdk_billing_blocked",
            "subscription_included",
            False,
            "active_source_terminal_billing_failed",
        ),
        (
            "failed",
            "claude_runtime_session_contract_changed",
            "subscription_included",
            False,
            "active_source_terminal_contract_failed",
        ),
        (
            "failed",
            "synthetic-unlisted-private-code",
            "subscription_included",
            False,
            "active_source_terminal_unknown_failed",
        ),
        (
            "cancelled",
            None,
            "subscription_included",
            False,
            "active_source_terminal_cancelled_or_interrupted",
        ),
        (
            "invalid",
            None,
            "subscription_included",
            False,
            "active_source_terminal_invalid",
        ),
        (
            "completed",
            None,
            "none",
            False,
            "active_source_billing_mismatch",
        ),
        (
            "completed",
            None,
            "subscription_included",
            True,
            "active_source_silent_fallback",
        ),
    ),
)
def test_source_docs_contract_reports_bounded_terminal_failure_before_other_gaps(
    tmp_path,
    terminal: str,
    failure_code: str | None,
    billing: str,
    silent_fallback: bool,
    expected_reason: str,
) -> None:
    source = LiveTurn(
        terminal=terminal,
        failure_code=failure_code,
        billing=billing,
        final_text="",
        final_hash=sha256_value(""),
        state=None,
        state_hash="a" * 64,
        tool_names=(),
        compaction_phases=(),
        event_hash="b" * 64,
        silent_fallback=silent_fallback,
    )
    docs_text = "SOURCE_QUARTZ_7319 DOCS_EMBER_4826 SOURCE_DOCS_PASS"
    docs = LiveTurn(
        terminal="completed",
        failure_code=None,
        billing="subscription_included",
        final_text=docs_text,
        final_hash=sha256_value(docs_text),
        state=None,
        state_hash="c" * 64,
        tool_names=("read",),
        compaction_phases=(),
        event_hash="d" * 64,
        silent_fallback=False,
    )
    host = NativeSandboxHost(tmp_path, (), deny_first=False)

    ok, reason, extra = _source_docs_contract(source, docs, host)

    assert ok is False
    assert reason == expected_reason
    assert extra["source_stage_ok"] is False


def test_source_docs_contract_reports_docs_terminal_failure_before_tool_gaps(
    tmp_path,
) -> None:
    source_text = "SOURCE_QUARTZ_7319 SOURCE_STAGE_PASS"
    source = LiveTurn(
        terminal="completed",
        failure_code=None,
        billing="subscription_included",
        final_text=source_text,
        final_hash=sha256_value(source_text),
        state=None,
        state_hash="a" * 64,
        tool_names=(),
        compaction_phases=(),
        event_hash="b" * 64,
        silent_fallback=False,
    )
    docs = LiveTurn(
        terminal="failed",
        failure_code="sdk_result_error_during_execution",
        billing="subscription_included",
        final_text="",
        final_hash=sha256_value(""),
        state=None,
        state_hash="c" * 64,
        tool_names=(),
        compaction_phases=(),
        event_hash="d" * 64,
        silent_fallback=False,
    )
    host = NativeSandboxHost(tmp_path, (), deny_first=False)

    ok, reason, extra = _source_docs_contract(source, docs, host)

    assert ok is False
    assert reason == "active_docs_terminal_query_failed"
    assert extra["source_stage_ok"] is True
    assert extra["docs_stage_ok"] is False


@pytest.mark.parametrize(
    ("denial_observed", "recovery_observed", "expected_reason"),
    (
        (False, False, "active_source_docs_denial_missing"),
        (True, False, "active_source_docs_recovery_missing"),
    ),
)
def test_source_docs_contract_reports_denial_and_recovery_before_marker_gaps(
    tmp_path,
    denial_observed: bool,
    recovery_observed: bool,
    expected_reason: str,
) -> None:
    source_text = "SOURCE_QUARTZ_7319 SOURCE_STAGE_PASS"
    docs_text = "SOURCE_QUARTZ_7319 DOCS_EMBER_4826 SOURCE_DOCS_PASS"

    def turn(text: str) -> LiveTurn:
        return LiveTurn(
            terminal="completed",
            failure_code=None,
            billing="subscription_included",
            final_text=text,
            final_hash=sha256_value(text),
            state=None,
            state_hash="a" * 64,
            tool_names=("read",),
            compaction_phases=(),
            event_hash="b" * 64,
            silent_fallback=False,
        )

    host = NativeSandboxHost(tmp_path, (), deny_first=False)
    host.successful_calls = 2
    host.denial_observed = denial_observed
    host.recovery_observed = recovery_observed

    ok, reason, extra = _source_docs_contract(
        turn(source_text),
        turn(docs_text),
        host,
    )

    assert ok is False
    assert reason == expected_reason
    assert extra["source_stage_ok"] is True
    assert extra["docs_stage_ok"] is True


def test_source_docs_contract_reports_marker_gap_after_host_tool_recovery(
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
            event_hash="b" * 64,
            silent_fallback=False,
        )

    host = NativeSandboxHost(tmp_path, (), deny_first=False)
    host.denial_observed = True
    host.recovery_observed = True
    host.successful_calls = 2
    source = turn("synthetic response without the required markers", ("read",))
    docs = turn(
        "SOURCE_QUARTZ_7319 DOCS_EMBER_4826 SOURCE_DOCS_PASS",
        ("read",),
    )

    ok, reason, extra = _source_docs_contract(source, docs, host)

    assert ok is False
    assert reason == "active_source_stage_marker_missing"
    assert extra["source_stage_ok"] is False
    assert extra["docs_stage_ok"] is True


def test_subagent_contract_is_not_defined_in_the_direct_suite() -> None:
    assert not hasattr(active_suite_module, "_subagent_contract")


def test_delegation_rows_fail_closed_before_any_runtime_preflight(
    catalog, candidate_fields, monkeypatch
) -> None:
    def unexpected(*_args, **_kwargs):
        raise AssertionError("delegation evidence gate must precede runtime construction")

    monkeypatch.setattr(active_suite_module, "_exact_source_preflight", unexpected)
    monkeypatch.setattr(active_suite_module, "_run_live_case_bounded", unexpected)

    for source_id in (
        "subagent-handoff",
        "subagent-fanout-synthesis",
    ):
        capability = catalog.by_id[f"active:{source_id}"]
        context = ExecutionContext(
            capability=capability,
            path="positive",
            trial_index=1,
            profile_id=candidate_fields["profile_id"],
            profile_hash=candidate_fields["profile_hash"],
            plugin_sha=candidate_fields["plugin_sha"],
            host_sha=candidate_fields["host_sha"],
            sdk_version=candidate_fields["sdk_version"],
            runner_version=candidate_fields["runner_version"],
            inventory_hash=candidate_fields["inventory_hash"],
            contract_hash=catalog.contract_hash,
            catalog_hash=catalog.catalog_hash,
            remaining_turn_budget=100,
            repo_root=str(Path(catalog.path).parent.parent),
        )

        bundle = asyncio.run(active_agentic_suite(context))

        assert bundle.turn_count == 0
        assert all(
            outcome.classification is ExecutionClassification.ENVIRONMENT_BLOCKED
            and outcome.reason_code == "installed_hermes_delegate_evidence_required"
            for outcome in bundle.outcomes.values()
        )


def test_stale_child_links_uses_provider_free_hermes_focused_evidence(
    catalog, candidate_fields, monkeypatch, tmp_path
) -> None:
    seen: dict[str, object] = {}

    monkeypatch.setenv("HERMES_AGENT_HOST_ROOT", str(tmp_path))
    monkeypatch.setattr(active_suite_module, "_exact_source_preflight", lambda *_: None)
    monkeypatch.setattr(active_suite_module, "_exact_git_checkout", lambda *_: True)

    def focused(context, nodes, *, plugin_root, host_root):
        seen["context"] = context
        seen["nodes"] = tuple(nodes)
        seen["plugin_root"] = plugin_root
        seen["host_root"] = host_root
        return active_suite_module.ActiveCaseResult(
            ExecutionClassification.COMPLETE,
            None,
            "none",
            0,
            "a" * 64,
            "b" * 64,
        )

    monkeypatch.setattr(active_suite_module, "_run_focused", focused)
    capability = catalog.by_id["active:subagent-stale-child-links"]
    context = ExecutionContext(
        capability=capability,
        path="positive",
        trial_index=1,
        profile_id=candidate_fields["profile_id"],
        profile_hash=candidate_fields["profile_hash"],
        plugin_sha=candidate_fields["plugin_sha"],
        host_sha=candidate_fields["host_sha"],
        sdk_version=candidate_fields["sdk_version"],
        runner_version=candidate_fields["runner_version"],
        inventory_hash=candidate_fields["inventory_hash"],
        contract_hash=catalog.contract_hash,
        catalog_hash=catalog.catalog_hash,
        remaining_turn_budget=100,
        repo_root=str(Path(catalog.path).parent.parent),
    )

    bundle = asyncio.run(active_agentic_suite(context))

    assert all(
        outcome.classification
        in {ExecutionClassification.COMPLETE, ExecutionClassification.EXPECTED_NEGATIVE}
        and outcome.primary_proof_hash
        and outcome.secondary_proof_hash
        for outcome in bundle.outcomes.values()
    ), [
        (
            path,
            outcome.classification.value,
            outcome.reason_code,
            bool(outcome.primary_proof_hash),
            bool(outcome.secondary_proof_hash),
        )
        for path, outcome in bundle.outcomes.items()
    ]
    assert seen["host_root"] == tmp_path
    assert seen["plugin_root"] == Path(catalog.path).parent.parent
    assert seen["nodes"] == active_suite_module._FOCUSED_NODES[
        "subagent-stale-child-links"
    ]
    assert all(
        node.startswith("tests/parity/test_v4_provider_free_delegation_runtime.py::")
        or node
        in {
            "tests/test_sdk_session.py::test_post_terminal_sdk_output_is_a_protocol_failure_without_background_delivery",
            "tests/test_runtime_sdk_integration.py::test_queued_idle_burst_is_released_only_after_parent_terminal_is_observed",
        }
        for node in seen["nodes"]
    )


def test_model_switch_focused_node_tracks_exact_fable_selection_test() -> None:
    assert active_suite_module._FOCUSED_NODES["model-switch-tool-continuity"] == (
        "tests/test_runtime_sdk_integration.py::test_unsupported_model_switch_fails_before_client_or_query",
    )


def test_operational_parity_sources_have_no_native_background_route() -> None:
    source_paths = (
        Path(active_suite_module.__file__),
        Path(active_suite_module.__file__).with_name("runtime_suite.py"),
        Path(active_suite_module.__file__).with_name("native_sandbox.py"),
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)
    for forbidden in (
        "native Agent",
        "native-Agent",
        "run_in_background",
        "emit_background_result",
        "background_hashes",
        "agent_tool_names",
        "_subagent_contract",
        "_FANOUT_DELEGATION_PROMPT",
    ):
        assert forbidden not in source
