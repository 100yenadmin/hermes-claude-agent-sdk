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
    assert plugin.TOOL_NAME == "v4_fixture_local_state"
    assert f"mcp__hermes-tools__{plugin.TOOL_NAME}" == "mcp__hermes-tools__v4_fixture_local_state"
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


def test_native_read_write_canary_fences_real_host_tools(tmp_path, monkeypatch):
    plugin = _fixture_module()
    root = tmp_path / "native"
    root.mkdir()
    monkeypatch.setenv("HERMES_V4_NATIVE_FIXTURE_ROOT", str(root))
    assert plugin.pre_tool_call("read_file", {"path": str(root / "audience_request.json")}) is None
    assert plugin.pre_tool_call("write_file", {"path": "audience_boundary.json", "content": "{}"}) is None
    for name, args in (
        ("write_file", {"path": "audience_request.json"}),
        ("read_file", {"path": "../outside"}),
        ("terminal", {"command": "true"}),
        ("delegate_task", {"goal": "anything"}),
        ("write_file", {"path": "subdir/other.json"}),
    ):
        assert plugin.pre_tool_call(name, args)["action"] == "block"
    (root / "link").symlink_to(tmp_path)
    assert plugin.pre_tool_call("read_file", {"path": "link/outside"})["action"] == "block"
    assert not (root / "audience_boundary.json").exists()


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


@pytest.mark.parametrize("name,args,allowed", [
    ("browser_navigate", {"url": "http://127.0.0.1:41731/dashboard"}, True),
    ("browser_snapshot", {"full": True}, True),
    ("browser_console", {}, True),
    ("browser_console", {"expression": "document.body.textContent"}, True),
    ("browser_navigate", {"url": "http://127.0.0.1:41732/dashboard"}, False),
    ("browser_navigate", {"url": "https://example.com"}, False),
    ("browser_console", {"expression": "fetch('/mutation')"}, False),
    ("browser_console", {"clear": True}, False),
    ("browser_click", {"ref": "e1"}, False),
    ("browser_vision", {"question": "describe"}, False),
])
def test_native_browser_uses_owned_url_and_read_only_calls(tmp_path, monkeypatch, name, args, allowed):
    plugin = _fixture_module()
    monkeypatch.setenv("HERMES_V4_NATIVE_FIXTURE_ROOT", str(tmp_path))
    monkeypatch.setenv("HERMES_V4_NATIVE_BROWSER_URL", "http://127.0.0.1:41731/dashboard")
    result = plugin.pre_tool_call(name, args)
    assert (result is None) is allowed
    if not allowed:
        assert result["action"] == "block"
    # Enabling browser reads never expands the file-write fence.
    assert plugin.pre_tool_call("write_file", {"path": "input.json"})["action"] == "block"


@pytest.mark.parametrize("url", ["", "http://localhost:41731/dashboard", "http://127.0.0.1:bad/dashboard",
                                   "http://127.0.0.1:41731/elsewhere", "http://user@127.0.0.1:41731/dashboard"])
def test_native_browser_requires_valid_operator_url(tmp_path, monkeypatch, url):
    plugin = _fixture_module()
    monkeypatch.setenv("HERMES_V4_NATIVE_FIXTURE_ROOT", str(tmp_path))
    monkeypatch.setenv("HERMES_V4_NATIVE_BROWSER_URL", url)
    assert plugin.pre_tool_call("browser_navigate", {"url": url})["action"] == "block"


@pytest.mark.parametrize("name,args,allowed", [
    ("skills_list", {}, True),
    ("skills_list", {"category": None}, True),
    ("skill_view", {"name": "weather"}, True),
    ("skill_view", {"name": "slack", "file_path": None}, True),
    ("skills_list", {"category": "unrelated"}, False),
    ("skill_view", {"name": "unrelated"}, False),
    ("skill_view", {"name": "../weather"}, False),
    ("skill_view", {"name": "weather", "file_path": "../../outside"}, False),
    ("skill_view", {"name": "weather", "preprocess": True}, False),
    ("skill_manage", {"action": "delete", "name": "weather"}, False),
    ("terminal", {"command": "true"}, False),
])
def test_native_skills_allow_only_isolated_readiness_reads(tmp_path, monkeypatch, name, args, allowed):
    plugin = _fixture_module()
    monkeypatch.setenv("HERMES_V4_NATIVE_FIXTURE_ROOT", str(tmp_path))
    monkeypatch.setenv("HERMES_V4_NATIVE_SKILLS", "isolated-readiness-v1")
    result = plugin.pre_tool_call(name, args)
    assert (result is None) is allowed
    if not allowed:
        assert result["action"] == "block"
    assert plugin.pre_tool_call("write_file", {"path": "input.json"})["action"] == "block"
    monkeypatch.delenv("HERMES_V4_NATIVE_SKILLS")
    assert plugin.pre_tool_call("skills_list", {})["action"] == "block"
    assert plugin.pre_tool_call("skill_view", {"name": "weather"})["action"] == "block"


def _oneshot_fixture(tmp_path, monkeypatch):
    home, root = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    root.mkdir()
    (root / "request.json").write_text(json.dumps({"allowed_window": {
        "start": "2099-01-02T08:30:00", "end": "2099-01-02T12:00:00",
    }}))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_V4_NATIVE_FIXTURE_ROOT", str(root))
    monkeypatch.setenv("HERMES_V4_NATIVE_CRON", "isolated-oneshot-v1")
    return home, root, {"action": "create", "schedule": "2099-01-02T09:57:00", "repeat": 1,
                        "prompt": "remind me to run the smoke check", "deliver": "local"}


def test_native_oneshot_store_admission_is_local_bounded_and_opt_in(tmp_path, monkeypatch):
    plugin = _fixture_module()
    home, root, args = _oneshot_fixture(tmp_path, monkeypatch)
    assert plugin.pre_tool_call("cronjob_manage", args) is None
    assert plugin.pre_tool_call("cronjob_manage", {"action": "list"}) is None
    assert not (home / "cron").exists()  # Guard does not implement the cron tool.
    assert plugin.pre_tool_call("write_file", {"path": "request.json"})["action"] == "block"
    assert plugin.pre_tool_call("terminal", {"command": "true"})["action"] == "block"
    (home / "cron").mkdir()
    (home / "cron/jobs.json").write_text('{"jobs": []}')
    assert plugin.pre_tool_call("cronjob_manage", args)["action"] == "block"
    assert plugin.pre_tool_call("cronjob_manage", {"action": "list", "include_disabled": True}) is None
    monkeypatch.delenv("HERMES_V4_NATIVE_CRON")
    assert plugin.pre_tool_call("cronjob_manage", {"action": "list"})["action"] == "block"


@pytest.mark.parametrize("changed", [
    {"action": "run"}, {"action": "remove"}, {"action": "update"},
    {"deliver": "origin"}, {"deliver": "all"}, {"failure_deliver": "all"},
    {"repeat": None}, {"repeat": True}, {"repeat": 2},
    {"prompt": "different task"}, {"schedule": "every hour"},
    {"schedule": "57 9 2 1 *"}, {"schedule": "2099-01-02T12:00:00"},
    {"schedule": "2099-01-02T09:57:00Z"}, {"schedule": "2000-01-02T09:57:00"},
    {"provider": "anything"}, {"model": "anything"}, {"base_url": "http://127.0.0.1"},
    {"script": "anything"}, {"monitor": "anything"}, {"no_agent": True},
    {"workdir": "/"}, {"context_from": ["other"]},
])
def test_native_oneshot_store_rejects_other_effects(tmp_path, monkeypatch, changed):
    plugin = _fixture_module()
    home, root, args = _oneshot_fixture(tmp_path, monkeypatch)
    assert plugin.pre_tool_call("cronjob_manage", dict(args, **changed))["action"] == "block"
    assert not (home / "cron").exists()


def test_native_oneshot_store_rejects_ambiguous_home_and_symlink_inputs(tmp_path, monkeypatch):
    plugin = _fixture_module()
    home, root, args = _oneshot_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(root))
    assert plugin.pre_tool_call("cronjob_manage", args)["action"] == "block"
    monkeypatch.setenv("HERMES_HOME", str(home))
    (root / "original.json").write_text((root / "request.json").read_text())
    (root / "request.json").unlink()
    (root / "request.json").symlink_to(root / "original.json")
    assert plugin.pre_tool_call("cronjob_manage", args)["action"] == "block"
    assert plugin.pre_tool_call("cronjob_manage", {"action": "list", "include_disabled": "yes"})["action"] == "block"


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


def test_real_hermes_discovery_handle_call_denial_recovery_and_unload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_root_value = os.environ.get("HERMES_AGENT_HOST_ROOT")
    if not host_root_value or not Path(host_root_value).is_dir():
        pytest.skip("HERMES_AGENT_HOST_ROOT is not configured")
    host_root = Path(host_root_value)
    if str(host_root) not in sys.path:
        sys.path.insert(0, str(host_root))
    from hermes_constants import hermes_home_key, reset_hermes_home_override, set_hermes_home_override
    from hermes_cli import plugins as plugins_mod
    from hermes_cli.plugins import PluginManager
    from model_tools import handle_function_call
    from tools import approval
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
    manager = PluginManager(scope_key=hermes_home_key(home))
    manager.discover_and_load()
    loaded = manager._plugins[plugin.PLUGIN_ID]
    assert loaded.enabled is True
    assert registry.get_entry(plugin.TOOL_NAME, scope=manager.scope_key) is not None

    monkeypatch.setattr(plugins_mod, "_plugin_manager", manager)
    approval_calls: list[dict[str, Any]] = []
    decisions = iter((False, True))

    def fake_request_tool_approval(tool_name: str, reason: str, **kwargs: Any) -> dict[str, Any]:
        from tools.approval import _approval_tool_call_id, _approval_turn_id

        approval_calls.append(
            {
                "tool_name": tool_name,
                "has_reason": bool(reason),
                "has_rule_key": bool(kwargs.get("rule_key")),
                "turn_context_bound": _approval_turn_id.get() == "synthetic-turn",
                "tool_context_bound": _approval_tool_call_id.get() == "synthetic-call",
            }
        )
        approved = next(decisions)
        return {"approved": approved, "message": "synthetic host approval denied" if not approved else None}

    monkeypatch.setattr(approval, "request_tool_approval", fake_request_tool_approval)
    args = _args(tmp_path, operation="record")
    def host_call() -> dict[str, Any]:
        return json.loads(handle_function_call(
            plugin.TOOL_NAME, args, task_id="synthetic-task",
            tool_call_id="synthetic-call", turn_id="synthetic-turn",
            skip_tool_request_middleware=True, skip_tool_execution_middleware=True,
        ))

    override = set_hermes_home_override(home)
    try:
        denied = host_call()
        assert denied["error"] == "synthetic host approval denied"
        assert not (tmp_path / plugin.STATE_FILE).exists()

        result = host_call()
        assert result["ok"] is True
        assert result["record_count"] == 1
        assert (tmp_path / plugin.STATE_FILE).exists()
        assert approval_calls == [{
            "tool_name": plugin.TOOL_NAME, "has_reason": True, "has_rule_key": True,
            "turn_context_bound": True, "tool_context_bound": True,
        }] * 2
    finally:
        reset_hermes_home_override(override)

    assert manager.unload(plugin.PLUGIN_ID) is True
    assert registry.get_entry(plugin.TOOL_NAME, scope=manager.scope_key) is None
    assert manager.has_hook("pre_tool_call") is False
