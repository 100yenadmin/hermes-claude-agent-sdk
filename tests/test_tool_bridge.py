"""Fail-closed tests for the SDK-facing host tool bridge."""

from __future__ import annotations

import asyncio
import sys
from types import MappingProxyType, ModuleType, SimpleNamespace
from typing import Any

import pytest

from hermes_claude_agent_sdk.tool_bridge import (
    HostToolBridge,
    ToolBridgeConfigurationError,
    ToolBridgeRequestError,
)


class RecordingHost:
    def __init__(self, result: Any = "ok") -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.cancelled = False
        self.raise_error: BaseException | None = None
        self.cancellation_error: BaseException | None = None

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        if self.raise_error is not None:
            raise self.raise_error
        return self.result

    def cancellation_requested(self) -> bool:
        if self.cancellation_error is not None:
            raise self.cancellation_error
        return self.cancelled


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _openai(name: str = "pwd", parameters: dict[str, Any] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "Synthetic tool",
            "parameters": parameters
            or {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    }


def test_direct_call_delegates_once_and_preserves_correlation_and_name() -> None:
    host = RecordingHost(result={"cwd": "/synthetic"})
    bridge = HostToolBridge(
        host,
        [_openai("mcp__hermes__pwd")],
        correlation_id="turn-synthetic",
    )

    result = _run(
        bridge.handle_tool_call(
            request_id="sdk-call-42",
            name="mcp__hermes__pwd",
            arguments={"path": "."},
        )
    )

    assert host.calls == [("mcp__hermes__pwd", {"path": "."})]
    assert bridge.host_execution_count == 1
    assert result.request_id == "sdk-call-42"
    assert result.correlation_id == "turn-synthetic"
    assert result.tool_name == "mcp__hermes__pwd"
    assert result.is_error is False
    assert result.to_sdk_result() == {
        "content": [{"type": "text", "text": '{"cwd":"/synthetic"}'}],
        "is_error": False,
    }


def test_anthropic_schema_maps_without_stripping_canonical_mcp_prefix() -> None:
    host = RecordingHost()
    bridge = HostToolBridge(
        host,
        [
            {
                "name": "mcp__server__tool",
                "description": "Synthetic MCP tool",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                },
            }
        ],
    )

    assert bridge.tool_names == ("mcp__server__tool",)
    _run(bridge.handle_tool_call("request-1", "mcp__server__tool", {}))
    assert host.calls == [("mcp__server__tool", {})]


def test_runtime_frozen_mappingproxy_schemas_and_arguments_are_supported() -> None:
    schema = MappingProxyType(
        {
            "type": "function",
            "function": MappingProxyType(
                {
                    "name": "pwd",
                    "description": "Synthetic tool",
                    "parameters": MappingProxyType(
                        {
                            "type": "object",
                            "properties": MappingProxyType(
                                {"path": MappingProxyType({"type": "string"})}
                            ),
                            "required": ("path",),
                            "additionalProperties": False,
                        }
                    ),
                }
            ),
        }
    )
    host = RecordingHost()
    bridge = HostToolBridge(host, (schema,))

    _run(
        bridge.handle_tool_call(
            "request",
            "pwd",
            MappingProxyType({"path": "."}),
        )
    )
    assert host.calls == [("pwd", {"path": "."})]


def test_unknown_duplicate_and_excluded_names_fail_before_host_call() -> None:
    host = RecordingHost()
    with pytest.raises(ToolBridgeConfigurationError, match="duplicate"):
        HostToolBridge(host, [_openai("same"), _openai("same")])

    bridge = HostToolBridge(
        host,
        [_openai("allowed"), _openai("excluded")],
        excluded_names={"excluded"},
    )
    for name in ("missing", "excluded"):
        with pytest.raises(ToolBridgeRequestError):
            _run(bridge.handle_tool_call("request", name, {}))
    assert host.calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        {"path": 7},
        {},
        {"path": ".", "extra": True},
    ],
)
def test_malformed_arguments_fail_closed_before_host_call(arguments: Any) -> None:
    host = RecordingHost()
    bridge = HostToolBridge(host, [_openai()])

    with pytest.raises(ToolBridgeRequestError):
        _run(bridge.handle_tool_call("request", "pwd", arguments))
    assert host.calls == []


def test_unsupported_schema_fails_closed_at_construction() -> None:
    host = RecordingHost()
    with pytest.raises(ToolBridgeConfigurationError, match="schema"):
        HostToolBridge(host, [_openai("bad", {"type": "string"})])
    with pytest.raises(ToolBridgeConfigurationError, match="schema"):
        HostToolBridge(
            host,
            [_openai("bad", {"type": "object", "properties": {}, "anyOf": []})],
        )


def test_cancellation_and_cancellation_probe_failure_do_not_call_host() -> None:
    host = RecordingHost()
    host.cancelled = True
    bridge = HostToolBridge(host, [_openai()])
    with pytest.raises(ToolBridgeRequestError, match="cancel"):
        _run(bridge.handle_tool_call("request", "pwd", {"path": "."}))
    assert host.calls == []

    host = RecordingHost()
    host.cancellation_error = RuntimeError("synthetic cancellation probe detail")
    bridge = HostToolBridge(host, [_openai()])
    with pytest.raises(ToolBridgeRequestError, match="cancel"):
        _run(bridge.handle_tool_call("request", "pwd", {"path": "."}))
    assert host.calls == []


def test_host_exception_is_redacted_and_bounded_without_raw_exception_text() -> None:
    host = RecordingHost()
    host.raise_error = RuntimeError("SECRET-CUSTOMER-DETAIL " + "x" * 100_000)
    bridge = HostToolBridge(host, [_openai()])

    result = _run(bridge.handle_tool_call("request", "pwd", {"path": "."}))

    assert result.is_error is True
    sdk_result = result.to_sdk_result()
    text = sdk_result["content"][0]["text"]
    assert text == "Host tool execution failed"
    assert "SECRET-CUSTOMER-DETAIL" not in text
    assert len(text.encode("utf-8")) <= 4096


def test_result_conversion_is_bounded_and_rejects_unsupported_host_values() -> None:
    host = RecordingHost(result={"value": "x" * 100_000})
    bridge = HostToolBridge(host, [_openai()])
    result = _run(bridge.handle_tool_call("request", "pwd", {"path": "."}))
    text = result.to_sdk_result()["content"][0]["text"]
    assert len(text.encode("utf-8")) <= 65536
    assert "truncated" in text

    host = RecordingHost(result=object())
    bridge = HostToolBridge(host, [_openai()])
    result = _run(bridge.handle_tool_call("request", "pwd", {"path": "."}))
    assert result.is_error is True
    assert result.to_sdk_result()["content"][0]["text"] == "Host returned unsupported result"


@pytest.mark.parametrize(
    "host_result",
    ['{"error":"synthetic failure"}', {"error": "synthetic failure"}],
)
def test_canonical_host_error_envelope_is_marked_as_sdk_error(host_result: Any) -> None:
    host = RecordingHost(result=host_result)
    bridge = HostToolBridge(host, [_openai()])
    result = _run(bridge.handle_tool_call("request", "pwd", {"path": "."}))
    assert result.is_error is True


def test_sdk_adapter_is_lazy_and_handler_only_calls_host(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "claude_agent_sdk" not in sys.modules

    registered: list[Any] = []

    class FakeSdkTool:
        def __init__(self, name: str, description: str, schema: dict, handler: Any) -> None:
            self.name = name
            self.description = description
            self.input_schema = schema
            self.handler = handler

    fake_sdk = ModuleType("claude_agent_sdk")

    def fake_tool(name: str, description: str, schema: dict, annotations: Any = None) -> Any:
        def decorate(handler: Any) -> FakeSdkTool:
            tool = FakeSdkTool(name, description, schema, handler)
            registered.append(tool)
            return tool

        return decorate

    def fake_create(name: str, version: str = "1.0.0", tools: list[Any] | None = None) -> Any:
        return {"type": "sdk", "name": name, "tools": tuple(tools or ())}

    fake_sdk.tool = fake_tool  # type: ignore[attr-defined]
    fake_sdk.create_sdk_mcp_server = fake_create  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)

    host = RecordingHost(result="synthetic result")
    bridge = HostToolBridge(host, [_openai("z_tool"), _openai("a_tool")])
    assert registered == []
    server = bridge.build_sdk_mcp_server("synthetic")

    assert server["name"] == "synthetic"
    assert [tool.name for tool in registered] == ["a_tool", "z_tool"]
    sdk_result = _run(registered[0].handler({"path": "."}))
    assert sdk_result == {
        "content": [{"type": "text", "text": "synthetic result"}],
        "is_error": False,
    }
    assert host.calls == [("a_tool", {"path": "."})]


def test_sdk_adapter_does_not_approve_or_execute_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    registered: list[Any] = []
    fake_sdk = ModuleType("claude_agent_sdk")

    def fake_tool(name: str, description: str, schema: dict, annotations: Any = None) -> Any:
        def decorate(handler: Any) -> Any:
            tool = SimpleNamespace(name=name, handler=handler)
            registered.append(tool)
            return tool

        return decorate

    fake_sdk.tool = fake_tool  # type: ignore[attr-defined]
    fake_sdk.create_sdk_mcp_server = lambda **kwargs: kwargs  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)

    host = RecordingHost(result="ok")
    def reject_approval(*_args: Any) -> None:
        raise AssertionError("approval bypass")

    host.request_approval = reject_approval  # type: ignore[attr-defined]
    bridge = HostToolBridge(host, [_openai()])
    bridge.build_sdk_mcp_server()
    _run(registered[0].handler({"path": "."}))
    assert host.calls == [("pwd", {"path": "."})]
