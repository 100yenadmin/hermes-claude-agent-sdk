"""Sealed provider-free execution of the small v4 local fixture paths."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .hashing import canonical_json_bytes, sha256_value
from .v4_live_map import V4LiveMapViolation, load_v4_live_execution_map

_ROOT = Path(__file__).resolve().parents[3]
_MAP = _ROOT / "qa" / "parity-v4-live-execution-map.yaml"
_FIXTURE = _ROOT / "tests" / "fixtures" / "v4_local_runtime_plugin"
_MAP_SHA256 = "aa68ce417d9a8ad74110de76f37ef550e1f5414eba0a6ecba0af235ba1488c69"
_HOST_FILE_SHA256 = {
    "agent/runtime_api.py": "0b230d2ea4ab074bd52cd2cfcb34c53cab64ebd0edb3ef8ff2874454b320e604",
    "agent/runtime_dispatch.py": "896240c77103010caf03a99fd355492e2fd985eded9c20851ce968c8de03a15b",
}
_STATE = ".v4_local_runtime_fixture_state.json"
_TOOL = "v2_non_soak/TOOL-02"
_APPROVAL = "clawprobench_native/constraints_23_external_approval_boundary_live"
_ROWS = {_TOOL: "tool", _APPROVAL: "approval"}
_EVENTS = {
    "tool": ("start", "tool_requested", "tool_result", "state", "terminal"),
    "approval": ("start", "approval_requested", "approval_decision", "terminal"),
}


class V4LocalPathExecutorViolation(ValueError):
    """The fixed v4 local path could not be admitted or observed safely."""


def _fail(message: str) -> None:
    raise V4LocalPathExecutorViolation(message)


def _task_root(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)):
        _fail("task_root must be an absolute directory")
    root = Path(value)
    if not root.is_absolute() or ".." in root.parts or root.is_symlink() or len(str(root)) > 4096:
        _fail("task_root is invalid")
    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        _fail("task_root is invalid")
    if not root.is_dir() or root == Path(root.anchor):
        _fail("task_root is invalid")
    state = root / _STATE
    if state.exists() or state.is_symlink():
        _fail("task_root must be fresh")
    return root


def _snapshot(state: Path) -> dict[str, Any]:
    if state.is_symlink():
        _fail("fixture state may not be a symlink")
    if not state.exists():
        return {"present": False, "size": 0, "sha256": None}
    if not state.is_file() or state.stat().st_size > 4096:
        _fail("fixture state is not bounded")
    data = state.read_bytes()
    return {"present": True, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _fixture_module() -> Any:
    source = _FIXTURE / "__init__.py"
    if not source.is_file() or source.is_symlink():
        _fail("fixed local fixture is unavailable")
    spec = importlib.util.spec_from_file_location("hermes_v4_local_fixture", source, submodule_search_locations=[str(_FIXTURE)])
    if spec is None or spec.loader is None or Path(spec.origin or "").resolve() != source.resolve():
        _fail("fixed local fixture identity is invalid")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (ImportError, OSError, RuntimeError, SyntaxError, TypeError, ValueError):
        _fail("fixed local fixture could not be loaded")
    return module


def _host_root() -> Path:
    value = os.environ.get("HERMES_AGENT_HOST_ROOT")
    if not value:
        _fail("HERMES_AGENT_HOST_ROOT is required")
    root = Path(value)
    if not root.is_absolute() or ".." in root.parts or root.is_symlink() or len(str(root)) > 4096:
        _fail("HERMES_AGENT_HOST_ROOT must be a direct absolute path")
    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        _fail("HERMES_AGENT_HOST_ROOT is unavailable")
    if not root.is_dir() or root == Path(root.anchor):
        _fail("HERMES_AGENT_HOST_ROOT must be a non-root directory")
    for relative in ("agent/runtime_api.py", "agent/runtime_dispatch.py"):
        path = root / relative
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and not path.is_symlink() else None
        except OSError:
            digest = None
        if digest != _HOST_FILE_SHA256[relative]:
            _fail("HERMES_AGENT_HOST_ROOT is not the expected host")
    return root


def _event(kind: str, ordinal: int, path: str, facts: Mapping[str, Any], terminal: str | None = None) -> dict[str, Any]:
    projection = {"source": "host_runtime", "kind": kind, "ordinal": ordinal, "path": path, **dict(facts)}
    encoded = canonical_json_bytes(projection)
    return {"kind": kind, "byte_length": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest(), "terminal_status": terminal}


def _install(home: Path, fixture: Path) -> None:
    (home / "plugins").mkdir(parents=True)
    shutil.copytree(fixture, home / "plugins" / "v4_local_runtime_fixture")
    (home / "config.yaml").write_text("plugins:\n  enabled:\n    - v4_local_runtime_fixture\n", encoding="utf-8")


def _run_attempt(plugin: Any, registration: Any, dispatch: Any, number: int) -> dict[str, Any]:
    class Agent:
        session_id = "v4-local-session"
        _interrupt_requested = False

        def __init__(self) -> None:
            self.valid_tool_names = frozenset({plugin.TOOL_NAME})
            self.tools = ({"type": "function", "function": plugin.SCHEMA},)
            self.execution_calls: list[str] = []

        def _execute_tool_calls(self, assistant_message: Any, turn_messages: list[dict[str, Any]], task_id: str) -> None:
            from model_tools import handle_function_call
            call = assistant_message.tool_calls[0]
            args = json.loads(call.function.arguments)
            self.execution_calls.append(call.id)
            result = handle_function_call(call.function.name, args, task_id=task_id, session_id=self.session_id, tool_call_id=call.id, turn_id=task_id)
            turn_messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    class Host(dispatch.HermesRuntimeHostServices):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.execute_calls = 0
            self.result_count = 0
            self.result_ok = 0
            super().__init__(*args, **kwargs)

        async def execute_tool(self, name: str, arguments: Mapping[str, Any], *, request_id: str | None = None) -> Any:
            self.execute_calls += 1
            result = await super().execute_tool(name, arguments, request_id=request_id)
            self.result_count += 1
            try:
                value = json.loads(result) if isinstance(result, str) else None
                self.result_ok += value == {"ok": True, "operation": "record", "record_count": 1}
            except (TypeError, ValueError):
                pass
            return result

    agent = Agent()
    messages: list[dict[str, Any]] = []
    host = Host(agent, task_id=f"v4-local-turn-{number}", runtime_id=plugin.RUNTIME_ID, turn_messages=messages)
    request = dispatch.build_runtime_turn_request(provider=plugin.PROVIDER_ID, model=plugin.MODEL_ID, api_mode=plugin.API_MODE, messages=({"role": "user", "content": plugin.PROMPT},), prompt_snapshot=plugin.PROMPT, tool_schemas=host._agent.tools, correlation_id=f"v4-local-correlation-{number}")
    runtime = registration.factory()
    from model_tools import _run_async
    try:
        outcome = dispatch.run_runtime_sync(runtime, request, host, descriptor=registration.descriptor)
    finally:
        _run_async(runtime.close())
    event_types = [type(event).__name__ for event in outcome.events]
    terminal = "completed" if outcome.completed else "denied" if outcome.failure and outcome.failure.code == "fixture_tool_not_approved" else "failed"
    if not event_types or event_types[0] != "RuntimeStatusEvent":
        _fail("fixture lifecycle did not emit a start event")
    if terminal == "completed" and event_types[-1] != "RuntimeCompletedEvent":
        _fail("fixture lifecycle did not emit a completed terminal")
    if terminal == "denied" and event_types[-1] != "RuntimeFailedEvent":
        _fail("fixture lifecycle did not emit a denied terminal")
    return {"terminal": terminal, "completed": outcome.completed, "execute_calls": host.execute_calls, "result_count": host.result_count, "result_ok": host.result_ok, "events": event_types}


def execute_v4_local_path(*, row_key: str, trial_index: int, path: str, task_root: str | Path) -> dict[str, Any]:
    """Execute one fixed map row through the real host and return a closed local packet."""
    if not isinstance(row_key, str) or row_key not in _ROWS or not isinstance(path, str) or path not in {"positive", "denial", "recovery"} or type(trial_index) is not int:
        _fail("row, trial, or path is unsupported")
    root = _task_root(task_root)
    try:
        document = load_v4_live_execution_map(_MAP)
        map_hash = hashlib.sha256(_MAP.read_bytes()).hexdigest()
    except (OSError, TypeError, V4LiveMapViolation):
        _fail("immutable v4 map is unavailable")
    if map_hash != _MAP_SHA256 or document.get("source", {}).get("contract_sha256") != "53864834496403388f3475291475fea70acfa3105609ad49f5edf75ad1c67d94":
        _fail("immutable v4 map identity drifted")
    row = next((item for item in document["rows"] if f"{item['source_pack']}/{item['source_item_id']}" == row_key), None)
    if not isinstance(row, Mapping) or path not in row.get("mandatory_paths", []) or trial_index not in row.get("required_trial_indexes", []):
        _fail("row path or trial is not mapped")
    category = _ROWS[row_key]
    if category != "approval" and path != "positive":
        _fail("denial and recovery are supported only for the approval row")

    host_root = _host_root()
    host_str = str(host_root)
    if host_str not in sys.path:
        sys.path.insert(0, host_str)
    plugin = _fixture_module()
    try:
        from agent import runtime_api as host_api
        from agent import runtime_dispatch as dispatch
        from hermes_cli import plugins as plugins_mod
        from hermes_cli.plugins import PluginManager
        from hermes_constants import (
            hermes_home_key,
            reset_hermes_home_override,
            set_hermes_home_override,
        )
        from tools.terminal_tool import _get_approval_callback, set_approval_callback
    except (ImportError, OSError, RuntimeError):
        _fail("fixed host runtime could not be loaded")
    if Path(getattr(dispatch, "__file__", "")).resolve() != host_root / "agent" / "runtime_dispatch.py" or Path(getattr(host_api, "__file__", "")).resolve() != host_root / "agent" / "runtime_api.py":
        _fail("fixed host runtime identity drifted")
    home, bundled = root / ".hermes-home", root / ".empty-bundled"
    if home.exists() or bundled.exists() or home.is_symlink() or bundled.is_symlink():
        _fail("task_root contains reserved runtime paths")
    home.mkdir(); bundled.mkdir(); _install(home, _FIXTURE)
    old_env = {key: os.environ.get(key) for key in ("HERMES_HOME", "HERMES_BUNDLED_PLUGINS", "HERMES_INTERACTIVE")}
    old_manager = getattr(plugins_mod, "_plugin_manager", None)
    old_callback = _get_approval_callback()
    override = set_hermes_home_override(home)
    decisions: list[str] = []
    attempts: list[dict[str, Any]] = []
    state = root / _STATE
    old_cwd = Path.cwd()
    manager: Any = None
    try:
        os.environ.update({"HERMES_HOME": str(home), "HERMES_BUNDLED_PLUGINS": str(bundled), "HERMES_INTERACTIVE": "1"})
        os.chdir(root)
        planned = iter(("deny", "once") if path == "recovery" else ("deny",) if path == "denial" else ("once",))
        def approval_callback(*_: Any, **__: Any) -> str:
            try:
                choice = next(planned)
            except StopIteration:
                _fail("fixture requested an unexpected approval")
            decisions.append(choice)
            return choice
        set_approval_callback(approval_callback)
        manager = PluginManager(scope_key=hermes_home_key(home)); manager._scan_entry_points = list
        plugins_mod._plugin_manager = manager
        manager.discover_and_load()
        registration = manager.select_agent_runtime(host_api.RuntimeSelection(plugin.PROVIDER_ID, plugin.MODEL_ID, plugin.API_MODE))
        if registration is None or registration.plugin_id != plugin.PLUGIN_ID:
            _fail("fixed fixture runtime was not selected")
        before = _snapshot(state)
        if path == "recovery":
            attempts.append(_run_attempt(plugin, registration, dispatch, 1))
            denied = _snapshot(state)
            attempts.append(_run_attempt(plugin, registration, dispatch, 2))
            after = _snapshot(state)
            if attempts[0]["terminal"] != "denied" or denied["present"] or attempts[1]["terminal"] != "completed" or not after["present"]:
                _fail("fixture recovery did not prove denial then one write")
        else:
            attempts.append(_run_attempt(plugin, registration, dispatch, 1))
            after = _snapshot(state)
            if path == "denial" and (attempts[0]["terminal"] != "denied" or after["present"]):
                _fail("fixture denial did not prove no write")
            if path == "positive" and attempts[0]["terminal"] != "completed":
                _fail("fixture positive path did not complete")
        if len(decisions) != len(attempts):
            _fail("fixture approval was not observed")
        terminal = "denied" if path == "denial" else "completed"
        if path == "recovery":
            denied = {"present": False, "size": 0, "sha256": None}
        observation = {"identity": {"row_key": row_key, "path": path, "trial_index": trial_index}, "surface": "host_runtime_dispatch", "runtime": plugin.RUNTIME_ID, "lifecycle": {"start": all(item["events"][0] == "RuntimeStatusEvent" for item in attempts), "terminal": terminal}, "attempt_count": len(attempts), "tool": {"request_count": sum(item["execute_calls"] for item in attempts), "result_count": sum(item["result_count"] for item in attempts)}, "approval": {"request_count": len(decisions), "decision_count": len(decisions), "decisions": list(decisions)}, "state_before": before, "state_after": after, "prior_denial": {"observed": path == "recovery" and attempts[0]["terminal"] == "denied", "no_write": path == "recovery" and not denied["present"]}, "no_write_on_denial": path in {"denial", "recovery"} and (not after["present"] if path == "denial" else not denied["present"]), "single_write_on_recovery": path == "recovery" and not denied["present"] and after["present"], "provider_calls": 0}
        events = []
        for ordinal, kind in enumerate(_EVENTS[category], 1):
            facts = {"attempts": len(attempts)}
            if kind == "state": facts.update({"before": before, "after": after})
            elif kind == "tool_requested": facts["request_count"] = observation["tool"]["request_count"]
            elif kind == "tool_result": facts["result_count"] = observation["tool"]["result_count"]
            elif kind == "approval_requested": facts["request_count"] = len(decisions)
            elif kind == "approval_decision": facts.update({"decision_count": len(decisions), "decisions": list(decisions)})
            events.append(_event(kind, ordinal, path, facts, terminal if kind == "terminal" else None))
        proof_primary = sha256_value(observation)
        proof_secondary = sha256_value({"identity": observation["identity"], "events": events})
        return {"schema_version": 1, "status": "PASS", "path": path, "host_local": True, "provider_calls": 0, "terminal_status": terminal, "events": events, "observation": observation, "proof_hashes": {"primary": proof_primary, "secondary": proof_secondary}}
    finally:
        if manager is not None:
            manager.unload(plugin.PLUGIN_ID)
        set_approval_callback(old_callback)
        plugins_mod._plugin_manager = old_manager
        reset_hermes_home_override(override)
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        os.chdir(old_cwd)


__all__ = ["V4LocalPathExecutorViolation", "execute_v4_local_path"]
