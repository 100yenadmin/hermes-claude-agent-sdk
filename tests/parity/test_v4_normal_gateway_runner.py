from __future__ import annotations
from collections import deque
from pathlib import Path
import pytest
from hermes_claude_agent_sdk.parity.v4_gateway import Gateway
from hermes_claude_agent_sdk.parity.v4_live_session import V4LiveSession
from hermes_claude_agent_sdk.parity.v4_normal_gateway_runner import V4NormalGatewayRunner, V4NormalGatewayRunnerViolation
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
def _events(): return [_event("message.start"), _event("message.state"), _event("message.usage"), _event("message.complete", {"status": "completed"})]
def test_construction_is_inert_and_incompatible_identity_precedes_start(tmp_path):
    calls = []; runner = _runner(tmp_path / "home", lambda **_: calls.append(True)); assert calls == [] and not (tmp_path / "home").exists(); assert runner.admission.turn_count == 1
    bad = _candidate(); bad["sdk_version"] = "bad"
    with pytest.raises(V4NormalGatewayRunnerViolation): V4NormalGatewayRunner(candidate=bad, preflight_projections=_preflights(bad), profile_id="isolated", inventory_hash="5" * 64, hermes_home=tmp_path / "bad")
def test_fake_normal_gateway_positive_packet_and_safe_env(tmp_path, monkeypatch):
    monkeypatch.setattr(V4LiveSession, "collect_host_observation", lambda *_a, **_k: _host(1))
    monkeypatch.setattr(V4LiveSession, "collect_delegation_observation", lambda *_a, **_k: {"status": "PASS", "count": 0, "invariant_violations": [], "parent_link_sha256": None, "lifecycle": "none"})
    monkeypatch.setenv("HOME", "transient-home"); monkeypatch.setenv("CLAUDE_CONFIG_DIR", "transient-config"); monkeypatch.setenv("ANTHROPIC_API_KEY", "redacted"); monkeypatch.setenv("GLM_API_KEY", "redacted"); monkeypatch.setenv("EXTRA_USAGE", "redacted")
    transport, seen = _Transport(_events()), {}
    def factory(**kwargs): seen.update(kwargs); return Gateway(python="fake", cwd=ROOT, env=kwargs["env"], transport=transport, host_tools=kwargs["host_tools"], mcp_tools=kwargs["mcp_tools"])
    result = _runner(tmp_path / "home", factory).execute(); env = seen["env"]
    assert result["paths"]["positive"]["classification"] == "COMPLETE" and transport.sid not in repr(result)
    assert env["HOME"] == "transient-home" and env["CLAUDE_CONFIG_DIR"] == "transient-config" and env["HERMES_MODEL"] == "claude-fable-5-1" and env["HERMES_TUI_PROVIDER"] == "claude-agent-sdk"
    assert all(name not in env for name in ("ANTHROPIC_API_KEY", "GLM_API_KEY", "EXTRA_USAGE")); assert "v4_fixture_local_state" in seen["host_tools"] and "mcp__hermes-tools__v4_fixture_local_state" in seen["mcp_tools"]
def test_missing_host_observation_fails_closed_without_path_fabrication(tmp_path):
    transport = _Transport(_events())
    with pytest.raises(V4NormalGatewayRunnerViolation): _runner(tmp_path / "home", lambda **kwargs: Gateway(python="fake", cwd=ROOT, env=kwargs["env"], transport=transport, host_tools=kwargs["host_tools"], mcp_tools=kwargs["mcp_tools"])).execute()
    assert transport.calls.count("prompt.submit") == 1
def test_child_rows_fail_before_gateway_when_event_grammar_cannot_observe_lifecycle(tmp_path):
    calls = []
    with pytest.raises(V4NormalGatewayRunnerViolation, match="event grammar"):
        _runner(tmp_path / "home", lambda **_: calls.append(True), "v2_non_soak/ORCH-05").execute()
    assert calls == [] and not (tmp_path / "home").exists()
