"""Real AIAgent delegate_task proof with a provider-free runtime fixture."""
from __future__ import annotations

import importlib.util
import json
import os
import queue
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_HOST_ROOT_VALUE = os.environ.get("HERMES_AGENT_HOST_ROOT")
if not _HOST_ROOT_VALUE or not Path(_HOST_ROOT_VALUE).is_dir():
    pytest.skip("HERMES_AGENT_HOST_ROOT is not configured", allow_module_level=True)
HOST_ROOT = Path(_HOST_ROOT_VALUE).resolve()
if str(HOST_ROOT) not in sys.path:
    sys.path.insert(0, str(HOST_ROOT))

from hermes_constants import (  # noqa: E402
    hermes_home_key,
    reset_hermes_home_override,
    set_hermes_home_override,
)
from hermes_state import SessionDB  # noqa: E402
from hermes_cli import plugins as plugins_mod  # noqa: E402
from hermes_cli.plugins import PluginManager  # noqa: E402
from run_agent import AIAgent  # noqa: E402
from tools import async_delegation  # noqa: E402
from tools.process_registry import process_registry  # noqa: E402

PLUGIN_ROOT = Path(__file__).parents[1] / "fixtures" / "v4_delegation_runtime_plugin"


def _fixture_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "v4_delegation_runtime_plugin_test",
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plugin: Any):
    home = tmp_path / "hermes-home"
    bundled = tmp_path / "empty-bundled"
    installed = home / "plugins" / plugin.PLUGIN_ID
    installed.parent.mkdir(parents=True)
    bundled.mkdir()
    shutil.copytree(PLUGIN_ROOT, installed)
    (home / "config.yaml").write_text(
        f"plugins:\n  enabled:\n    - {plugin.PLUGIN_ID}\n"
        "delegation:\n  max_iterations: 1\n  max_spawn_depth: 1\n"
        "auxiliary:\n  title_generation:\n    enabled: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled))
    monkeypatch.setenv("HERMES_INTERACTIVE", "0")
    override = set_hermes_home_override(home)
    manager = PluginManager(scope_key=hermes_home_key(home))
    manager._scan_entry_points = lambda: []  # type: ignore[method-assign]
    monkeypatch.setattr(plugins_mod, "_plugin_manager", manager)
    manager.discover_and_load()
    return home, manager, override


def _session_rows(db_path: Path, parent_id: str) -> list[tuple[str, str | None, str]]:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT id, parent_session_id, source FROM sessions "
            "WHERE id = ? OR parent_session_id = ? ORDER BY id",
            (parent_id, parent_id),
        ).fetchall()


def _await_batch(parent_id: str, expected_children: int) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + 30
    event: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            candidate = process_registry.completion_queue.get(timeout=0.25)
        except queue.Empty:
            continue
        if candidate.get("type") != "async_delegation":
            continue
        if candidate.get("parent_session_id") != parent_id:
            continue
        event = candidate
        break
    assert event is not None, "provider-free delegation did not emit a completion event"
    assert event.get("is_batch") is True
    assert len(event.get("results") or []) == expected_children
    assert all(result.get("status") == "completed" for result in event["results"])
    assert all(result.get("summary") for result in event["results"])
    assert all(result.get("api_calls") == 0 for result in event["results"])
    assert all(result.get("model") == "v4-provider-free-delegation-v1" for result in event["results"])
    with sqlite3.connect(Path(os.environ["HERMES_HOME"]) / "state.db") as conn:
        row = conn.execute(
            "SELECT state, delivery_state, result_json FROM async_delegations "
            "WHERE delegation_id = ?",
            (event["delegation_id"],),
        ).fetchone()
    assert row is not None
    state, delivery_state, result_json = row
    assert state == "completed"
    assert delivery_state in {"pending", "delivered"}
    durable = json.loads(result_json)
    assert len(durable["results"]) == expected_children
    return event, {"state": state, "delivery_state": delivery_state, "result": durable}


def _run_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plugin: Any,
    prompt: str,
) -> tuple[dict[str, Any], list[tuple[str, str | None, str]], dict[str, Any]]:
    home, manager, override = _install_fixture(tmp_path, monkeypatch, plugin)
    from agent.title_generator import _auto_title_enabled

    assert _auto_title_enabled() is False
    db = SessionDB(home / "state.db")
    parent_id = f"v4-parent-{prompt.rsplit(' ', 1)[-1]}"
    client_builds: list[str] = []
    def forbidden_client(self: Any, *args: Any, **kwargs: Any):
        client_builds.append(str(getattr(self, "session_id", "")))
        raise AssertionError("provider client construction is forbidden")

    monkeypatch.setattr(AIAgent, "_create_openai_client", forbidden_client)
    parent = AIAgent(
        provider=plugin.PROVIDER_ID,
        model=plugin.MODEL_ID,
        api_mode=plugin.API_MODE,
        session_id=parent_id,
        enabled_toolsets=["delegation"],
        skip_context_files=True,
        skip_memory=True,
        skip_background_review=True,
        session_db=db,
        quiet_mode=True,
    )
    assert parent.client is None
    assert parent.api_key == ""
    assert client_builds == []
    try:
        first = parent.run_conversation(prompt, task_id=f"{parent_id}-turn-1")
        assert first["completed"] is True
        assert first["api_calls"] == 0
        assert first["final_response"]
        assert async_delegation.active_count() in {0, 1}
        event, durable = _await_batch(parent_id, 1 if "one" in prompt else 2)
        rows = _session_rows(home / "state.db", parent_id)
        parent_row = next(row for row in rows if row[0] == parent_id)
        assert parent_row[1:] == (None, "cli")
        children = [row for row in rows if row[1] == parent_id]
        assert len(children) == (1 if "one" in prompt else 2)
        followup = parent.run_conversation("v4 fixture fixed parent follow-up", task_id=f"{parent_id}-turn-2")
        assert followup["completed"] is True
        assert followup["api_calls"] == 0
        assert "follow-up" in followup["final_response"]
        assert client_builds == []
        assert _session_rows(home / "state.db", parent_id) == rows
        return event, rows, durable
    finally:
        parent.close()
        db.close()
        manager.unload(plugin.PLUGIN_ID)
        reset_hermes_home_override(override)


def test_descriptor_is_reserved_and_disjoint_from_claude_fable() -> None:
    plugin = _fixture_module()
    descriptor = plugin.build_runtime_descriptor()
    assert descriptor.supports(plugin.RuntimeSelection(plugin.PROVIDER_ID, plugin.MODEL_ID, plugin.API_MODE))
    for selection in (
        plugin.RuntimeSelection("claude-agent-sdk", "claude-fable-5-1", "agent_runtime"),
        plugin.RuntimeSelection("anthropic", "claude-sonnet-4-5", "anthropic_messages"),
        plugin.RuntimeSelection(plugin.PROVIDER_ID, "claude-fable-5-1", "agent_runtime"),
    ):
        assert not descriptor.supports(selection)


def test_real_aiaagent_single_child_is_provider_free_and_non_recursive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = _fixture_module()
    event, rows, durable = _run_parent(tmp_path, monkeypatch, plugin, plugin.ONE_PARENT_PROMPT)
    assert event["results"][0]["summary"] == "v4 fixture child completed"
    assert len([row for row in rows if row[1]]) == 1
    assert durable["state"] == "completed"


def test_real_aiaagent_fanout_is_one_batch_with_two_provider_free_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = _fixture_module()
    event, rows, durable = _run_parent(tmp_path, monkeypatch, plugin, plugin.FANOUT_PARENT_PROMPT)
    assert event["goals"] == list(plugin.FANOUT_CHILD_GOALS)
    assert len(event["results"]) == 2
    assert len([row for row in rows if row[1]]) == 2
    with sqlite3.connect(Path(os.environ["HERMES_HOME"]) / "state.db") as conn:
        count = conn.execute("SELECT COUNT(*) FROM async_delegations").fetchone()[0]
    assert count == 1
    assert durable["delivery_state"] in {"pending", "delivered"}
