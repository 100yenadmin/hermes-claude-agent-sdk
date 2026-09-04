from __future__ import annotations
from collections import deque
from pathlib import Path
from typing import Any
import pytest
from hermes_claude_agent_sdk.parity.v4_gateway import Gateway, OpaqueHandle
from hermes_claude_agent_sdk.parity.v4_live_map import load_v4_live_execution_map
from hermes_claude_agent_sdk.parity.v4_live_session import V4LiveSession, V4LiveSessionViolation
from .test_v4_host_probe import _db, _delegation_db
from .test_v4_live_executor import _candidate, _event, _preflights

ROOT = Path(__file__).parents[2]
MAP_PATH = ROOT / "qa" / "parity-v4-live-execution-map.yaml"
LIVE_SESSION_ID = "live-session-fixture"
STORED_SESSION_ID = "stored-session-fixture"
class _SessionTransport:
    def __init__(self, *, resume: bool = False, stored_session_id: str = STORED_SESSION_ID) -> None:
        self.resume = resume
        self.stored_session_id = stored_session_id
        self._events = deque([_event("gateway.ready")])
        self._turn = 0
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.started = False
        self.closed = False
    def start(self) -> None:
        self.started = True
    def send(self, frame: dict[str, object]) -> dict[str, object]:
        method = frame["method"]
        params = frame.get("params", {})
        assert isinstance(method, str) and isinstance(params, dict)
        self.calls.append((method, dict(params)))
        if method == "session.create":
            result = {"session_id": LIVE_SESSION_ID, "stored_session_id": self.stored_session_id}
        elif method == "session.resume":
            result = {"session_id": "resumed-live-session", "stored_session_id": self.stored_session_id}
        else:
            result = {"status": "streaming"}
            if method == "prompt.submit":
                self._turn += 1
                self._events.extend([_event("message.start", {"session_id": self._live_id()}), _event("message.complete", {"status": "completed", "session_id": self._live_id()})])
        return {"jsonrpc": "2.0", "id": frame["id"], "result": result}
    def _live_id(self) -> str:
        return "resumed-live-session" if self.resume else LIVE_SESSION_ID
    def recv(self, _: float) -> dict[str, object]:
        if self._events:
            return self._events.popleft()
        raise TimeoutError
    def close(self) -> None:
        self.closed = True


class _AsyncDelegationTransport(_SessionTransport):
    def send(self, frame: dict[str, object]) -> dict[str, object]:
        method = frame["method"]
        params = frame.get("params", {})
        assert isinstance(method, str) and isinstance(params, dict)
        self.calls.append((method, dict(params)))
        if method == "session.create":
            result = {"session_id": LIVE_SESSION_ID, "stored_session_id": self.stored_session_id}
        else:
            result = {"status": "streaming"}
            if method == "prompt.submit":
                self._turn += 1
                self._events.extend([
                    _event("message.start", {"session_id": self._live_id()}),
                    _event("subagent.start", {"task_index": 0, "task_count": 1}),
                    _event("message.complete", {"status": "completed", "session_id": self._live_id()}),
                    _event("subagent.complete", {"task_index": 0, "task_count": 1}),
                    _event("message.start", {"session_id": self._live_id()}),
                    _event("session.usage", {"session_id": self._live_id()}),
                    _event("message.complete", {"status": "completed", "session_id": self._live_id()}),
                ])
        return {"jsonrpc": "2.0", "id": frame["id"], "result": result}

def _session(transport: _SessionTransport, **kwargs: Any) -> V4LiveSession:
    return V4LiveSession(
        gateway=Gateway(python="fake-python", cwd=ROOT, env={}, transport=transport),
        candidate=_candidate(),
        preflight_projections=_preflights(_candidate()),
        live_map=load_v4_live_execution_map(MAP_PATH),
        map_path=MAP_PATH,
        planned_calls=kwargs.pop("planned_calls", 1),
        planned_turns=kwargs.pop("planned_turns", 1),
        **kwargs,
    )

def _run_turn(session: V4LiveSession, prompt: str, item_id: str, trial_index: int = 1, source_pack: str = "v2_non_soak", path: str = "positive") -> dict[str, Any]:
    return session.run_turn(
        prompt,
        source_pack=source_pack,
        source_item_id=item_id,
        path=path,
        trial_index=trial_index,
        approval_choice="deny",
    )

def test_live_session_reuses_one_gateway_for_two_turns_in_one_row_trial() -> None:
    transport = _SessionTransport()
    session = _session(transport, planned_calls=2, planned_turns=2)
    identity = session.start()
    assert isinstance(identity["live_handle"], OpaqueHandle)
    assert isinstance(identity["stored_handle"], OpaqueHandle)
    assert LIVE_SESSION_ID not in repr(identity)
    assert STORED_SESSION_ID not in repr(identity)
    first = _run_turn(session, "first fixture prompt", "source-docs-discovery-report", source_pack="openclaw_active")
    second = _run_turn(session, "second fixture prompt", "source-docs-discovery-report", source_pack="openclaw_active")
    assert transport.started and not transport.closed
    assert [method for method, _ in transport.calls] == [
        "session.create",
        "prompt.submit",
        "prompt.submit",
    ]
    assert first["turn_index"] == 1 and second["turn_index"] == 2
    assert first["identity"]["row_key"] == "openclaw_active/source-docs-discovery-report"
    assert second["identity"]["row_key"] == "openclaw_active/source-docs-discovery-report"
    assert first["event_count"] == second["event_count"] == 2
    assert first["terminal_status"] == second["terminal_status"] == "completed"
    assert first["approval"]["decision_class"] == second["approval"]["decision_class"] == "deny"
    assert first["provider_calls"] == second["provider_calls"] == 1
    assert "first fixture prompt" not in repr(first)
    assert "second fixture prompt" not in repr(second)
    session.close()
    assert transport.closed


def test_live_session_counts_hermes_generated_delegation_delivery_as_parent_turn() -> None:
    transport = _AsyncDelegationTransport()
    session = _session(transport, planned_calls=2, planned_turns=2)
    session.start()
    initial = session.run_turn(
        "delegate one bounded task",
        source_pack="v2_non_soak",
        source_item_id="TOOL-05",
        path="positive",
        trial_index=1,
        approval_choice="allow",
        expect_followup=True,
    )
    delivered = session.observe_delivery_turn(
        source_pack="v2_non_soak",
        source_item_id="TOOL-05",
        trial_index=1,
    )
    assert [method for method, _ in transport.calls] == ["session.create", "prompt.submit"]
    assert initial["turn_index"] == 1 and initial["provider_calls"] == 1
    assert delivered["turn_index"] == 2 and delivered["provider_calls"] == 1
    assert delivered["event_kinds"] == {
        "message.start": 1,
        "session.usage": 1,
        "subagent.complete": 1,
        "message.complete": 1,
    }
    session.close()

def test_live_session_restart_resumes_exact_stored_identity_without_exposing_it() -> None:
    first_transport = _SessionTransport()
    first = _session(first_transport, planned_calls=2, planned_turns=2)
    first.start()
    _run_turn(first, "before restart", "config-restart-capability-flip", source_pack="openclaw_active")
    first.close()
    resumed_transport = _SessionTransport(resume=True)
    resumed = first.restart(gateway=Gateway(python="fake-python", cwd=ROOT, env={}, transport=resumed_transport))
    identity = resumed.start()
    resumed_turn = _run_turn(resumed, "after restart", "config-restart-capability-flip", source_pack="openclaw_active")
    resume_calls = [params for method, params in resumed_transport.calls if method == "session.resume"]
    assert resume_calls == [{"session_id": STORED_SESSION_ID, "cols": 80}]
    assert identity["stored_handle"].sha256 == OpaqueHandle.from_value("stored_session", STORED_SESSION_ID).sha256
    assert STORED_SESSION_ID not in repr(identity)
    assert STORED_SESSION_ID not in repr(resumed_turn)
    assert resumed_turn["identity"]["row_key"] == "openclaw_active/config-restart-capability-flip"

def test_live_session_rejects_resume_identity_drift_and_unbounded_turns() -> None:
    first = _session(_SessionTransport(), planned_calls=1, planned_turns=1)
    first.start()
    first.close()
    drift = _SessionTransport(resume=True, stored_session_id="different-stored-session")
    resumed = first.restart(gateway=Gateway(python="fake-python", cwd=ROOT, env={}, transport=drift))
    with pytest.raises(V4LiveSessionViolation):
        resumed.start()
    assert drift.closed
    bounded_transport = _SessionTransport()
    bounded = _session(bounded_transport, planned_calls=1, planned_turns=1)
    bounded.start()
    _run_turn(bounded, "one", "AUTH-01")
    with pytest.raises(V4LiveSessionViolation):
        _run_turn(bounded, "two", "AUTH-01")
    assert bounded_transport.closed

def test_live_session_rejects_cross_row_or_nonpositive_provider_turn() -> None:
    cross = _session(_SessionTransport())
    cross.start()
    _run_turn(cross, "one", "AUTH-01")
    with pytest.raises(V4LiveSessionViolation):
        _run_turn(cross, "two", "PARENT-01")
    assert not cross.started

    denial = _session(_SessionTransport())
    denial.start()
    with pytest.raises(V4LiveSessionViolation):
        _run_turn(denial, "local denial", "AUTH-01", path="denial")
    assert not denial.started

def test_live_session_rejects_bad_candidate_before_gateway_start() -> None:
    transport = _SessionTransport()
    candidate = _candidate()
    candidate["sdk_version"] = "0.2.150"
    with pytest.raises(V4LiveSessionViolation):
        V4LiveSession(
            gateway=Gateway(python="fake-python", cwd=ROOT, env={}, transport=transport),
            candidate=candidate,
            preflight_projections=_preflights(candidate),
            live_map=load_v4_live_execution_map(MAP_PATH),
            map_path=MAP_PATH,
        )
    assert not transport.started

def test_live_session_collects_sanitized_host_observation_with_private_stored_identity(tmp_path: Path) -> None:
    path, stored_id = _db(tmp_path)
    transport = _SessionTransport(stored_session_id=stored_id)
    session = _session(transport)
    with pytest.raises(V4LiveSessionViolation):
        session.collect_host_observation(path, allowed_root=tmp_path, expected_turn_count=1)
    session.start()
    before_close = session.collect_host_observation(path, allowed_root=tmp_path, expected_turn_count=1)
    session.close()
    after_close = session.collect_host_observation(path, allowed_root=tmp_path, expected_turn_count=1)
    assert before_close == after_close
    assert before_close["status"] == "PASS"
    assert stored_id not in repr(before_close)
    assert [method for method, _ in transport.calls] == ["session.create"]

def test_live_session_host_observation_errors_fail_closed_without_raw_identity(tmp_path: Path) -> None:
    path, stored_id = _db(tmp_path)
    transport = _SessionTransport(stored_session_id=stored_id)
    session = _session(transport)
    session.start()
    with pytest.raises(V4LiveSessionViolation) as exc:
        session.collect_host_observation(tmp_path / "missing.db", allowed_root=tmp_path, expected_turn_count=1)
    assert stored_id not in str(exc.value)
    assert transport.closed
    with pytest.raises(V4LiveSessionViolation):
        session.collect_host_observation(path, allowed_root=tmp_path, expected_turn_count=1)


def test_live_session_collects_durable_delegation_with_private_stored_identity(tmp_path: Path) -> None:
    path, stored_id, delegation_id = _delegation_db(tmp_path)
    transport = _SessionTransport(stored_session_id=stored_id)
    session = _session(transport)
    with pytest.raises(V4LiveSessionViolation):
        session.collect_delegation_observation(path, allowed_root=tmp_path, expected_count=1)
    session.start()
    observation = session.collect_delegation_observation(
        path, allowed_root=tmp_path, expected_count=1
    )
    assert observation["status"] == "PASS"
    assert observation["count"] == observation["parent_delivery_count"] == 1
    assert stored_id not in repr(observation)
    assert delegation_id not in repr(observation)


def test_live_session_delegation_observation_failure_closes_session(tmp_path: Path) -> None:
    path, stored_id, _ = _delegation_db(tmp_path)
    transport = _SessionTransport(stored_session_id=stored_id)
    session = _session(transport)
    session.start()
    with pytest.raises(V4LiveSessionViolation) as exc:
        session.collect_delegation_observation(
            tmp_path / "missing.db", allowed_root=tmp_path, expected_count=1
        )
    assert stored_id not in str(exc.value)
    assert transport.closed
