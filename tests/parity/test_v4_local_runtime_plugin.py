"""Provider-free end-to-end proof for the task-only local runtime fixture."""
from __future__ import annotations
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from typing import Any, Mapping
import pytest
_HOST_ROOT_VALUE = os.environ.get("HERMES_AGENT_HOST_ROOT")
if not _HOST_ROOT_VALUE or not Path(_HOST_ROOT_VALUE).is_dir():
    pytest.skip("HERMES_AGENT_HOST_ROOT is not configured", allow_module_level=True)
HOST_ROOT = Path(_HOST_ROOT_VALUE).resolve()
if str(HOST_ROOT) not in sys.path:
    sys.path.insert(0, str(HOST_ROOT))
from agent.runtime_api import RuntimeSelection  # noqa: E402
from agent.runtime_dispatch import (  # noqa: E402
    HermesRuntimeHostServices,
    build_runtime_turn_request,
    run_runtime_sync,
)
from hermes_cli import plugins as plugins_mod  # noqa: E402
from hermes_cli.plugins import PluginManager  # noqa: E402
from hermes_constants import (  # noqa: E402
    hermes_home_key,
    reset_hermes_home_override,
    set_hermes_home_override,
)
PLUGIN_ROOT = Path(__file__).parents[1] / "fixtures" / "v4_local_runtime_plugin"
def _fixture_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "v4_local_runtime_plugin_test",
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
class _RecordingContext:
    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = []
        self.hooks: list[tuple[str, Any]] = []
        self.runtimes: list[tuple[Any, Any]] = []
    def register_tool(self, **kwargs: Any) -> None:
        self.tools.append(kwargs)
    def register_hook(self, name: str, callback: Any) -> None:
        self.hooks.append((name, callback))
    def register_agent_runtime(self, *, descriptor: Any, factory: Any) -> None:
        self.runtimes.append((descriptor, factory))
def _tool_schema(plugin: Any) -> dict[str, Any]:
    return {"type": "function", "function": plugin.SCHEMA}
class _SyntheticAgent:
    session_id = "synthetic-v4-local-parent"
    _interrupt_requested = False
    def __init__(self, plugin: Any) -> None:
        self.valid_tool_names = frozenset({plugin.TOOL_NAME})
        self.tools = (_tool_schema(plugin),)
        self.execution_calls: list[str] = []
    def _execute_tool_calls(
        self,
        assistant_message: Any,
        turn_messages: list[dict[str, Any]],
        effective_task_id: str,
    ) -> None:
        from model_tools import handle_function_call
        call = assistant_message.tool_calls[0]
        args = json.loads(call.function.arguments)
        self.execution_calls.append(call.id)
        result = handle_function_call(
            call.function.name,
            args,
            task_id=effective_task_id,
            session_id=self.session_id,
            tool_call_id=call.id,
            turn_id=effective_task_id,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
        turn_messages.append(
            {"role": "tool", "tool_call_id": call.id, "content": result}
        )
class _RecordingHost(HermesRuntimeHostServices):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.execute_calls = 0
        super().__init__(*args, **kwargs)
    async def execute_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        request_id: str | None = None,
    ) -> Any:
        self.execute_calls += 1
        return await super().execute_tool(name, arguments, request_id=request_id)
def test_registration_is_public_namespaced_and_routing_is_disjoint() -> None:
    plugin = _fixture_module()
    context = _RecordingContext()
    plugin.register(context)
    assert len(context.tools) == 1
    assert context.tools[0]["name"] == plugin.TOOL_NAME
    assert context.tools[0]["toolset"] == plugin.TOOLSET
    assert context.tools[0]["schema"]["parameters"]["additionalProperties"] is False
    assert context.tools[0]["schema"]["parameters"]["properties"]["operation"]["enum"] == ["record"]
    assert context.hooks == [("pre_tool_call", plugin.pre_tool_call)]
    assert len(context.runtimes) == 1
    descriptor, factory = context.runtimes[0]
    assert factory is plugin.create_runtime
    assert descriptor.supports(RuntimeSelection(plugin.PROVIDER_ID, plugin.MODEL_ID, plugin.API_MODE))
    for selection in (
        RuntimeSelection("claude-agent-sdk", "claude-fable-5-1", "agent_runtime"),
        RuntimeSelection("anthropic", "claude-sonnet-4-5", "anthropic_messages"),
        RuntimeSelection("v4-local-fixture", "other-model", "agent_runtime"),
    ):
        assert not descriptor.supports(selection)
def test_preflight_rejects_open_or_caller_directed_inputs() -> None:
    plugin = _fixture_module()
    request = build_runtime_turn_request(provider=plugin.PROVIDER_ID, model=plugin.MODEL_ID,
        api_mode=plugin.API_MODE, messages=(), prompt_snapshot=plugin.PROMPT,
        tool_schemas=(_tool_schema(plugin), {"name": "arbitrary_tool"}))
    assert plugin.create_runtime().preflight(request) is not None


def test_preflight_returns_runtime_failure_for_missing_tool_parameters() -> None:
    plugin = _fixture_module()
    request = SimpleNamespace(
        selection=RuntimeSelection(plugin.PROVIDER_ID, plugin.MODEL_ID, plugin.API_MODE),
        prompt_snapshot=plugin.PROMPT,
        tool_schemas=({"name": plugin.TOOL_NAME},),
    )
    failure = plugin.create_runtime().preflight(request)
    assert failure is not None
    assert failure.code == "fixture_runtime_tool_surface_unsupported"
def test_discovered_runtime_crosses_real_host_approval_and_records_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = _fixture_module()
    home = tmp_path / "hermes-home"
    empty_bundled = tmp_path / "empty-bundled"
    installed = home / "plugins" / plugin.PLUGIN_ID
    installed.parent.mkdir(parents=True)
    empty_bundled.mkdir()
    shutil.copytree(PLUGIN_ROOT, installed)
    (home / "config.yaml").write_text(
        f"plugins:\n  enabled:\n    - {plugin.PLUGIN_ID}\n", encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(empty_bundled))
    monkeypatch.setenv("HERMES_INTERACTIVE", "1")
    override = set_hermes_home_override(home)
    manager = PluginManager(scope_key=hermes_home_key(home))
    manager._scan_entry_points = lambda: []  # type: ignore[method-assign]
    monkeypatch.setattr(plugins_mod, "_plugin_manager", manager)
    decisions = iter(("deny", "once"))
    approval_calls: list[tuple[str, str]] = []
    def approval_callback(command: str, description: str, **_: Any) -> str:
        approval_calls.append((command, description))
        return next(decisions)
    from tools.terminal_tool import set_approval_callback
    set_approval_callback(approval_callback)
    try:
        manager.discover_and_load()
        registration = manager.select_agent_runtime(
            RuntimeSelection(plugin.PROVIDER_ID, plugin.MODEL_ID, plugin.API_MODE)
        )
        assert registration is not None
        assert manager.get_agent_runtime(plugin.RUNTIME_ID) is registration
        descriptor = registration.descriptor
        runtime = registration.factory()
        agent = _SyntheticAgent(plugin)
        outcomes: list[Any] = []
        for turn_number in (1, 2):
            monkeypatch.chdir(tmp_path)
            turn_messages: list[dict[str, Any]] = []
            host = _RecordingHost(
                agent,
                task_id=f"synthetic-v4-turn-{turn_number}",
                runtime_id=plugin.RUNTIME_ID,
                turn_messages=turn_messages,
            )
            request = build_runtime_turn_request(
                provider=plugin.PROVIDER_ID,
                model=plugin.MODEL_ID,
                api_mode=plugin.API_MODE,
                messages=({"role": "user", "content": plugin.PROMPT},),
                prompt_snapshot=plugin.PROMPT,
                tool_schemas=(_tool_schema(plugin),),
                correlation_id=f"synthetic-v4-correlation-{turn_number}",
            )
            outcome = run_runtime_sync(runtime, request, host, descriptor=descriptor)
            outcomes.append((outcome, host, turn_messages))
            assert host.execute_calls == 1
            assert [event.kind.value for event in outcome.events] == (
                ["status", "failed"] if turn_number == 1 else ["status", "completed"]
            )
            assert sum(event.kind.value in {"completed", "cancelled", "failed"} for event in outcome.events) == 1
            assert len(turn_messages) == 2
            if turn_number == 1:
                assert not (tmp_path / plugin.STATE_FILE).exists()
                assert "blocked" in str(turn_messages[-1]["content"]).lower()
        denied, _, denied_messages = outcomes[0]
        assert denied.completed is False
        assert denied.failure is not None and denied.failure.code == "fixture_tool_not_approved"
        recovered, _, recovered_messages = outcomes[1]
        assert recovered.completed is True
        assert json.loads(recovered_messages[-1]["content"]) == {
            "ok": True,
            "operation": "record",
            "record_count": 1,
        }
        assert json.loads((tmp_path / plugin.STATE_FILE).read_text()) == {
            "schema_version": 1,
            "record_count": 1,
        }
        assert len(agent.execution_calls) == 2
        assert len(approval_calls) == 2
    finally:
        set_approval_callback(None)
        manager.unload(plugin.PLUGIN_ID)
        reset_hermes_home_override(override)
