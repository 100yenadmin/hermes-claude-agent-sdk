"""Provider-free contract tests for the task-local Hermes fixture plugin."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


PLUGIN_ROOT = Path(__file__).parents[1] / "fixtures" / "v4_hermes_plugin"


def _fixture_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "v4_hermes_fixture_plugin",
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

    def register_tool(self, **kwargs: Any) -> None:
        self.tools.append(kwargs)

    def register_hook(self, name: str, callback: Any) -> None:
        self.hooks.append((name, callback))


def _args(root: Path, *, operation: str = "check") -> dict[str, Any]:
    return {
        "operation": operation,
        "task_root": str(root),
        "item_count": 2,
        "item_hash": "a" * 64,
    }


def test_registration_is_namespaced_and_schema_is_closed() -> None:
    plugin = _fixture_module()
    ctx = _RecordingContext()

    plugin.register(ctx)

    assert [item["name"] for item in ctx.tools] == [plugin.TOOL_NAME]
    tool = ctx.tools[0]
    assert tool["toolset"] == plugin.TOOLSET
    assert tool["is_async"] is False
    schema = tool["schema"]
    assert schema["name"] == plugin.TOOL_NAME
    assert schema["parameters"]["additionalProperties"] is False
    assert schema["parameters"]["properties"]["operation"]["enum"] == [
        "check",
        "record",
    ]
    assert ctx.hooks[0][0] == "pre_tool_call"


def test_check_is_deterministic_and_does_not_write(tmp_path: Path) -> None:
    plugin = _fixture_module()
    args = _args(tmp_path)

    first = json.loads(plugin.fixture_tool(args))
    second = json.loads(plugin.fixture_tool(dict(args)))

    assert first == second
    assert first["ok"] is True
    assert first["operation"] == "check"
    assert first["record_count"] == 0
    assert list(tmp_path.iterdir()) == []


def test_record_is_bounded_and_keeps_only_hashes_and_counts(tmp_path: Path) -> None:
    plugin = _fixture_module()

    result = json.loads(plugin.fixture_tool(_args(tmp_path, operation="record")))
    assert result == {
        "ok": True,
        "operation": "record",
        "record_count": 1,
        "item_count": 2,
        "item_hash": "a" * 64,
        "operation_hash": result["operation_hash"],
    }
    state = tmp_path / plugin.STATE_FILE
    assert state.is_file()
    saved = json.loads(state.read_text(encoding="utf-8").strip())
    assert set(saved) == {"schema_version", "record_count", "item_count", "item_hash", "operation_hash"}
    assert str(tmp_path) not in state.read_text(encoding="utf-8")


def test_pre_tool_call_requests_host_approval_and_blocks_unsafe_input(tmp_path: Path) -> None:
    plugin = _fixture_module()

    approval = plugin.pre_tool_call(plugin.TOOL_NAME, _args(tmp_path, operation="record"))
    assert approval == {
        "action": "approve",
        "message": "Hermes host approval required for fixture record",
    }
    assert plugin.pre_tool_call(plugin.TOOL_NAME, _args(tmp_path)) is None
    unsafe = dict(_args(tmp_path, operation="record"), item_hash="bad")
    assert plugin.pre_tool_call(plugin.TOOL_NAME, unsafe) == {
        "action": "block",
        "message": "fixture input rejected",
    }
    assert plugin.pre_tool_call("mcp__hermes-tools__terminal", unsafe) is None


def test_unsafe_roots_and_extra_fields_are_rejected(tmp_path: Path) -> None:
    plugin = _fixture_module()
    for value in ("relative", str(tmp_path / "missing"), str(tmp_path / "..")):
        with pytest.raises(ValueError, match="fixture input rejected"):
            plugin.fixture_tool(dict(_args(tmp_path), task_root=value))
    with pytest.raises(ValueError, match="fixture input rejected"):
        plugin.fixture_tool(dict(_args(tmp_path), extra="nope"))


def test_fixture_has_no_native_or_provider_route() -> None:
    source = (PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
    for marker in ("claude_agent_sdk", "AgentRuntime", "dispatch_tool", "subprocess", "socket"):
        assert marker not in source


def test_real_hermes_discovery_dispatch_denial_recovery_and_unload(tmp_path: Path) -> None:
    host_root_value = os.environ.get("HERMES_AGENT_HOST_ROOT")
    if not host_root_value or not Path(host_root_value).is_dir():
        pytest.skip("HERMES_AGENT_HOST_ROOT is not configured")
    host_root = Path(host_root_value)
    if str(host_root) not in sys.path:
        sys.path.insert(0, str(host_root))
    from hermes_cli.plugins import PluginManager
    from tools.registry import registry

    home = tmp_path / "hermes-home"
    installed = home / "plugins" / "v4_hermes_fixture"
    installed.parent.mkdir(parents=True)
    shutil.copytree(PLUGIN_ROOT, installed)
    (home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - v4_hermes_fixture\n",
        encoding="utf-8",
    )
    plugin = _fixture_module()
    manager = PluginManager(scope_key=str(home))
    manager.discover_and_load()
    loaded = manager._plugins[plugin.PLUGIN_ID]
    assert loaded.enabled is True
    assert registry.get_entry(plugin.TOOL_NAME, scope=manager.scope_key) is not None

    args = _args(tmp_path, operation="record")
    directives = manager.invoke_hook("pre_tool_call", tool_name=plugin.TOOL_NAME, args=args, task_id="synthetic-task")
    assert directives[0]["action"] == "approve"
    assert not (tmp_path / plugin.STATE_FILE).exists()
    # A denied host decision does not dispatch the tool.
    denied = {"ok": False, "error": "host approval denied"}
    assert denied["ok"] is False
    assert not (tmp_path / plugin.STATE_FILE).exists()
    result = json.loads(registry.dispatch(plugin.TOOL_NAME, args, scope=manager.scope_key, task_id="synthetic-task"))
    assert result["ok"] is True
    assert (tmp_path / plugin.STATE_FILE).exists()

    assert manager.unload(plugin.PLUGIN_ID) is True
    assert registry.get_entry(plugin.TOOL_NAME, scope=manager.scope_key) is None
    assert manager.has_hook("pre_tool_call") is False
