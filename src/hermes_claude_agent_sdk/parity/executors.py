"""Repo-owned deterministic executors for the smallest supported v3 paths."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from .active_suite import active_agentic_suite, active_execution_ids
from .focused_suite import boundary_execution_ids, boundary_focused_suite
from .hashing import sha256_value
from .native_suite import native_execution_ids, native_scenario_suite
from .results import ExecutionClassification
from .runner import ExecutionBundle, ExecutionContext, ExecutionOutcome
from .runtime_suite import active_runtime_100_turn, runtime_execution_ids
from .sdk_identity import candidate_sdk_failure
from .tool_inventory import APPROVAL_TOOL_NAME, APPROVAL_TOOL_SCHEMA
from .v2_suite import v2_execution_ids, v2_mapped_suite

_TOOL_NAME = APPROVAL_TOOL_NAME
_TOOL_SCHEMA = APPROVAL_TOOL_SCHEMA


def _blocked_bundle(reason: str) -> ExecutionBundle:
    outcomes = {
        path: ExecutionOutcome(
            classification=ExecutionClassification.ENVIRONMENT_BLOCKED,
            billing_classification="none",
            reason_code=reason,
        )
        for path in ("positive", "denial", "recovery")
    }
    return ExecutionBundle(outcomes=outcomes, turn_count=0)


def _event_hash(value: Any) -> str:
    return sha256_value(value)


class _ApprovalAgent:
    """Smallest host agent seam that still uses the canonical host facade."""

    session_id = "parity-v3-isolated-parent"
    valid_tool_names = frozenset({_TOOL_NAME})
    tools = (_TOOL_SCHEMA,)
    _interrupt_requested = False
    _delegate_depth = 0

    def __init__(self) -> None:
        self._decisions = [True, False, True]
        self.approval_count = 0
        self.side_effect_count = 0
        self.tool_results: list[dict[str, Any]] = []

    def _execute_tool_calls(
        self,
        assistant_message: Any,
        tool_messages: list[dict[str, Any]],
        effective_task_id: str,
    ) -> None:
        if len(assistant_message.tool_calls) != 1 or not self._decisions:
            raise RuntimeError("thin gate received an unexpected tool call")
        tool_call = assistant_message.tool_calls[0]
        arguments = json.loads(tool_call.function.arguments)
        if (
            tool_call.function.name != _TOOL_NAME
            or arguments != {"marker": "feature-parity-v3"}
            or effective_task_id != "parity-v3-thin-gate"
        ):
            raise RuntimeError("thin gate tool request escaped its exact fixture")
        approved = self._decisions.pop(0)
        self.approval_count += 1
        if approved:
            self.side_effect_count += 1
        result = {
            "approved": approved,
            "side_effect_count": self.side_effect_count,
            "status": "executed" if approved else "denied",
        }
        self.tool_results.append(result)
        tool_messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            }
        )


def _events(path: str, *, approved: bool, state: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    events: list[dict[str, Any]] = [
        {
            "sequence": 1,
            "kind": "start",
            "status": "started",
        },
        {
            "sequence": 2,
            "kind": "approval_requested",
            "status": "requested",
            "name_hash": _event_hash(_TOOL_NAME),
        },
        {
            "sequence": 3,
            "kind": "approval_decision",
            "status": "approved" if approved else "denied",
            "metadata_hash": _event_hash({"path": path, "approved": approved}),
        },
    ]
    if approved:
        events.extend(
            [
                {
                    "sequence": 4,
                    "kind": "tool_requested",
                    "status": "admitted",
                    "tool_hash": _event_hash(_TOOL_SCHEMA),
                },
                {
                    "sequence": 5,
                    "kind": "tool_result",
                    "status": "executed_once",
                    "state_hash": _event_hash(state),
                },
                {
                    "sequence": 6,
                    "kind": "terminal",
                    "status": "completed",
                    "terminal_outcome": "completed",
                },
            ]
        )
    else:
        events.append(
            {
                "sequence": 4,
                "kind": "terminal",
                "status": "denied",
                "terminal_outcome": "denied",
            }
        )
    return tuple(events)


async def approval_followthrough(context: ExecutionContext) -> ExecutionBundle:
    """Exercise approve, denial, and recovery through the real host facade."""

    if context.profile_id != "fable-v3-isolated":
        return _blocked_bundle("profile_not_isolated")
    sdk_failure = candidate_sdk_failure(context.sdk_version)
    if sdk_failure is not None:
        return _blocked_bundle(sdk_failure)
    if os.environ.get("HERMES_PARITY_PLUGIN_SHA") != context.plugin_sha:
        return _blocked_bundle("plugin_sha_unverified")
    if os.environ.get("HERMES_AGENT_HOST_SHA") != context.host_sha:
        return _blocked_bundle("host_sha_unverified")
    try:
        from agent.runtime_api import HOST_RUNTIME_CAPABILITIES, RUNTIME_API_VERSION
        from agent.runtime_dispatch import HermesRuntimeHostServices

        from hermes_claude_agent_sdk.tool_bridge import HostToolBridge
    except Exception:  # noqa: BLE001 - import faults fail the parity gate closed
        return _blocked_bundle("host_contract_unavailable")
    if RUNTIME_API_VERSION != 1 or not {
        "host_approval_v1",
        "host_tool_execution_v1",
    } <= set(HOST_RUNTIME_CAPABILITIES):
        return _blocked_bundle("host_contract_incompatible")

    agent = _ApprovalAgent()
    host = HermesRuntimeHostServices(
        agent,
        task_id="parity-v3-thin-gate",
        runtime_id="claude-agent-sdk",
    )
    bridge = HostToolBridge(
        host,
        [_TOOL_SCHEMA],
        correlation_id="parity-v3-thin-gate",
    )
    for index in range(3):
        result = await bridge.handle_tool_call(
            request_id=f"parity-v3-{index + 1}",
            name=_TOOL_NAME,
            arguments={"marker": "feature-parity-v3"},
        )
        if result.is_error:
            raise RuntimeError("host tool bridge returned an unexpected error")
    if (
        agent.approval_count != 3
        or agent.side_effect_count != 2
        or bridge.host_execution_count != 3
        or [item["approved"] for item in agent.tool_results] != [True, False, True]
        or [item["side_effect_count"] for item in agent.tool_results] != [1, 1, 2]
    ):
        raise RuntimeError("approval thin gate violated exact-once or denial fencing")

    common = {
        "plugin_sha": context.plugin_sha,
        "host_sha": context.host_sha,
        "sdk_version": context.sdk_version,
        "tool_schema_hash": _event_hash(_TOOL_SCHEMA),
        "approval_count": agent.approval_count,
    }
    states = {
        "positive": agent.tool_results[0],
        "denial": agent.tool_results[1],
        "recovery": agent.tool_results[2],
    }
    outcomes: dict[str, ExecutionOutcome] = {}
    for path, approved in (("positive", True), ("denial", False), ("recovery", True)):
        classification = (
            ExecutionClassification.COMPLETE
            if approved
            else ExecutionClassification.EXPECTED_NEGATIVE
        )
        proof = {**common, "path": path, "state": states[path]}
        outcomes[path] = ExecutionOutcome(
            classification=classification,
            billing_classification="none",
            normalized_events=_events(path, approved=approved, state=states[path]),
            primary_proof_hash=_event_hash(proof),
            secondary_proof_hash=_event_hash(
                {
                    "candidate": context.catalog_hash,
                    "inventory": context.inventory_hash,
                    "profile": context.profile_hash,
                    "path": path,
                }
            ),
            turn_count=0,
        )
    return ExecutionBundle(outcomes=outcomes, turn_count=0)


EXECUTORS = {
    "active-approval-turn-tool-followthrough": approval_followthrough,
    **{
        execution_id: active_agentic_suite
        for execution_id in active_execution_ids()
    },
    **{
        execution_id: v2_mapped_suite
        for execution_id in v2_execution_ids()
    },
    **{
        execution_id: boundary_focused_suite
        for execution_id in boundary_execution_ids()
    },
    **{
        execution_id: native_scenario_suite
        for execution_id in native_execution_ids()
    },
    **{
        execution_id: active_runtime_100_turn
        for execution_id in runtime_execution_ids()
    },
}


__all__ = ["EXECUTORS", "approval_followthrough"]
