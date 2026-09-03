from __future__ import annotations

import asyncio
from dataclasses import fields

import pytest

from agent.runtime_api import (
    RuntimeSelection,
    RuntimeTurnRequest,
    RuntimeUsageReceipt,
)

from hermes_claude_agent_sdk.configuration import SDKSessionConfiguration
from hermes_claude_agent_sdk.tool_bridge import (
    HostToolBridge,
    ToolBridgeRequestError,
)


_SAFE_TOOL = {
    "type": "function",
    "function": {
        "name": "parity_read_only",
        "description": "Return one isolated parity marker",
        "parameters": {
            "type": "object",
            "properties": {"marker": {"type": "string"}},
            "required": ["marker"],
            "additionalProperties": False,
        },
    },
}


class _Host:
    def __init__(self) -> None:
        self.cancelled = False
        self.calls: list[tuple[str, dict[str, object], str | None]] = []

    async def execute_tool(
        self,
        name: str,
        arguments,
        *,
        request_id: str | None = None,
    ):
        self.calls.append((name, dict(arguments), request_id))
        return {"ok": True, "call_count": len(self.calls)}

    def cancellation_requested(self) -> bool:
        return self.cancelled


def _valid_turn_request(**extra):
    values = {
        "selection": RuntimeSelection(
            provider="claude-agent-sdk",
            model="claude-fable-5",
            api_mode="subscription",
        ),
        "messages": (),
        "prompt_snapshot": "stable prompt",
        "tool_schemas": (),
        "tool_schema_hash": "a" * 64,
    }
    values.update(extra)
    return RuntimeTurnRequest(**values)


def test_cache_receipts_and_resume_survive_while_unknown_fork_controls_fail_closed() -> None:
    configuration = SDKSessionConfiguration.create(
        cwd="/synthetic/parity-v3",
        model="claude-fable-5",
        resume_external_session_id="sdk-session-safe",
        parent_env={},
    )
    options = configuration.option_fields()
    receipt_fields = {field.name for field in fields(RuntimeUsageReceipt)}

    assert options["resume"] == "sdk-session-safe"
    assert {"cache_read_tokens", "cache_write_tokens"} <= receipt_fields
    assert {
        "selected_model",
        "effective_model",
        "canonical_model",
        "model_resolution",
    } <= receipt_fields
    assert not {"effort", "checkpoint", "fork"} & set(options)
    with pytest.raises(TypeError):
        _valid_turn_request(effort="high")
    with pytest.raises(TypeError):
        SDKSessionConfiguration.create(
            cwd="/synthetic/parity-v3",
            parent_env={},
            checkpoint="unsafe",
        )

    recovered = SDKSessionConfiguration.create(
        cwd="/synthetic/parity-v3",
        model="claude-fable-5",
        parent_env={},
    )
    assert recovered.option_fields()["model"] == "claude-fable-5"


def test_variadic_sdk_surfaces_are_denied_without_weakening_tool_or_mcp_isolation() -> None:
    mcp_servers = {"hermes-tools": {"type": "sdk"}}
    with pytest.raises(ValueError, match="setting_sources must be empty"):
        SDKSessionConfiguration.create(
            cwd="/synthetic/parity-v4",
            parent_env={},
            setting_sources=("project", "local"),
            mcp_servers=mcp_servers,
            allowed_tools=("mcp__hermes-tools__parity_read_only",),
        )
    configuration = SDKSessionConfiguration.create(
        cwd="/synthetic/parity-v4",
        parent_env={},
        mcp_servers=mcp_servers,
        allowed_tools=("mcp__hermes-tools__parity_read_only",),
    )
    mcp_servers["unadmitted"] = {"type": "stdio"}
    options = configuration.option_fields()

    assert options["setting_sources"] == []
    assert options["mcp_servers"] == {"hermes-tools": {"type": "sdk"}}
    assert options["strict_mcp_config"] is True
    assert options["allowed_tools"] == ["mcp__hermes-tools__parity_read_only"]
    assert options["tools"] == []
    with pytest.raises(TypeError):
        SDKSessionConfiguration.create(
            cwd="/synthetic/parity-v3",
            parent_env={},
            additional_directories=("/tmp",),
        )
    with pytest.raises(ValueError, match="duplicates"):
        SDKSessionConfiguration.create(
            cwd="/synthetic/parity-v3",
            parent_env={},
            allowed_tools=(
                "mcp__hermes-tools__parity_read_only",
                "mcp__hermes-tools__parity_read_only",
            ),
        )


def test_unavailable_structured_question_surface_fails_before_host_and_recovers() -> None:
    async def scenario() -> None:
        host = _Host()
        bridge = HostToolBridge(host, [_SAFE_TOOL], correlation_id="question-boundary")

        with pytest.raises(ToolBridgeRequestError) as unknown:
            await bridge.handle_tool_call(
                "question-1",
                "AskUserQuestion",
                {"question": "unavailable"},
            )
        assert unknown.value.code == "unknown"
        with pytest.raises(ToolBridgeRequestError) as malformed:
            await bridge.handle_tool_call(
                "question-2",
                "parity_read_only",
                {"unexpected": "field"},
            )
        assert malformed.value.code == "arguments"
        assert host.calls == []

        recovered = await bridge.handle_tool_call(
            "question-recovery",
            "parity_read_only",
            {"marker": "safe"},
        )
        assert recovered.is_error is False
        assert recovered.correlation_id == "question-boundary"
        assert host.calls == [
            ("parity_read_only", {"marker": "safe"}, "question-recovery")
        ]

    asyncio.run(scenario())


def test_cancelled_or_late_tool_request_is_fenced_then_next_turn_rebinds() -> None:
    async def scenario() -> None:
        host = _Host()
        bridge = HostToolBridge(host, [_SAFE_TOOL], correlation_id="turn-one")
        first = await bridge.handle_tool_call(
            "turn-one-request",
            "parity_read_only",
            {"marker": "first"},
        )
        assert first.correlation_id == "turn-one"

        host.cancelled = True
        with pytest.raises(ToolBridgeRequestError) as late:
            await bridge.handle_tool_call(
                "turn-one-late",
                "parity_read_only",
                {"marker": "late"},
            )
        assert late.value.code == "cancelled"
        assert len(host.calls) == 1

        host.cancelled = False
        bridge.begin_turn("turn-two")
        recovered = await bridge.handle_tool_call(
            "turn-two-request",
            "parity_read_only",
            {"marker": "recovered"},
        )
        assert recovered.correlation_id == "turn-two"
        assert host.calls == [
            ("parity_read_only", {"marker": "first"}, "turn-one-request"),
            ("parity_read_only", {"marker": "recovered"}, "turn-two-request"),
        ]

    asyncio.run(scenario())
