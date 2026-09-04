from __future__ import annotations
import json
from pathlib import Path
import pytest
from hermes_claude_agent_sdk.parity.v4_gateway import EventProjection, OpaqueHandle
from hermes_claude_agent_sdk.parity.v4_gateway_observer import V4GatewayObserver, V4GatewayObserverViolation, identity_hash, snapshot_fixture_state
def _event(kind: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    return {"jsonrpc": "2.0", "method": "event", "params": {"type": kind, "payload": payload or {}}}
def _reject(events: list[dict[str, object]]) -> None:
    observer = V4GatewayObserver(_FakeGateway(events)); observer.start(); [observer.next_event() for _ in events[:-1]]
    with pytest.raises(V4GatewayObserverViolation): observer.next_event()
class _FakeGateway:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events, self.calls, self.started, self.closed = list(events), [], False, False
    def start(self) -> None: self.started = True
    def call(self, method, params=None, *, projector=None, **kwargs):
        params = dict(params or {})
        self.calls.append((method, params))
        result = {"session_id": "session-secret"} if method == "session.create" else {"resolved": 1}
        if callable(projector): projector(result)
        return {"ok": True, "result_kind": "object", "result_bytes": 16, "result_sha256": "a" * 64}
    def next_event(self, *, projector=None, **kwargs):
        event = self.events.pop(0)
        if callable(projector):
            projector(event)
        return EventProjection(event["params"]["type"], 1, "b" * 64, "completed" if event["params"]["type"] == "message.complete" else None)
    def close(self) -> None: self.closed = True
def test_observer_composes_projectors_and_records_sanitized_approval_recovery() -> None:
    fake = _FakeGateway([
        _event("message.start", {"session_id": "session-secret"}),
        _event("approval.request", {"request_id": "request-secret"}),
        _event("approval.request", {"request_id": "recovery-secret"}),
        _event("tool.start", {"name": "fixture_tool", "tool_call_id": "call-a"}),
        _event("tool.complete", {"name": "fixture_tool", "tool_call_id": "call-a"}),
        _event("subagent.spawn_requested", {"task_index": 0, "task_count": 1, "parent_id": "parent-secret", "child_id": "child-secret", "delegation_id": "delegation-secret"}),
        _event("subagent.start", {"task_index": 0, "task_count": 1}),
        _event("subagent.complete", {"task_index": 0, "task_count": 1}),
        _event("message.complete", {"status": "completed"}),
    ])
    observer = V4GatewayObserver(fake, allowed_tool_names={"fixture_tool"})
    observer.start(); observer.call("session.create", projector=lambda raw: OpaqueHandle.from_value("session", raw["session_id"]))
    observer.next_event(); observer.next_event()
    observer.call("approval.respond", {"session_id": "session-secret", "request_id": "request-secret", "choice": "deny"})
    observer.next_event(); observer.call("approval.respond", {"session_id": "session-secret", "request_id": "recovery-secret", "choice": "allow"})
    for _ in range(6):
        observer.next_event(projector=lambda raw: OpaqueHandle.from_value("frame", "seen"))
    result = observer.snapshot()
    assert result["approval"]["responses"] == [{"decision_class": "deny", "resolved": 1}, {"decision_class": "allow", "resolved": 1}]
    assert result["approval"]["denial_tool_free"] and result["approval"]["recovery_succeeded"]
    assert result["tools"] == {"started": ["fixture_tool"], "completed": ["fixture_tool"]}
    assert result["subagents"][0]["task_index"] == 0 and result["subagents"][0]["parent_id_sha256"] == identity_hash("parent_id", "parent-secret")
    assert observer.collect_delegation_observation()["count"] == 1
    assert "request-secret" not in repr(result) and "session-secret" not in repr(result)
    assert fake.calls[0][0] == "session.create" and fake.started
    observer.close()
    assert fake.closed
def test_observer_rejects_wrong_approval() -> None:
    fake = _FakeGateway([_event("approval.request", {"request_id": "request-secret"})])
    observer = V4GatewayObserver(fake); observer.start(); observer.next_event()
    with pytest.raises(V4GatewayObserverViolation):
        observer.call("approval.respond", {"request_id": "stale-request", "choice": "allow"})
def test_observer_accepts_two_child_lifecycles_and_rejects_duplicates() -> None:
    events = [[_event("subagent.spawn_requested", {"task_index": i, "task_count": 2, "parent_id": "p", "child_id": "c" + str(i), "delegation_id": "d" + str(i)}), _event("subagent.start", {"task_index": i, "task_count": 2}), _event("subagent.complete", {"task_index": i, "task_count": 2, "status": "completed"})] for i in range(2)]
    fake = _FakeGateway([frame for phases in events for frame in phases] + [_event("background.status"), _event("delegation.status"), _event("subagent.text", {"task_index": 1, "task_count": 2}), _event("message.complete", {"status": "completed"})])
    observer = V4GatewayObserver(fake); observer.start()
    for _ in range(10): observer.next_event()
    result = observer.snapshot()
    assert [item["phase"] for item in result["subagents"]] == ["spawn_requested", "start", "complete"] * 2 and [item["task_index"] for item in result["subagents"]] == [0, 0, 0, 1, 1, 1] and observer.collect_delegation_observation()["count"] == 2
    _reject([events[0][0], events[0][0]])
    _reject([events[0][0], _event("subagent.start", {"task_index": 0, "task_count": 2, "parent_id": "q"})]); _reject([events[0][0], _event("subagent.spawn_requested", {"task_index": 1, "task_count": 2, "parent_id": "q"})])


def test_observer_accepts_real_gateway_two_phase_child_lifecycle() -> None:
    fake = _FakeGateway([
        _event("subagent.start", {"task_index": 0, "task_count": 1, "parent_id": "p", "subagent_id": "runtime-child", "child_session_id": "persisted-child", "delegation_id": "d"}),
        _event("subagent.complete", {"task_index": 0, "task_count": 1, "parent_id": "p", "subagent_id": "runtime-child", "child_session_id": "persisted-child", "delegation_id": "d", "status": "completed"}),
        _event("message.complete", {"status": "completed"}),
    ])
    observer = V4GatewayObserver(fake)
    observer.start()
    for _ in range(3):
        observer.next_event()
    result = observer.snapshot()
    assert [item["phase"] for item in result["subagents"]] == ["start", "complete"]
    assert result["subagents"][0]["child_id_sha256"] == identity_hash("child_id", "persisted-child")
    assert observer.collect_delegation_observation()["count"] == 1
def test_observer_pairs_same_name_tools_by_id() -> None:
    observer = V4GatewayObserver(_FakeGateway([_event("tool.start", {"name": "fixture_tool", "tool_call_id": "a"}), _event("tool.start", {"name": "fixture_tool", "tool_call_id": "b"}), _event("tool.complete", {"name": "fixture_tool", "tool_call_id": "b"}), _event("tool.complete", {"name": "fixture_tool", "tool_call_id": "a"}), _event("message.complete", {"status": "completed"})]), allowed_tool_names={"fixture_tool"}); observer.start(); [observer.next_event() for _ in range(5)]
    assert observer.snapshot()["tools"] == {"started": ["fixture_tool", "fixture_tool"], "completed": ["fixture_tool", "fixture_tool"]}


def test_observer_accepts_normal_hermes_gateway_tool_id() -> None:
    observer = V4GatewayObserver(_FakeGateway([
        _event("tool.start", {"name": "fixture_tool", "tool_id": "gateway-call"}),
        _event("tool.complete", {"name": "fixture_tool", "tool_id": "gateway-call"}),
        _event("message.complete", {"status": "completed"}),
    ]), allowed_tool_names={"fixture_tool"})
    observer.start()
    for _ in range(3):
        observer.next_event()
    assert observer.snapshot()["tools"] == {"started": ["fixture_tool"], "completed": ["fixture_tool"]}


def test_turn_complete_is_terminal_and_rejects_trailing_events() -> None:
    observer = V4GatewayObserver(
        _FakeGateway([
            _event("turn.complete", {"status": "completed"}),
            _event("message.delta"),
        ])
    )
    observer.start()
    observer.next_event()
    assert observer.snapshot()["terminal_status"] == "completed"
    with pytest.raises(V4GatewayObserverViolation, match="after terminal"):
        observer.next_event()

def test_fixture_snapshot_is_bounded_and_sanitized(tmp_path: Path) -> None:
    state = tmp_path / ".hermes_v4_fixture_state.json"
    state.write_text(json.dumps({"schema_version": 1, "record_count": 2, "item_count": 1, "item_hash": "a" * 64, "operation_hash": "b" * 64}), encoding="utf-8")
    result = snapshot_fixture_state(tmp_path)
    assert result["exists"] is True and result["record_count"] == 2 and result["size"] == state.stat().st_size and set(result) == {"exists", "size", "sha256", "record_count"}
    state.write_text("{}", encoding="utf-8")
    with pytest.raises(V4GatewayObserverViolation):
        snapshot_fixture_state(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(V4GatewayObserverViolation):
        snapshot_fixture_state(link)
