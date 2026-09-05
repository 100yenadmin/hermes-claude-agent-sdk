"""Provider-free AgentRuntime fixture that exercises Hermes delegation."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agent.runtime_api import (
    RuntimeCompletedEvent,
    RuntimeDescriptor,
    RuntimeFailure,
    RuntimeFailurePhase,
    RuntimeFailedEvent,
    RuntimeSelection,
    RuntimeStatusEvent,
)

PLUGIN_ID = "v4_delegation_runtime_fixture"
RUNTIME_ID = "v4-delegation-runtime-fixture"
PLUGIN_VERSION = "1.0.0"
PROVIDER_ID = "v4-provider-free-delegation"
MODEL_ID = "v4-provider-free-delegation-v1"
API_MODE = "agent_runtime"

ONE_PARENT_PROMPT = "v4 fixture parent: dispatch exactly one child"
FANOUT_PARENT_PROMPT = "v4 fixture parent: dispatch exactly two children"
ONE_CHILD_GOAL = "v4 fixture child: complete the single bounded task"
FANOUT_CHILD_GOALS = (
    "v4 fixture child A: complete the first bounded task",
    "v4 fixture child B: complete the second bounded task",
)
CHILD_SYSTEM_MARKER = "You are a focused subagent working on a specific delegated task."
DELEGATE_TOOL = "delegate_task"


def build_runtime_descriptor() -> RuntimeDescriptor:
    return RuntimeDescriptor(
        runtime_id=RUNTIME_ID,
        plugin_version=PLUGIN_VERSION,
        runtime_api_min=1,
        runtime_api_max=1,
        required_host_capabilities=frozenset({
            "host_tool_execution_v1", "host_tool_request_id_v1",
        }),
        provider_ids=frozenset({PROVIDER_ID}),
        api_modes=frozenset({API_MODE}),
        model_prefixes=("v4-provider-free-delegation-",),
        session_state_schema_version=1,
    )


def _failure(code: str, message: str) -> RuntimeFailure:
    return RuntimeFailure(
        code=code,
        message=message,
        phase=RuntimeFailurePhase.PREFLIGHT,
        replay_safe=False,
        retryable=False,
    )


def _is_child(request: Any) -> bool:
    return CHILD_SYSTEM_MARKER in str(getattr(request, "prompt_snapshot", ""))


def _has_tool(request: Any, name: str) -> bool:
    for schema in getattr(request, "tool_schemas", ()) or ():
        if not isinstance(schema, Mapping):
            continue
        function = schema.get("function")
        if isinstance(function, Mapping) and function.get("name") == name:
            return True
        if schema.get("name") == name:
            return True
    return False


def _parent_mode(request: Any) -> str | None:
    for message in reversed(getattr(request, "messages", ()) or ()):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        if message.get("content") == ONE_PARENT_PROMPT:
            return "one"
        if message.get("content") == FANOUT_PARENT_PROMPT:
            return "fanout"
    return None


class DelegationFixtureRuntime:
    """A deterministic parent/leaf runtime with no provider transport."""

    def __init__(self) -> None:
        self._parent_dispatch_used = False

    def preflight(self, request: Any) -> RuntimeFailure | None:
        if getattr(request, "selection", None) != RuntimeSelection(
            PROVIDER_ID, MODEL_ID, API_MODE
        ):
            return _failure("fixture_selection_unsupported", "fixture selection rejected")
        if _is_child(request):
            return None
        if self._parent_dispatch_used:
            return None
        if _parent_mode(request) is None:
            return _failure("fixture_parent_prompt_unsupported", "fixture prompt rejected")
        if not _has_tool(request, DELEGATE_TOOL):
            return _failure("fixture_delegate_tool_missing", "delegate_task is required")
        return None

    async def run_turn(self, request: Any, host: Any):
        if host.cancellation_requested():
            from agent.runtime_api import RuntimeCancelledEvent

            yield RuntimeCancelledEvent(reason="fixture cancelled")
            return
        yield RuntimeStatusEvent(message="v4 delegation fixture running")

        if _is_child(request):
            yield RuntimeCompletedEvent(
                result={
                    "final_response": "v4 fixture child completed",
                    "api_calls": 0,
                }
            )
            return
        if self._parent_dispatch_used:
            yield RuntimeCompletedEvent(
                result={
                    "final_response": "v4 fixture parent follow-up completed",
                    "api_calls": 0,
                }
            )
            return

        mode = _parent_mode(request)
        if mode == "one":
            tasks = [{"goal": ONE_CHILD_GOAL}]
        elif mode == "fanout":
            tasks = [{"goal": goal} for goal in FANOUT_CHILD_GOALS]
        else:  # pragma: no cover - preflight owns this invariant
            yield RuntimeFailedEvent(
                failure=_failure("fixture_parent_prompt_unsupported", "fixture prompt rejected")
            )
            return

        # Mark before crossing the host boundary: any later parent turn is a
        # fixed completion and can never recursively dispatch another child.
        self._parent_dispatch_used = True
        raw = await host.execute_tool(
            DELEGATE_TOOL,
            {"tasks": tasks},
            request_id=f"v4-delegation-{mode}",
        )
        payload = json.loads(raw) if isinstance(raw, str) else {}
        yield RuntimeCompletedEvent(
            result={
                "final_response": json.dumps(
                    {
                        "fixture": RUNTIME_ID,
                        "mode": mode,
                        "delegation_status": payload.get("status", "completed"),
                        "expected_children": len(tasks),
                    },
                    sort_keys=True,
                ),
                "api_calls": 0,
            }
        )

    async def close(self) -> None:
        return None


def create_runtime() -> DelegationFixtureRuntime:
    return DelegationFixtureRuntime()


def register(context: Any) -> None:
    context.register_agent_runtime(
        descriptor=build_runtime_descriptor(),
        factory=create_runtime,
    )
