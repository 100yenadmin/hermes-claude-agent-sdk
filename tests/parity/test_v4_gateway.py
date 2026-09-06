from __future__ import annotations
import queue
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import pytest
from hermes_claude_agent_sdk.parity.v4_gateway import MAX_FRAME_BYTES, DuplicateTerminalError, EventAccumulator, EventAccumulatorError, Gateway, GatewayProtocolError, HERMES_DISCOVERY_TOOLS, HOST_TOOLS, MCP_TOOLS, MissingTerminalError, NativeToolEvent, OpaqueHandle, PostTerminalEventError
def _event(kind: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    return {"jsonrpc": "2.0", "method": "event", "params": {"type": kind, "payload": payload or {}}}
class _Transport:
    def __init__(self) -> None:
        self.frames = deque([_event("gateway.ready"), _event("message.delta"), _event("approval.requested", {"approval_id": "fixture-approval"})])
        self.sent: list[dict[str, object]] = []
        self.started = False
    def start(self) -> None:
        self.started = True
    def send(self, frame: dict[str, object]) -> dict[str, object]:
        self.sent.append(frame)
        return {"jsonrpc": "2.0", "id": frame["id"], "result": {"ok": True, "session_id": "fixture-session"}}
    def recv(self, timeout: float) -> dict[str, object]:
        if self.frames:
            return self.frames.popleft()
        raise queue.Empty
    def close(self) -> None:
        self.frames.clear()
def test_gateway_is_inert_and_injected_transport_is_provider_free() -> None:
    transport = _Transport()
    gateway = Gateway(python=Path("/explicit/python"), cwd=Path("/tmp/hermes"), env={"PATH": "/bin"}, transport=transport, host_tools=frozenset({"memory"}), mcp_tools=frozenset({"mcp__hermes-tools__memory"}))
    assert not gateway.started
    assert gateway.command == ("/explicit/python", "-u", "-m", "tui_gateway.entry")
    gateway.start()
    assert transport.started
    result = gateway.call("health.check", {"scope": "local"}, projector=lambda raw: OpaqueHandle.from_value("session", raw["session_id"]))
    assert result["ok"] is True
    assert result["result_kind"] == "object"
    assert "raw" not in result and "scope" not in result
    assert isinstance(result["projection"], OpaqueHandle) and "session_id" not in result
    approval = gateway.wait_event("approval.requested", projector=lambda raw: OpaqueHandle.from_value("approval", raw["params"]["payload"]["approval_id"]))
    assert isinstance(approval.projection, OpaqueHandle) and "approval_id" not in approval.to_dict()
    with pytest.raises(GatewayProtocolError): gateway.call("health.check", projector=lambda _: {"raw": "unsafe"})
    assert transport.sent[0]["method"] == "health.check"
    gateway.close()
def test_accumulator_projects_and_rejects_native_duplicate_and_trailing_events() -> None:
    accumulator = EventAccumulator()
    accumulator.add(_event("gateway.ready", {"secret": "never-project"}))
    accumulator.add(_event("tool.start", {"name": "terminal"}))
    accumulator.add(_event("tool.complete", {"name": "terminal"}))
    accumulator.add(_event("tool.start", {"name": "memory"}))
    accumulator.add(_event("tool.complete", {"name": "memory"}))
    accumulator.add(_event("tool.request", {"name": "mcp__hermes-tools__memory", "args": {"command": "pwd"}}))
    accumulator.add(_event("message.complete", {"status": "completed"}))
    projection = accumulator.finish()
    assert projection["event_count"] == 7
    assert projection["terminal_status"] == "completed"
    assert projection["event_kinds"] == {"gateway.ready": 1, "message.complete": 1, "tool.complete": 2, "tool.request": 1, "tool.start": 2}
    assert all(set(item) == {"kind", "byte_length", "sha256", "terminal_status"} for item in projection["events"])
    with pytest.raises(DuplicateTerminalError):
        accumulator.add(_event("terminal", {"terminal_outcome": "completed"}))
    with pytest.raises(PostTerminalEventError):
        accumulator.add(_event("message.delta"))
    with pytest.raises(NativeToolEvent):
        EventAccumulator().add(_event("message.delta", {"content": [{"type": "tool_use", "name": "Bash"}]}))
    with pytest.raises(NativeToolEvent): EventAccumulator().add(_event("tool.request", {"name": "mcp__hermes-tools__evil"}))
    for name in ("Agent", "Bash", "Read", "Write", "Edit", "Web"):
        with pytest.raises(NativeToolEvent): EventAccumulator().add(_event("tool.start", {"name": name}))

def test_accumulator_accepts_only_exact_hermes_subagent_events() -> None:
    accumulator = EventAccumulator()
    admitted = ("subagent.spawn_requested", "subagent.start", "subagent.complete", "subagent.text", "subagent.thinking", "subagent.tool", "subagent.progress")
    for kind in admitted:
        payload = {"task_index": 0, "task_count": 1, "child_session_id": "synthetic-child"}
        if kind == "subagent.tool": payload["tool_name"] = "terminal"
        accumulator.add(_event(kind, payload))
    accumulator.add(_event("message.complete", {"status": "completed"}))
    projection = accumulator.finish()
    assert projection["event_kinds"] == {kind: 1 for kind in admitted} | {"message.complete": 1}

    for kind in ("subagent.start.lookalike", "subagent.spawn_requestedly", "subagent.unknown"):
        with pytest.raises(NativeToolEvent): EventAccumulator().add(_event(kind, {"task_index": 0, "task_count": 1}))
    with pytest.raises(NativeToolEvent): EventAccumulator().add(_event("subagent.start", {"content": [{"type": "Agent"}]}))
    with pytest.raises(NativeToolEvent): EventAccumulator().add(_event("subagent.tool", {"tool_name": "Agent"}))

def test_subagent_completion_does_not_end_parent_turn() -> None:
    accumulator = EventAccumulator()
    accumulator.add(_event("subagent.start", {"task_index": 0, "task_count": 1}))
    child = accumulator.add(_event("subagent.complete", {"status": "completed", "terminal_outcome": "completed"}))
    assert child.terminal_status is None
    progress = accumulator.add(_event("subagent.progress", {"task_index": 0, "task_count": 1}))
    assert progress.terminal_status is None
    terminal = accumulator.add(_event("message.complete", {"status": "completed"}))
    assert terminal.terminal_status == "completed"
    projection = accumulator.finish()
    assert projection["terminal_status"] == "completed" and projection["terminal_count"] == 1

def test_gateway_accepts_hermes_subagent_lifecycle_from_fake_transport() -> None:
    transport = _Transport()
    transport.frames = deque([
        _event("gateway.ready"),
        _event("subagent.spawn_requested", {"task_index": 0, "task_count": 1}),
        _event("subagent.start", {"task_index": 0, "task_count": 1}),
        _event("subagent.complete", {"task_index": 0, "task_count": 1, "status": "completed"}),
        _event("subagent.text", {"task_index": 0, "task_count": 1}),
        _event("subagent.thinking", {"task_index": 0, "task_count": 1}),
        _event("subagent.tool", {"task_index": 0, "task_count": 1, "tool_name": "terminal"}),
        _event("subagent.progress", {"task_index": 0, "task_count": 1}),
    ])
    gateway = Gateway(python=Path("/explicit/python"), cwd=Path("/tmp/hermes"), env={"PATH": "/bin"}, transport=transport)
    gateway.start()
    assert [gateway.next_event().event_type for _ in range(7)] == [
        "subagent.spawn_requested", "subagent.start", "subagent.complete", "subagent.text", "subagent.thinking", "subagent.tool", "subagent.progress"
    ]
    gateway.close()


def test_stdio_reader_bounds_each_read_before_frame_allocation() -> None:
    class Stream:
        def __init__(self) -> None:
            self.readline_sizes: list[int] = []
            self.iterated = False

        def __iter__(self):
            self.iterated = True
            return iter((b"x" * (MAX_FRAME_BYTES + 1),))

        def readline(self, size: int) -> bytes:
            self.readline_sizes.append(size)
            return b"x" * (MAX_FRAME_BYTES + 1)

    stream = Stream()
    gateway = Gateway(python="python", cwd="/tmp", env={"PATH": "/bin"})
    gateway._process = SimpleNamespace(stdout=stream)
    gateway._pump_stdio()
    assert not stream.iterated
    assert stream.readline_sizes == [MAX_FRAME_BYTES + 1]
    assert isinstance(gateway._reader_error, GatewayProtocolError)

@pytest.mark.parametrize(
    ("status", "expected"),
    (("complete", "completed"), ("error", "failed"), ("interrupted", "cancelled")),
)
def test_message_complete_maps_hermes_terminal_statuses(status: str, expected: str) -> None:
    accumulator = EventAccumulator()
    projection = accumulator.add(_event("message.complete", {"status": status}))
    assert projection.terminal_status == expected
    assert accumulator.finish()["terminal_status"] == expected

@pytest.mark.parametrize("payload", ({"status": "unknown"}, {}))
def test_message_complete_unknown_or_missing_status_fails_closed(payload: dict[str, object]) -> None:
    with pytest.raises(EventAccumulatorError):
        EventAccumulator().add(_event("message.complete", payload))

def test_host_tool_inventory_is_explicit_and_fail_closed() -> None:
    accumulator = EventAccumulator(host_tools=frozenset({"memory", "session_search"}), mcp_tools=frozenset({"mcp__hermes-tools__memory"}))
    accumulator.add(_event("tool.start", {"name": "session_search"}))
    with pytest.raises(NativeToolEvent): accumulator.add(_event("tool.start", {"name": "browser"}))
    with pytest.raises(ValueError): EventAccumulator(host_tools=frozenset({"*"}))
    with pytest.raises(ValueError): EventAccumulator(host_tools=frozenset({"Bash"}))


@pytest.mark.parametrize("skill_name", ("weather", "slack"))
def test_hermes_skill_result_name_is_data_not_a_tool_name(skill_name: str) -> None:
    tools = frozenset({"skill_view"})
    mcps = frozenset({"mcp__hermes-tools__skill_view"})
    # _on_tool_complete JSON-decodes skill_view's result into this payload.
    payload = {"name": "skill_view", "args": {"name": skill_name},
               "result": {"success": True, "name": skill_name, "content": "fixture"}}
    EventAccumulator(host_tools=tools, mcp_tools=mcps).add(_event("tool.complete", payload))
    with pytest.raises(NativeToolEvent):
        EventAccumulator(host_tools=tools, mcp_tools=mcps).add(
            _event("tool.complete", dict(payload, name="unknown_tool")))
    with pytest.raises(NativeToolEvent):
        EventAccumulator(host_tools=tools, mcp_tools=mcps).add(
            _event("tool.complete", dict(payload, result={"content": [{"type": "tool_use", "name": "Agent"}]})))


def test_default_inventory_admits_only_hermes_discovery_bridges() -> None:
    assert HERMES_DISCOVERY_TOOLS == {"tool_search", "tool_describe", "tool_call"}
    assert HERMES_DISCOVERY_TOOLS <= HOST_TOOLS
    assert {f"mcp__hermes-tools__{name}" for name in HERMES_DISCOVERY_TOOLS} <= MCP_TOOLS
    accumulator = EventAccumulator()
    for index, name in enumerate(sorted(HERMES_DISCOVERY_TOOLS), 1):
        accumulator.add(_event("tool.start", {"name": name, "tool_id": f"bridge-{index}"}))
        accumulator.add(_event("tool.complete", {"name": name, "tool_id": f"bridge-{index}"}))
    accumulator.add(_event("message.complete", {"status": "completed"}))
    assert accumulator.finish()["terminal_status"] == "completed"
    with pytest.raises(NativeToolEvent):
        EventAccumulator().add(_event("tool.start", {"name": "Agent", "tool_id": "native"}))
def test_accumulator_requires_terminal_and_is_bounded() -> None:
    accumulator = EventAccumulator(max_events=1)
    accumulator.add(_event("message.delta"))
    with pytest.raises(MissingTerminalError):
        accumulator.finish()
    with pytest.raises(ValueError):
        accumulator.add(_event("message.delta"))
