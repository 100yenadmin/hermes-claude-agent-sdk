"""Inert, provider-free JSON-RPC gateway and content-free event receipts."""
from __future__ import annotations
import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
MAX_FRAME_BYTES = 1_048_576
MAX_EVENTS = 10_000
MAX_QUEUE = 1_024
MAX_TIMEOUT = 600.0
TOOL_PREFIX = "mcp__hermes-tools__"
HOST_TOOLS = frozenset({"memory", "session_search", "skills", "browser", "cron", "terminal", "process_manage", "delegate_task"})
MCP_TOOLS = frozenset({"mcp__hermes-tools__memory", "mcp__hermes-tools__session_search", "mcp__hermes-tools__skills", "mcp__hermes-tools__browser", "mcp__hermes-tools__cron", "mcp__hermes-tools__terminal", "mcp__hermes-tools__process_manage", "mcp__hermes-tools__delegate_task"})
TERMINAL_STATUSES = frozenset({"completed", "denied", "failed", "cancelled"})
_TERMINAL_STATUS_MAP = {
    "complete": "completed",
    "completed": "completed",
    "error": "failed",
    "failed": "failed",
    "interrupted": "cancelled",
    "cancelled": "cancelled",
    "denied": "denied",
}
_SAFE_KIND = re.compile(r"^[A-Za-z0-9_.:/-]{1,96}$")
_NATIVE_TYPES = frozenset({"agent", "agent_tool", "tool_use", "tool_result", "server_tool_use", "server_tool_result"})
_NATIVE_NAMES = frozenset({"agent", "bash", "read", "write", "edit", "web"})
# Hermes emits these concrete delegation progress events from ``delegate_task``.
# They contain the word "agent" but are not Claude SDK native tool events; keep
# the admission set exact so lookalikes and future provider events stay closed.
_HERMES_SUBAGENT_LIFECYCLE = frozenset({"subagent.spawn_requested", "subagent.start", "subagent.complete"})
_HERMES_SUBAGENT_PROGRESS = frozenset({"subagent.text", "subagent.thinking", "subagent.tool", "subagent.progress"})
_HERMES_SUBAGENT_EVENTS = _HERMES_SUBAGENT_LIFECYCLE | _HERMES_SUBAGENT_PROGRESS
_TURN_TERMINAL_TYPES = frozenset({"terminal", "message.complete", "session.complete", "run.complete", "task.complete", "turn.complete"})
_STOP = object()
class GatewayError(RuntimeError): pass
class GatewayNotStarted(GatewayError): pass
class GatewayClosed(GatewayError): pass
class GatewayTimeout(GatewayError): pass
class GatewayProtocolError(GatewayError): pass
class EventAccumulatorError(ValueError): pass
class NativeToolEvent(EventAccumulatorError): pass
class DuplicateTerminalError(EventAccumulatorError): pass
class MissingTerminalError(EventAccumulatorError): pass
class PostTerminalEventError(EventAccumulatorError): pass
class GatewayRpcError(GatewayProtocolError):
    """An RPC error projected without retaining its message."""
    def __init__(self, method: str, code: object, message: object) -> None:
        self.method = method
        self.code = code if type(code) is int else None
        text = message if isinstance(message, str) else ""
        encoded = text.encode("utf-8", errors="replace")
        self.message_bytes = len(encoded)
        self.message_sha256 = hashlib.sha256(encoded).hexdigest()
        super().__init__("gateway RPC returned an error envelope")
class JsonRpcTransport(Protocol):
    def send(self, frame: Mapping[str, Any]) -> object: ...
    def recv(self, timeout: float) -> Mapping[str, Any] | None: ...
@dataclass(frozen=True)
class OpaqueHandle:
    """A caller-reusable identity projection that never stores the identity."""
    kind: str
    byte_length: int
    sha256: str
    @classmethod
    def from_value(cls, kind: str, value: str | bytes) -> "OpaqueHandle":
        if not isinstance(value, (str, bytes)): raise ValueError("handle value is invalid")
        raw = value.encode() if isinstance(value, str) else value
        if not raw or len(raw) > MAX_FRAME_BYTES: raise ValueError("handle value is not bounded")
        return cls(_kind(kind), len(raw), hashlib.sha256(raw).hexdigest())
    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "byte_length": self.byte_length, "sha256": self.sha256}
def _dump(value: Any) -> bytes:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, UnicodeError) as exc:
        raise GatewayProtocolError("frame is not bounded JSON") from exc
    if len(encoded) > MAX_FRAME_BYTES: raise GatewayProtocolError("frame exceeds the bounded size")
    return encoded
def _load(value: object) -> tuple[dict[str, Any], bytes]:
    if isinstance(value, Mapping):
        frame, encoded = dict(value), _dump(value)
    elif isinstance(value, (bytes, str)):
        encoded = value.encode() if isinstance(value, str) else value
        if len(encoded) > MAX_FRAME_BYTES: raise GatewayProtocolError("frame exceeds the bounded size")
        try: frame = json.loads(encoded)
        except (TypeError, ValueError, UnicodeDecodeError) as exc: raise GatewayProtocolError("frame is not valid JSON") from exc
    else: raise GatewayProtocolError("frame must be a mapping or JSON text")
    if not isinstance(frame, dict): raise GatewayProtocolError("frame must be a JSON object")
    return frame, encoded
def _kind(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or _SAFE_KIND.fullmatch(value) is None: raise EventAccumulatorError("event kind is not a bounded identifier")
    return value
def _params(frame: Mapping[str, Any]) -> Mapping[str, Any]:
    value = frame.get("params")
    return value if isinstance(value, Mapping) else {}
def _host_inventory(value: object, prefix: str = "") -> frozenset[str]:
    if value is None: return MCP_TOOLS if prefix else HOST_TOOLS
    if not isinstance(value, (set, frozenset)): raise ValueError("host_tools must be an explicit set")
    tools = frozenset(value)
    if any(not isinstance(name, str) or _SAFE_KIND.fullmatch(name) is None or prefix and (name == prefix or not name.startswith(prefix)) or not prefix and name.casefold() in _NATIVE_NAMES for name in tools): raise ValueError("tool inventory contains an invalid or native name")
    return tools
def _tool_check(value: object, prefix: str, hosts: frozenset[str] = HOST_TOOLS, mcps: frozenset[str] = MCP_TOOLS, *, depth: int = 0, toolish: bool = False, hermes_event_type: str | None = None) -> None:
    if depth > 5: return
    if isinstance(value, Mapping):
        typ, local = value.get("type"), toolish
        if isinstance(typ, str):
            lowered = typ.casefold()
            names = [value.get(key) for key in ("name", "tool_name", "tool")]
            is_hermes_event = depth == 0 and typ == hermes_event_type and typ in _HERMES_SUBAGENT_EVENTS
            if not is_hermes_event and ("agent" in lowered or lowered in _NATIVE_TYPES): raise NativeToolEvent("native tool or agent event is not admitted")
            local = local or "tool" in lowered or ("agent" in lowered and not is_hermes_event)
        for key in ("name", "tool_name", "tool"):
            name = value.get(key)
            if isinstance(name, str) and name:
                if name in mcps or local and name in hosts: local = True
                elif local: raise NativeToolEvent("tool request is outside the Hermes MCP namespace")
        for key, child in value.items():
            if key in {"payload", "content", "content_block", "delta", "event", "request", "result", "tool"}:
                _tool_check(child, prefix, hosts, mcps, depth=depth + 1, toolish=local)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in list(value)[:64]: _tool_check(child, prefix, hosts, mcps, depth=depth + 1, toolish=toolish)
def _event_projection(frame: Mapping[str, Any], encoded: bytes, prefix: str = TOOL_PREFIX, hosts: frozenset[str] = HOST_TOOLS, mcps: frozenset[str] = MCP_TOOLS) -> "EventProjection":
    params, event_type = _params(frame), _kind(_params(frame).get("type"))
    lowered = event_type.casefold()
    _tool_check(params, prefix, hosts, mcps, hermes_event_type=event_type)
    body = params.get("payload")
    body = body if isinstance(body, Mapping) else {}
    names = (params.get("name"), params.get("tool_name"), params.get("tool"), body.get("name"), body.get("tool_name"), body.get("tool"))
    is_hermes_event = event_type in _HERMES_SUBAGENT_EVENTS
    if "tool" in lowered and not is_hermes_event and not any(isinstance(name, str) and name in (hosts if lowered in {"tool.start", "tool.complete"} else mcps) for name in names): raise NativeToolEvent("tool request is outside the Hermes MCP namespace")
    terminalish = lowered in _TURN_TERMINAL_TYPES
    candidate = body.get("terminal_outcome") if terminalish else None
    terminal: str | None = None
    if candidate is not None:
        if not isinstance(candidate, str) or candidate not in TERMINAL_STATUSES: raise EventAccumulatorError("terminal outcome is unsupported")
        terminal = candidate
    elif terminalish:
        status = body.get("status")
        terminal = _TERMINAL_STATUS_MAP.get(status) if isinstance(status, str) else None
        if terminal is None: raise EventAccumulatorError("terminal status is unsupported")
    return EventProjection(event_type, len(encoded), hashlib.sha256(encoded).hexdigest(), terminal)
@dataclass(frozen=True)
class EventProjection:
    event_type: str
    byte_length: int
    sha256: str
    terminal_status: str | None = None
    projection: OpaqueHandle | None = None
    def to_dict(self) -> dict[str, object]:
        result = {"kind": self.event_type, "byte_length": self.byte_length, "sha256": self.sha256, "terminal_status": self.terminal_status}
        if self.projection is not None: result["projection"] = _safe_projection(self.projection).to_dict()
        return result
def _safe_projection(value: object) -> OpaqueHandle:
    if type(value) is not OpaqueHandle or not isinstance(value.kind, str) or _SAFE_KIND.fullmatch(value.kind) is None or type(value.byte_length) is not int or not 0 <= value.byte_length <= MAX_FRAME_BYTES or not isinstance(value.sha256, str) or re.fullmatch(r"[0-9a-f]{64}", value.sha256) is None: raise GatewayProtocolError("projector returned an invalid handle")
    return value
def _run_projector(projector: Callable[[object], object] | None, raw: object) -> OpaqueHandle | None:
    if projector is None: return None
    try: value = projector(raw)
    except Exception: raise GatewayProtocolError("projector failed") from None
    return None if value is None else _safe_projection(value)
class EventAccumulator:
    """Validate one event sequence, retaining only safe projections."""
    def __init__(self, *, max_events: int = MAX_EVENTS, tool_prefix: str = TOOL_PREFIX, host_tools: set[str] | frozenset[str] | None = None, mcp_tools: set[str] | frozenset[str] | None = None) -> None:
        if type(max_events) is not int or not 1 <= max_events <= MAX_EVENTS or not isinstance(tool_prefix, str) or not tool_prefix: raise ValueError("accumulator bounds are invalid")
        self._max_events, self._prefix, self._hosts, self._mcps = max_events, tool_prefix, _host_inventory(host_tools), _host_inventory(mcp_tools, tool_prefix)
        self._events: list[EventProjection] = []
        self._counts: Counter[str] = Counter()
        self._bytes, self._terminal = 0, None
    def add(self, frame: object) -> EventProjection:
        parsed, encoded = _load(frame)
        if parsed.get("jsonrpc") != "2.0" or parsed.get("method") != "event": raise EventAccumulatorError("frame is not a gateway event")
        projection = _event_projection(parsed, encoded, self._prefix, self._hosts, self._mcps)
        if self._terminal is not None:
            if projection.terminal_status is not None: raise DuplicateTerminalError("multiple terminal events")
            raise PostTerminalEventError("event arrived after terminal")
        if len(self._events) >= self._max_events: raise EventAccumulatorError("event sequence exceeds the bounded count")
        self._events.append(projection); self._counts[projection.event_type] += 1; self._bytes += projection.byte_length
        if projection.terminal_status is not None: self._terminal = projection.terminal_status
        return projection
    accept = add
    record = add
    @property
    def terminal_status(self) -> str | None: return self._terminal
    def projection(self, *, require_terminal: bool = True) -> dict[str, object]:
        if require_terminal and self._terminal is None: raise MissingTerminalError("event sequence has no terminal")
        return {"event_count": len(self._events), "event_kinds": dict(sorted(self._counts.items())), "event_bytes": self._bytes, "events": [item.to_dict() for item in self._events], "terminal_status": self._terminal, "terminal_count": 1 if self._terminal is not None else 0}
    snapshot = projection
    finish = projection
class Gateway:
    """Normal-Hermes stdio client; construction never creates a process."""
    def __init__(self, *, python: str | Path, cwd: str | Path, env: Mapping[str, str], transport: JsonRpcTransport | None = None, max_backlog: int = MAX_QUEUE, host_tools: set[str] | frozenset[str] | None = None, mcp_tools: set[str] | frozenset[str] | None = None) -> None:
        executable, workdir = os.fspath(python), os.fspath(cwd)
        if not executable or not workdir or not isinstance(env, Mapping) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()) or type(max_backlog) is not int or not 1 <= max_backlog <= MAX_QUEUE: raise ValueError("python, cwd, env, and backlog are required and bounded")
        self.python, self.cwd, self._env, self._transport, self._hosts, self._mcps = executable, workdir, dict(env), transport, _host_inventory(host_tools), _host_inventory(mcp_tools, TOOL_PREFIX)
        self._max_backlog = max_backlog
        self._responses: queue.Queue[object] = queue.Queue(maxsize=max_backlog); self._events: queue.Queue[object] = queue.Queue(maxsize=max_backlog)
        self._backlog: deque[dict[str, Any]] = deque(maxlen=max_backlog)
        self._next_id, self._process, self._reader = 1, None, None
        self._stop, self._reader_error = threading.Event(), None
    @property
    def command(self) -> tuple[str, str, str, str]: return (self.python, "-u", "-m", "tui_gateway.entry")
    @property
    def started(self) -> bool: return self._reader is not None
    def start(self, *, wait_ready: bool = True, timeout: float = 30.0) -> None:
        if self.started: raise GatewayProtocolError("gateway was already started")
        if not 0 < timeout <= MAX_TIMEOUT: raise ValueError("timeout is outside the bounded range")
        self._stop.clear()
        if self._transport is None:
            try: self._process = subprocess.Popen(self.command, cwd=self.cwd, env=dict(self._env), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except OSError as exc: raise GatewayError("gateway process could not start") from exc
            target = self._pump_stdio
        else:
            starter = getattr(self._transport, "start", None)
            if callable(starter): starter()
            target = self._pump_transport
        self._reader = threading.Thread(target=target, daemon=True); self._reader.start()
        if wait_ready: self.wait_event("gateway.ready", timeout=timeout)
    def _enqueue(self, value: object) -> None:
        try:
            frame, _ = _load(value); (self._events if frame.get("method") == "event" else self._responses).put_nowait(frame)
        except (GatewayProtocolError, queue.Full):
            self._reader_error, _ = GatewayProtocolError("gateway emitted an invalid or excessive frame"), self._stop.set()
    def _pump_stdio(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            for line in self._process.stdout:
                if self._stop.is_set(): break
                if len(line) > MAX_FRAME_BYTES:
                    self._reader_error = GatewayProtocolError("gateway frame exceeds the bounded size"); break
                self._enqueue(line)
        finally: self._signal_closed()
    def _pump_transport(self) -> None:
        assert self._transport is not None
        try:
            while not self._stop.is_set():
                try: frame = self._transport.recv(0.1)
                except (queue.Empty, TimeoutError): continue
                if frame is None: break
                self._enqueue(frame)
        except BaseException: self._reader_error = GatewayProtocolError("injected transport failed")
        finally: self._signal_closed()
    def _signal_closed(self) -> None:
        for target in (self._responses, self._events):
            try: target.put_nowait(_STOP)
            except queue.Full: pass
    def _read(self, target: queue.Queue[object], timeout: float) -> dict[str, Any]:
        if not 0 < timeout <= MAX_TIMEOUT: raise ValueError("timeout is outside the bounded range")
        try: value = target.get(timeout=timeout)
        except queue.Empty as exc: raise GatewayTimeout("gateway response or event timed out") from exc
        if value is _STOP:
            if self._reader_error is not None: raise self._reader_error
            raise GatewayClosed("gateway exited before the requested frame")
        if not isinstance(value, dict): raise GatewayProtocolError("gateway emitted a non-object frame")
        return value
    def _require_started(self) -> None:
        if not self.started: raise GatewayNotStarted("call start() after preflight")
    def _send(self, frame: Mapping[str, Any]) -> None:
        if self._transport is not None:
            result = self._transport.send(frame)
            if isinstance(result, Mapping): self._enqueue(result)
            elif isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
                for item in list(result)[:MAX_QUEUE]: self._enqueue(item)
            return
        assert self._process is not None and self._process.stdin is not None
        try: self._process.stdin.write(_dump(frame) + b"\n"); self._process.stdin.flush()
        except (OSError, ValueError) as exc: raise GatewayClosed("gateway input is unavailable") from exc
    def call(self, method: str, params: Mapping[str, Any] | None = None, *, timeout: float = 30.0, projector: Callable[[object], object] | None = None) -> dict[str, object]:
        self._require_started()
        if not 0 < timeout <= MAX_TIMEOUT or not isinstance(method, str) or _SAFE_KIND.fullmatch(method) is None or params is not None and not isinstance(params, Mapping): raise ValueError("method, params, or timeout is invalid")
        request_id = str(self._next_id); self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params or {})})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise GatewayTimeout("gateway response timed out")
            frame = self._read(self._responses, min(MAX_TIMEOUT, remaining))
            if str(frame.get("id")) != request_id: continue
            if isinstance(frame.get("error"), Mapping):
                error = frame["error"]; raise GatewayRpcError(method, error.get("code"), error.get("message"))
            if "result" not in frame: raise GatewayProtocolError("gateway response has no result")
            result, encoded = frame["result"], _dump(frame["result"])
            kind = "null" if result is None else "boolean" if isinstance(result, bool) else "number" if isinstance(result, (int, float)) else "string" if isinstance(result, str) else "array" if isinstance(result, list) else "object"
            projected = _run_projector(projector, result)
            response = {"ok": True, "method": method, "id": request_id, "result_kind": kind, "result_bytes": len(encoded), "result_sha256": hashlib.sha256(encoded).hexdigest()}
            if projected is not None: response["projection"] = projected
            return response
    rpc = call
    def wait_event(self, event_type: str, *, session_id: str | None = None, timeout: float = 30.0, projector: Callable[[object], object] | None = None) -> EventProjection:
        self._require_started()
        if not 0 < timeout <= MAX_TIMEOUT: raise ValueError("timeout is outside the bounded range")
        expected = _kind(event_type)
        if session_id is not None and (not isinstance(session_id, str) or not session_id): raise ValueError("session_id is invalid")
        deadline, deferred = time.monotonic() + timeout, []
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise GatewayTimeout("gateway event timed out")
                frame = self._backlog.popleft() if self._backlog else self._read(self._events, min(MAX_TIMEOUT, remaining))
                parsed, encoded = _load(frame); projection = _event_projection(parsed, encoded, hosts=self._hosts, mcps=self._mcps)
                params = _params(parsed)
                if params.get("type") != expected or session_id is not None and params.get("session_id") != session_id:
                    deferred.append(parsed)
                    if len(deferred) >= self._max_backlog: raise GatewayProtocolError("event backlog exceeds the bounded count")
                    continue
                projected = _run_projector(projector, parsed)
                return EventProjection(projection.event_type, projection.byte_length, projection.sha256, projection.terminal_status, projected)
        finally:
            for frame in reversed(deferred): self._backlog.appendleft(frame)
    def next_event(self, *, timeout: float = 30.0, projector: Callable[[object], object] | None = None) -> EventProjection:
        self._require_started()
        frame = self._backlog.popleft() if self._backlog else self._read(self._events, timeout)
        parsed, encoded = _load(frame)
        projection = _event_projection(parsed, encoded, hosts=self._hosts, mcps=self._mcps)
        return EventProjection(projection.event_type, projection.byte_length, projection.sha256, projection.terminal_status, _run_projector(projector, parsed))
    next_message = next_event
    wait_for_event = wait_event
    def close(self) -> None:
        if not self.started: return
        self._stop.set()
        if self._transport is not None:
            closer = getattr(self._transport, "close", None)
            if callable(closer): closer()
        elif self._process is not None:
            if self._process.stdin is not None and not self._process.stdin.closed: self._process.stdin.close()
            try: self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.terminate()
                try: self._process.wait(timeout=2)
                except subprocess.TimeoutExpired: self._process.kill(); self._process.wait(timeout=2)
GatewayEvent = EventProjection
RpcFailure = GatewayRpcError
EnvironmentBlocked = GatewayError
__all__ = ["DuplicateTerminalError", "EnvironmentBlocked", "EventAccumulator", "EventAccumulatorError", "EventProjection", "Gateway", "GatewayClosed", "GatewayError", "GatewayEvent", "GatewayNotStarted", "GatewayProtocolError", "GatewayRpcError", "GatewayTimeout", "HOST_TOOLS", "JsonRpcTransport", "MCP_TOOLS", "MissingTerminalError", "NativeToolEvent", "OpaqueHandle", "PostTerminalEventError", "RpcFailure", "TOOL_PREFIX"]
