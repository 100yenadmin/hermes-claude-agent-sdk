"""Provider-free task-only AgentRuntime fixture for v4 lifecycle tests."""
from __future__ import annotations
import json
import os
from pathlib import Path
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
PLUGIN_ID = "v4_local_runtime_fixture"
RUNTIME_ID = "v4-local-runtime-fixture"
PLUGIN_VERSION = "1.0.0"
PROVIDER_ID = "v4-local-fixture"
MODEL_ID = "v4-local-fixture-v1"
API_MODE = "agent_runtime"
PROMPT = "v4 local runtime fixture"
TOOL_NAME = "v4_local_runtime_state"
TOOLSET = "v4_local_runtime_fixture"
STATE_FILE = ".v4_local_runtime_fixture_state.json"
_OPERATION = "record"
SCHEMA = {
    "name": TOOL_NAME,
    "description": "Record one bounded synthetic local fixture state marker.",
    "parameters": {
        "type": "object",
        "properties": {"operation": {"type": "string", "enum": [_OPERATION]}},
        "required": ["operation"],
        "additionalProperties": False,
    },
}
def build_runtime_descriptor() -> RuntimeDescriptor:
    return RuntimeDescriptor(
        runtime_id=RUNTIME_ID, plugin_version=PLUGIN_VERSION,
        runtime_api_min=1, runtime_api_max=1,
        required_host_capabilities=frozenset({
            "host_approval_v1", "host_tool_execution_v1", "host_tool_request_id_v1",
        }), provider_ids=frozenset({PROVIDER_ID}), api_modes=frozenset({API_MODE}),
        model_prefixes=("v4-local-fixture-",), session_state_schema_version=1,
    )
def _invalid() -> ValueError:
    return ValueError("fixture input rejected")
def _state_path() -> Path:
    root = Path.cwd()
    if not root.is_absolute() or root == Path(root.anchor) or root.is_symlink(): raise _invalid()
    path = root / STATE_FILE
    if path.is_symlink(): raise _invalid()
    return path
def _normalize(args: Any) -> None:
    if not isinstance(args, dict) or set(args) != {"operation"} or args["operation"] != _OPERATION:
        raise _invalid()
def fixture_tool(args: dict[str, Any], **_: Any) -> str:
    _normalize(args)
    path = _state_path()
    if path.exists():
        if path.is_symlink():
            raise _invalid()
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            raise _invalid() from None
        if saved != {"schema_version": 1, "record_count": 1}:
            raise _invalid()
        raise _invalid()
    try:
        with open(path, "w", encoding="utf-8", opener=lambda name, flags: os.open(name, flags | os.O_NOFOLLOW, 0o600)) as handle:
            json.dump(
                {"schema_version": 1, "record_count": 1},
                handle,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
    except (OSError, ValueError):
        raise _invalid() from None
    return json.dumps(
        {"ok": True, "operation": _OPERATION, "record_count": 1},
        sort_keys=True,
        separators=(",", ":"),
    )
def pre_tool_call(tool_name: str, args: dict[str, Any], **_: Any) -> dict[str, str] | None:
    if tool_name != TOOL_NAME: return None
    try:
        _normalize(args)
    except ValueError:
        return {"action": "block", "message": "fixture input rejected"}
    return {
        "action": "approve",
        "message": "Hermes host approval required for fixture record",
        "rule_key": "v4-local-runtime-record",
    }
def _failure(code: str, message: str) -> RuntimeFailure:
    return RuntimeFailure(
        code=code,
        message=message,
        phase=RuntimeFailurePhase.PREFLIGHT,
        replay_safe=True,
    )
def _has_exact_tool_surface(request: Any) -> bool:
    schemas = tuple(getattr(request, "tool_schemas", ()))
    if len(schemas) != 1 or not hasattr(schemas[0], "get"):
        return False
    schema = schemas[0]
    function = schema.get("function")
    if hasattr(function, "get"):
        name = function.get("name")
        parameters = function.get("parameters")
    else:
        name = schema.get("name")
        parameters = schema.get("parameters")
    properties = parameters.get("properties") if hasattr(parameters, "get") else None
    operation = properties.get("operation") if hasattr(properties, "get") else None
    return (
        name == TOOL_NAME
        and set(parameters) == {"type", "properties", "required", "additionalProperties"}
        and parameters.get("type") == "object"
        and tuple(parameters.get("required", ())) == ("operation",)
        and parameters.get("additionalProperties") is False
        and hasattr(operation, "get")
        and operation.get("type") == "string"
        and tuple(operation.get("enum", ())) == (_OPERATION,)
    )
class LocalFixtureRuntime:
    def preflight(self, request: Any) -> RuntimeFailure | None:
        selection = getattr(request, "selection", None)
        if selection != RuntimeSelection(PROVIDER_ID, MODEL_ID, API_MODE):
            return _failure("fixture_runtime_selection_unsupported", "fixture selection rejected")
        if getattr(request, "prompt_snapshot", None) != PROMPT:
            return _failure("fixture_runtime_prompt_unsupported", "fixture prompt rejected")
        if not _has_exact_tool_surface(request):
            return _failure("fixture_runtime_tool_surface_unsupported", "fixture tool surface rejected")
        return None
    async def run_turn(self, request: Any, host: Any):
        if host.cancellation_requested():
            from agent.runtime_api import RuntimeCancelledEvent
            yield RuntimeCancelledEvent(reason="fixture cancelled")
            return
        yield RuntimeStatusEvent(message="v4 local fixture running")
        try:
            raw = await host.execute_tool(
                TOOL_NAME, {"operation": _OPERATION}, request_id="v4-local-record-1"
            )
            result = json.loads(raw) if isinstance(raw, str) else None
        except Exception:
            result = None
        if result != {"ok": True, "operation": _OPERATION, "record_count": 1}:
            yield RuntimeFailedEvent(
                failure=RuntimeFailure(
                    code="fixture_tool_not_approved",
                    message="fixture record was not approved",
                    phase=RuntimeFailurePhase.AFTER_SIDE_EFFECTS,
                    replay_safe=False,
                )
            )
            return
        yield RuntimeCompletedEvent(result=result)
    async def close(self) -> None:
        return None
def create_runtime() -> LocalFixtureRuntime:
    return LocalFixtureRuntime()
def register(context: Any) -> None:
    context.register_tool(
        name=TOOL_NAME,
        toolset=TOOLSET,
        schema=SCHEMA,
        handler=fixture_tool,
        is_async=False,
        description=SCHEMA["description"],
    )
    context.register_hook("pre_tool_call", pre_tool_call)
    context.register_agent_runtime(
        descriptor=build_runtime_descriptor(),
        factory=create_runtime,
    )
