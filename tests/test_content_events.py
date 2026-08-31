"""Contract tests for the dependency-light Claude SDK event projector.

The package imports the host's public AgentRuntime v1 types.  The fallback
below exists only so this standalone repository can run its tests when the
host checkout is not installed; it mirrors the small public types exercised by
these tests and never stands in for the package implementation.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


def _install_runtime_api_test_compat() -> None:
    if "agent.runtime_api" in sys.modules:
        return

    runtime_api = types.ModuleType("agent.runtime_api")

    class RuntimeEventKind(str, Enum):
        CONTENT = "content"
        TOOL_REQUEST = "tool_request"
        USAGE = "usage"
        COMPLETED = "completed"

    @dataclass(frozen=True)
    class RuntimeContentEvent:
        text: str = ""
        kind: RuntimeEventKind = field(
            default=RuntimeEventKind.CONTENT, init=False
        )

    @dataclass(frozen=True)
    class RuntimeToolRequestEvent:
        request_id: str
        name: str
        arguments: Mapping[str, Any]
        kind: RuntimeEventKind = field(
            default=RuntimeEventKind.TOOL_REQUEST, init=False
        )

    @dataclass(frozen=True)
    class RuntimeUsageReceipt:
        runtime_id: str
        provider: str
        model: str
        billing_mode: str
        cost_status: str
        input_tokens: int = 0
        output_tokens: int = 0
        cache_read_tokens: int = 0
        cache_write_tokens: int = 0
        reasoning_tokens: int = 0
        replay_safe: bool = False
        correlation_id: str | None = None

    @dataclass(frozen=True)
    class RuntimeUsageEvent:
        receipt: RuntimeUsageReceipt
        kind: RuntimeEventKind = field(default=RuntimeEventKind.USAGE, init=False)

    @dataclass(frozen=True)
    class RuntimeCompletedEvent:
        result: Mapping[str, Any] | None = None
        kind: RuntimeEventKind = field(
            default=RuntimeEventKind.COMPLETED, init=False
        )

    for name, value in {
        "RuntimeContentEvent": RuntimeContentEvent,
        "RuntimeToolRequestEvent": RuntimeToolRequestEvent,
        "RuntimeUsageEvent": RuntimeUsageEvent,
        "RuntimeUsageReceipt": RuntimeUsageReceipt,
        "RuntimeCompletedEvent": RuntimeCompletedEvent,
        "RuntimeEvent": Any,
    }.items():
        setattr(runtime_api, name, value)

    agent = types.ModuleType("agent")
    agent.__path__ = []  # type: ignore[attr-defined]
    sys.modules.setdefault("agent", agent)
    sys.modules["agent.runtime_api"] = runtime_api


try:
    from agent.runtime_api import (
        RuntimeCompletedEvent,
        RuntimeContentEvent,
        RuntimeToolRequestEvent,
        RuntimeUsageEvent,
    )
except ModuleNotFoundError as exc:
    if exc.name not in {"agent", "agent.runtime_api"}:
        raise
    _install_runtime_api_test_compat()
    from agent.runtime_api import (
        RuntimeCompletedEvent,
        RuntimeContentEvent,
        RuntimeToolRequestEvent,
        RuntimeUsageEvent,
    )

from hermes_claude_agent_sdk.content_events import ClaudeSdkEventProjector


class TextBlock:
    def __init__(self, text: str):
        self.text = text


class ThinkingBlock:
    def __init__(self, thinking: str):
        self.thinking = thinking


class ToolUseBlock:
    def __init__(self, *, block_id: str, name: str, input: Any):
        self.id = block_id
        self.name = name
        self.input = input


class ToolResultBlock:
    def __init__(self, *, tool_use_id: str, content: Any, is_error: bool = False):
        self.tool_use_id = tool_use_id
        self.content = content
        self.is_error = is_error


class AssistantMessage:
    def __init__(self, content: list[Any], model: str = "claude-test"):
        self.content = content
        self.model = model


class UserMessage:
    def __init__(self, content: Any):
        self.content = content


class ResultMessage:
    def __init__(
        self,
        *,
        result: Any = None,
        usage: Any = None,
        model: str = "claude-test",
        is_error: bool = False,
    ):
        self.result = result
        self.usage = usage
        self.model = model
        self.is_error = is_error


class SystemMessage:
    content = "lifecycle"


class UnknownObject:
    def __repr__(self) -> str:
        return "UnknownObject(raw-secret-like-value)"


class ExplodingMessage:
    @property
    def content(self) -> Any:
        raise RuntimeError("raw exception should not cross the projector")


def _events(projector: ClaudeSdkEventProjector, message: Any) -> list[Any]:
    return list(projector.project(message).events)


def test_assistant_text_maps_to_one_bounded_public_content_event() -> None:
    result = ClaudeSdkEventProjector().project(
        AssistantMessage([TextBlock("hello"), TextBlock("world")])
    )

    assert result.final_text == "hello\nworld"
    assert result.model == "claude-test"
    assert result.events == (RuntimeContentEvent(text="hello\nworld"),)


def test_assistant_tool_use_maps_to_public_tool_request_metadata() -> None:
    result = ClaudeSdkEventProjector().project(
        AssistantMessage(
            [
                TextBlock("I will inspect it"),
                ToolUseBlock(
                    block_id="toolu_test_1",
                    name="read_file",
                    input={"path": "/tmp/synthetic.txt", "line": 3},
                ),
            ]
        )
    )

    assert result.events == (
        RuntimeContentEvent(text="I will inspect it"),
        RuntimeToolRequestEvent(
            request_id="toolu_test_1",
            name="read_file",
            arguments={"path": "/tmp/synthetic.txt", "line": 3},
        ),
    )
    assert not result.is_tool_iteration


def test_user_tool_results_map_to_bounded_content_without_raw_objects() -> None:
    result = ClaudeSdkEventProjector().project(
        UserMessage(
            [
                ToolResultBlock(
                    tool_use_id="toolu_test_1",
                    content=[
                        {"type": "text", "text": "synthetic result"},
                        {"type": "json", "value": 3},
                    ],
                ),
                ToolResultBlock(
                    tool_use_id="toolu_test_2",
                    content=UnknownObject(),
                    is_error=True,
                ),
            ]
        )
    )

    assert result.is_tool_iteration
    assert len(result.events) == 2
    assert all(isinstance(event, RuntimeContentEvent) for event in result.events)
    assert result.events[0].text == (
        '[tool_result tool_use_id="toolu_test_1"] '
        'synthetic result\n{"type": "json", "value": 3}'
    )
    assert result.events[1].text == (
        '[tool_result tool_use_id="toolu_test_2" error] [unavailable tool result]'
    )
    assert "raw-secret-like-value" not in result.events[1].text


def test_result_maps_authoritative_text_usage_and_completion() -> None:
    usage = {
        "input_tokens": 11,
        "output_tokens": 7,
        "cache_read_input_tokens": 5,
        "cache_creation_input_tokens": 2,
    }
    result = ClaudeSdkEventProjector(
        runtime_id="claude-agent-sdk",
        provider="claude-agent-sdk",
        billing_mode="subscription_included",
        correlation_id="turn-test-1",
    ).project(
        ResultMessage(result="authoritative final", usage=usage)
    )

    assert result.is_result
    assert result.final_text == "authoritative final"
    assert result.events[0] == RuntimeContentEvent(text="authoritative final")
    assert isinstance(result.events[1], RuntimeUsageEvent)
    assert result.events[1].receipt.input_tokens == 11
    assert result.events[1].receipt.output_tokens == 7
    assert result.events[1].receipt.cache_read_tokens == 5
    assert result.events[1].receipt.cache_write_tokens == 2
    assert result.events[1].receipt.billing_mode == "subscription_included"
    assert result.events[1].receipt.cost_status == "included"
    assert result.events[1].receipt.correlation_id == "turn-test-1"
    assert result.events[2] == RuntimeCompletedEvent(
        result={"text": "authoritative final", "model": "claude-test"}
    )


def test_result_without_usage_still_completes_and_error_is_bounded() -> None:
    result = ClaudeSdkEventProjector().project(
        ResultMessage(result="done", usage=None, is_error=True)
    )

    assert result.events[0] == RuntimeContentEvent(text="done")
    assert result.events[-1] == RuntimeCompletedEvent(
        result={"text": "done", "model": "claude-test", "is_error": True}
    )
    assert all("ResultMessage" not in repr(event) for event in result.events)


def test_thinking_only_and_lifecycle_messages_do_not_leak_or_emit_events() -> None:
    projector = ClaudeSdkEventProjector()

    assert _events(projector, AssistantMessage([ThinkingBlock("private thought")])) == []
    assert _events(projector, SystemMessage()) == []
    assert _events(projector, UnknownObject()) == []
    assert _events(projector, ExplodingMessage()) == []


def test_tool_arguments_are_sanitized_and_bounded() -> None:
    result = ClaudeSdkEventProjector().project(
        AssistantMessage(
            [
                ToolUseBlock(
                    block_id="toolu_test_3",
                    name="",
                    input=UnknownObject(),
                )
            ]
        )
    )

    assert result.events == (
        RuntimeToolRequestEvent(
            request_id="toolu_test_3",
            name="unknown",
            arguments={"input": "[unavailable tool input]"},
        ),
    )
