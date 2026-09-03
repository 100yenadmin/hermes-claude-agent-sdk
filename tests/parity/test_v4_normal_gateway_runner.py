from __future__ import annotations

import inspect
from collections import deque
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity import v4_normal_gateway_runner as runner_module
from hermes_claude_agent_sdk.parity.v4_gateway import Gateway
from hermes_claude_agent_sdk.parity.v4_live_session import V4LiveSession
from hermes_claude_agent_sdk.parity.v4_normal_gateway_runner import (
    V4NormalGatewayRunner,
    V4NormalGatewayRunnerViolation,
)

from .test_v4_live_executor import _candidate, _event, _preflights
from .test_v4_live_packets import _host

ROOT = Path(__file__).parents[2]
class _Transport:
    def __init__(self, events):
        self.events, self.calls, self.started = deque([_event("gateway.ready"), *events]), [], False; self.sid = "s" + format(id(self), "x")
    def start(self): self.started = True
    def send(self, frame):
        method = frame["method"]; self.calls.append(method); result = {"session_id": self.sid, "stored_session_id": self.sid} if method == "session.create" else {}
        return {"jsonrpc": "2.0", "id": frame["id"], "result": result}
    def recv(self, _):
        if self.events: return self.events.popleft()
        raise TimeoutError
    def close(self): pass
def _runner(home, factory, row="v2_non_soak/AUTH-01"):
    return V4NormalGatewayRunner(candidate=_candidate(), preflight_projections=_preflights(_candidate()), profile_id="isolated", inventory_hash="5" * 64, hermes_home=home, contract=ROOT / "qa/parity-contract-v4.yaml", live_map=ROOT / "qa/parity-v4-live-execution-map.yaml", fixture_manifest=ROOT / "qa/parity-v4-live-fixtures.yaml", gateway_factory=factory, row_key=row, trial_index=1)
def _events(extra=()): return [_event("message.start"), _event("message.state"), *extra, _event("message.usage"), _event("message.complete", {"status": "completed"})]
def _children(transport, count, background=False):
    parent, events = "p" + format(id(transport), "x"), []
    for index in range(count):
        child = "c" + format(id(transport), "x") + str(index); payload = {"task_index": index, "task_count": count, "parent_id": parent, "child_id": child, "delegation_id": child}
        events.extend([_event("subagent.spawn_requested", payload), _event("subagent.start", payload), _event("subagent.complete", payload)])
    if background: events.append(_event("background"))
    return events
def test_construction_is_inert_and_incompatible_identity_precedes_start(tmp_path):
    calls = []; runner = _runner(tmp_path / "home", lambda **_: calls.append(True)); assert calls == [] and not (tmp_path / "home").exists(); assert runner.admission.turn_count == 1
    bad = _candidate(); bad["sdk_version"] = "bad"
    with pytest.raises(V4NormalGatewayRunnerViolation): V4NormalGatewayRunner(candidate=bad, preflight_projections=_preflights(bad), profile_id="isolated", inventory_hash="5" * 64, hermes_home=tmp_path / "bad")


def test_stage_plugin_excludes_transient_python_bytecode(tmp_path):
    source = tmp_path / "source-plugin"
    source.mkdir()
    (source / "__init__.py").write_text("PLUGIN_API_VERSION = 1\n", encoding="utf-8")
    (source / "plugin.yaml").write_text("name: fixture\n", encoding="utf-8")
    cache = source / "__pycache__"
    cache.mkdir()
    (cache / "__init__.cpython-313.pyc").write_bytes(b"transient")

    home = tmp_path / "home"
    runner_module._stage_plugin(source, home)

    staged = home / "plugins" / "v4_hermes_fixture"
    assert (staged / "__init__.py").read_text(encoding="utf-8") == (
        "PLUGIN_API_VERSION = 1\n"
    )
    assert (staged / "plugin.yaml").read_text(encoding="utf-8") == "name: fixture\n"
    assert not (staged / "__pycache__").exists()


def test_fake_normal_gateway_positive_packet_and_safe_env(tmp_path, monkeypatch):
    monkeypatch.setattr(V4LiveSession, "collect_host_observation", lambda *_a, **_k: _host(1))
    monkeypatch.setattr(V4LiveSession, "collect_delegation_observation", lambda *_a, **_k: {"status": "PASS", "count": 0, "background_count": 0, "invariant_violations": [], "parent_link_sha256": None, "lifecycle": "none"})
    transient_home = tmp_path / "transient-home"
    transient_config = tmp_path / "transient-config"
    monkeypatch.setenv("HOME", str(transient_home)); monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(transient_config)); monkeypatch.setenv("ANTHROPIC_API_KEY", "redacted"); monkeypatch.setenv("GLM_API_KEY", "redacted"); monkeypatch.setenv("EXTRA_USAGE", "redacted")
    transport, seen = _Transport(_events()), {}
    def factory(**kwargs): seen.update(kwargs); return Gateway(python="fake", cwd=ROOT, env=kwargs["env"], transport=transport, host_tools=kwargs["host_tools"], mcp_tools=kwargs["mcp_tools"])
    result = _runner(tmp_path / "home", factory).execute(); env = seen["env"]
    assert result["paths"]["positive"]["classification"] == "COMPLETE" and transport.sid not in repr(result)
    assert env["HOME"] == str(transient_home) and env["CLAUDE_CONFIG_DIR"] == str(transient_config) and env["HERMES_MODEL"] == "claude-fable-5-1" and env["HERMES_TUI_PROVIDER"] == "claude-agent-sdk"
    assert all(name not in env for name in ("ANTHROPIC_API_KEY", "GLM_API_KEY", "EXTRA_USAGE")); assert "v4_fixture_local_state" in seen["host_tools"] and "mcp__hermes-tools__v4_fixture_local_state" in seen["mcp_tools"]


def test_safe_env_strips_python_startup_overrides(tmp_path, monkeypatch):
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONEXECUTABLE"):
        monkeypatch.setenv(name, f"/ambient/{name.casefold()}")
    environment = runner_module._safe_env(tmp_path / "home")
    assert all(name not in environment for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONEXECUTABLE"))
def test_missing_host_observation_fails_closed_without_path_fabrication(tmp_path):
    transport = _Transport(_events())
    with pytest.raises(V4NormalGatewayRunnerViolation): _runner(tmp_path / "home", lambda **kwargs: Gateway(python="fake", cwd=ROOT, env=kwargs["env"], transport=transport, host_tools=kwargs["host_tools"], mcp_tools=kwargs["mcp_tools"])).execute()
    assert transport.calls.count("prompt.submit") == 1
def test_sync_child_uses_observed_lifecycle_not_durable_batch_count(tmp_path, monkeypatch):
    monkeypatch.setattr(V4LiveSession, "collect_host_observation", lambda *_a, **_k: _host(1)); monkeypatch.setattr(V4LiveSession, "collect_delegation_observation", lambda *_a, **_k: {"status": "PASS", "count": 0, "background_count": 0, "invariant_violations": [], "parent_link_sha256": None, "lifecycle": "none"})
    transport = _Transport(_events(_children(None, 1)))
    result = _runner(tmp_path / "home", lambda **k: Gateway(python="fake", cwd=ROOT, env=k["env"], transport=transport, host_tools=k["host_tools"], mcp_tools=k["mcp_tools"]), "v2_non_soak/ORCH-01").execute()
    assert result["scenario_receipt"]["delegation_summary"]["count"] == 1 and result["scenario_receipt"]["delegation_summary"]["background_count"] == 0
def test_two_child_fanout_uses_observed_ordinals(tmp_path, monkeypatch):
    monkeypatch.setattr(V4LiveSession, "collect_host_observation", lambda *_a, **_k: _host(1)); monkeypatch.setattr(V4LiveSession, "collect_delegation_observation", lambda *_a, **_k: {"status": "PASS", "count": 0, "background_count": 0, "invariant_violations": [], "parent_link_sha256": None, "lifecycle": "none"})
    transport = _Transport(_events(_children(None, 2)))
    result = _runner(tmp_path / "home", lambda **k: Gateway(python="fake", cwd=ROOT, env=k["env"], transport=transport, host_tools=k["host_tools"], mcp_tools=k["mcp_tools"]), "v2_non_soak/ORCH-05").execute()
    assert result["scenario_receipt"]["delegation_summary"]["count"] == 2
def test_background_batch_count_mismatch_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(V4LiveSession, "collect_host_observation", lambda *_a, **_k: _host(1)); monkeypatch.setattr(V4LiveSession, "collect_delegation_observation", lambda *_a, **_k: {"status": "PASS", "count": 2, "background_count": 2, "invariant_violations": [], "parent_link_sha256": "a" * 64, "lifecycle": "completed"})
    transport = _Transport(_events(_children(None, 1, True)))
    with pytest.raises(V4NormalGatewayRunnerViolation): _runner(tmp_path / "home", lambda **k: Gateway(python="fake", cwd=ROOT, env=k["env"], transport=transport, host_tools=k["host_tools"], mcp_tools=k["mcp_tools"]), "v2_non_soak/BG-01").execute()


def test_local_observations_are_selected_internally_by_immutable_row(tmp_path, monkeypatch):
    runner = _runner(tmp_path / "home", lambda **_: None, "openclaw_active/config-restart-capability-flip")
    scenario = next(item for item in runner._catalog.scenarios if item.row_key == "openclaw_active/config-restart-capability-flip")
    calls = []

    def sealed_executor(**kwargs):
        assert Path(kwargs["task_root"]).is_dir()
        calls.append({key: value for key, value in kwargs.items() if key != "task_root"})
        return {"path": kwargs["path"]}

    monkeypatch.setitem(runner_module._LOCAL_EXECUTORS, scenario.row_key, sealed_executor)
    observations = runner_module._local_observations(scenario, 1, tmp_path)
    assert observations == {"denial": {"path": "denial"}, "recovery": {"path": "recovery"}}
    assert calls == [
        {"row_key": scenario.row_key, "trial_index": 1, "path": "denial"},
        {"row_key": scenario.row_key, "trial_index": 1, "path": "recovery"},
    ]


def test_unmapped_local_row_stays_pending_and_caller_cannot_inject_observations(tmp_path):
    runner = _runner(tmp_path / "home", lambda **_: None, "clawprobench_native/constraints_22_message_audience_boundary_live")
    scenario = next(item for item in runner._catalog.scenarios if item.row_key == "clawprobench_native/constraints_22_message_audience_boundary_live")
    assert runner_module._local_observations(scenario, 1, tmp_path) == {}
    assert "sealed_local_observation" not in inspect.signature(V4NormalGatewayRunner.execute).parameters
    with pytest.raises(TypeError):
        runner.execute(sealed_local_observation={"denial": {}})
