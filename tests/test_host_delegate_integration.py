"""Cross-repository delegate_task contract coverage.

This test intentionally uses the public plugin bridge and the public Hermes
runtime host facade together.  The synthetic agent is only the smallest
``AIAgent`` surface needed by ``HermesRuntimeHostServices.execute_tool``: its
canonical tool executor records the generated call and forwards
``delegate_task`` to a recording dispatcher.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest


# The standalone plugin's existing host-integration tests use this same
# explicit host-root override. Keeping the host checkout out of the plugin's
# package makes the cross-repository dependency visible at test time without
# binding the suite to one developer-machine path.
_HOST_ROOT_VALUE = os.environ.get("HERMES_AGENT_HOST_ROOT")
if not _HOST_ROOT_VALUE:
    pytest.skip("HERMES_AGENT_HOST_ROOT is not configured", allow_module_level=True)
HOST_ROOT = Path(_HOST_ROOT_VALUE)
if not HOST_ROOT.is_dir():
    pytest.skip("configured Hermes host checkout is absent", allow_module_level=True)
if str(HOST_ROOT) not in sys.path:
    sys.path.insert(0, str(HOST_ROOT))

from agent.runtime_dispatch import HermesRuntimeHostServices
from tools.delegate_tool import DELEGATE_TASK_SCHEMA

from hermes_claude_agent_sdk.tool_bridge import HostToolBridge


PARENT_SESSION_ID = "synthetic-parent-session-42"
TASK_ID = "synthetic-parent-turn-42"
CORRELATION_ID = "synthetic-correlation-42"
REQUEST_ID = "synthetic-sdk-request-42"


def _delegate_tool_schema() -> dict[str, Any]:
    """Adapt Hermes' registry schema to the public function-tool shape."""

    return {
        "type": "function",
        "function": {
            "name": DELEGATE_TASK_SCHEMA["name"],
            "description": DELEGATE_TASK_SCHEMA["description"],
            "parameters": DELEGATE_TASK_SCHEMA["parameters"],
        },
    }


class _SyntheticAgent:
    """Minimal host agent surface; no real delegate implementation is called."""

    session_id = PARENT_SESSION_ID
    valid_tool_names = frozenset({"delegate_task"})
    tools = (_delegate_tool_schema(),)
    _interrupt_requested = False
    _delegate_depth = 0

    def __init__(self) -> None:
        self.execution_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.dispatch_calls: list[tuple[str, dict[str, Any]]] = []
        self.approval_calls = 0

    def _execute_tool_calls(
        self,
        assistant_message: Any,
        tool_messages: list[dict[str, Any]],
        effective_task_id: str,
    ) -> None:
        """Exercise the host's normal assistant/tool-result execution seam."""

        assert len(assistant_message.tool_calls) == 1
        tool_call = assistant_message.tool_calls[0]
        assert tool_call.type == "function"
        assert tool_call.function.name == "delegate_task"
        arguments = json.loads(tool_call.function.arguments)
        self.execution_calls.append(
            (effective_task_id, tool_call.id, arguments)
        )
        result = self._dispatch_delegate_task(arguments)
        tool_messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            }
        )

    def _dispatch_delegate_task(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """Record the canonical delegate entry point without spawning work."""

        self.dispatch_calls.append((self.session_id, function_args))
        return {"accepted": True, "route": "synthetic-host"}

    def request_approval(self, *_args: Any, **_kwargs: Any) -> bool:
        self.approval_calls += 1
        raise AssertionError("delegate bridge must not bypass host tool execution")


def test_delegate_schema_bridge_reaches_real_host_facade_and_parent_dispatch() -> None:
    agent = _SyntheticAgent()
    host = HermesRuntimeHostServices(
        agent,
        task_id=TASK_ID,
        runtime_id="synthetic-runtime",
    )
    bridge = HostToolBridge(
        host,
        [_delegate_tool_schema()],
        correlation_id=CORRELATION_ID,
    )
    arguments = {
        "tasks": [
            {
                "goal": "Synthetic bounded delegation goal",
                "context": "Synthetic bounded delegation context",
            }
        ],
        "action": "spawn",
    }

    result = asyncio.run(
        bridge.handle_tool_call(
            request_id=REQUEST_ID,
            name="delegate_task",
            arguments=arguments,
        )
    )

    assert bridge.tool_names == ("delegate_task",)
    assert bridge.host_execution_count == 1
    assert result.request_id == REQUEST_ID
    assert result.correlation_id == CORRELATION_ID
    assert result.tool_name == "delegate_task"
    assert result.is_error is False
    assert result.text == '{"accepted":true,"route":"synthetic-host"}'
    assert agent.execution_calls == [
        (TASK_ID, "runtime-tool-0001", arguments)
    ]
    assert agent.dispatch_calls == [(PARENT_SESSION_ID, arguments)]
    assert agent.approval_calls == 0
