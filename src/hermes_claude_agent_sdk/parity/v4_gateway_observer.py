from __future__ import annotations
import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from .v4_gateway import EventProjection
MAX_FIXTURE_BYTES = 64 * 1024
MAX_OBSERVED_EVENTS = 10_000
STATE_FILE = ".hermes_v4_fixture_state.json"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE = re.compile(r"^[A-Za-z0-9_.:/@+#-]{1,256}$")
_TERMINALS = {"complete": "completed", "completed": "completed", "denied": "denied", "error": "failed", "failed": "failed", "interrupted": "cancelled", "cancelled": "cancelled"}
_APPROVAL = frozenset(("approval.request", "approval.requested"))
_START = frozenset(("tool.start", "tool.started"))
_COMPLETE = frozenset(("tool.complete", "tool.completed", "tool.result"))
_CHILD = frozenset(("subagent.spawn_requested", "subagent.start", "subagent.complete"))
_ALIASES = {"parent_id": "parent_id", "parent_session_id": "parent_id", "parent": "parent_id", "child_id": "child_id", "child_session_id": "child_id", "child": "child_id", "subagent_id": "child_id", "delegation_id": "delegation_id", "delegation": "delegation_id"}
class V4GatewayObserverViolation(ValueError): pass
def _fail(message: str) -> None:
    raise V4GatewayObserverViolation(message)
def _id(value: object, field: str = "identity") -> str:
    if not isinstance(value, str) or not value or len(value.encode()) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        _fail(f"{field} is not bounded")
    return value
def identity_hash(kind: str, value: str) -> str:
    if not isinstance(kind, str) or not _SAFE.fullmatch(kind):
        _fail("identity kind is not bounded")
    raw = _id(value, kind).encode()
    return hashlib.sha256(b"hermes-claude-agent-sdk:v4:gateway-observer\0" + kind.encode("ascii") + b"\0" + raw).hexdigest()
def _digest(value: object) -> tuple[int, str]:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, UnicodeError): _fail("observed frame is not bounded JSON")
    if len(raw) > 1_048_576: _fail("observed frame exceeds the bounded size")
    return len(raw), hashlib.sha256(raw).hexdigest()
def _find(value: object, keys: Iterable[str], depth: int = 0) -> object | None:
    if depth > 4: return None
    wanted = tuple(keys)
    if isinstance(value, Mapping):
        for key in wanted:
            if key in value: return value[key]
        for child in value.values():
            found = _find(child, wanted, depth + 1)
            if found is not None: return found
    elif isinstance(value, (list, tuple)):
        for child in value[:32]:
            found = _find(child, wanted, depth + 1)
            if found is not None: return found
    return None
def _parts(value: object) -> tuple[Mapping[str, Any], str, int, str, str | None]:
    if not isinstance(value, Mapping) or value.get("jsonrpc") != "2.0" or value.get("method") != "event": _fail("observed frame is not a Gateway event")
    params = value.get("params")
    if not isinstance(params, Mapping) or not isinstance(params.get("type"), str) or not _SAFE.fullmatch(params["type"]): _fail("event type is not bounded")
    kind = params["type"]
    size, digest = _digest(value)
    payload = params.get("payload") if isinstance(params.get("payload"), Mapping) else {}
    lowered = kind.casefold()
    terminal = None if lowered in _CHILD else payload.get("terminal_outcome")
    if terminal is None and (kind.casefold() in {"terminal", "message.complete", "session.complete", "run.complete", "task.complete"} or kind.casefold().endswith((".terminal", ".finished", ".done"))):
        terminal = _TERMINALS.get(payload.get("status")) if isinstance(payload.get("status"), str) else None
    if terminal is not None and terminal not in {"completed", "denied", "failed", "cancelled"}: _fail("event terminal status is unsupported")
    return params, kind, size, digest, terminal
def _root(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)): _fail("fixture root is invalid")
    root = Path(value)
    if not root.is_absolute() or ".." in root.parts or root.is_symlink(): _fail("fixture root is not an existing task-local directory")
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError): _fail("fixture root is not an existing task-local directory")
    if not resolved.is_dir() or resolved == Path(resolved.anchor) or resolved.is_symlink(): _fail("fixture root is not an existing task-local directory")
    return resolved
def snapshot_fixture_state(task_root: str | Path, *, max_bytes: int = MAX_FIXTURE_BYTES) -> dict[str, object]:
    if type(max_bytes) is not int or not 1 <= max_bytes <= 1_048_576: _fail("fixture state bound is invalid")
    path = _root(task_root) / STATE_FILE
    if path.is_symlink(): _fail("fixture state is a symlink")
    if not path.exists(): return {"exists": False, "size": 0, "sha256": None, "record_count": 0}
    if not path.is_file(): _fail("fixture state is not a regular file")
    try:
        size, content = path.stat().st_size, path.read_bytes()
        if size > max_bytes or len(content) > max_bytes:
            _fail("fixture state exceeds the bounded size")
        parsed = json.loads(content.decode())
    except V4GatewayObserverViolation:
        raise
    except (OSError, UnicodeError, ValueError, TypeError):
        _fail("fixture state is malformed")
    fields = {"schema_version", "record_count", "item_count", "item_hash", "operation_hash"}
    if not isinstance(parsed, dict) or set(parsed) != fields:
        _fail("fixture state shape is invalid")
    count = parsed["record_count"]
    if parsed["schema_version"] != 1 or type(count) is not int or not 0 <= count < 32 or type(parsed["item_count"]) is not int or not 0 <= parsed["item_count"] <= 32:
        _fail("fixture state values are invalid")
    if not all(isinstance(parsed[name], str) and _HEX64.fullmatch(parsed[name]) for name in ("item_hash", "operation_hash")):
        _fail("fixture state digest is invalid")
    return {"exists": True, "size": len(content), "sha256": hashlib.sha256(content).hexdigest(), "record_count": count}
fixture_state_snapshot = snapshot_fixture_state
class V4GatewayObserver:
    def __init__(self, gateway: Any, *, session_id: str | None = None, allowed_tool_names: Iterable[str] | None = None, fixture_root: str | Path | None = None, max_events: int = MAX_OBSERVED_EVENTS) -> None:
        if type(max_events) is not int or not 1 <= max_events <= MAX_OBSERVED_EVENTS:
            _fail("observer event bound is invalid")
        self._gateway = gateway
        self._session_hash = identity_hash("session_id", session_id) if session_id is not None else None
        names = set(allowed_tool_names) if allowed_tool_names is not None else set(getattr(gateway, "_hosts", ())) | set(getattr(gateway, "_mcps", ()))
        if allowed_tool_names is None and not names: names = {"memory", "session_search", "skills", "browser", "cron", "terminal", "process_manage", "delegate_task"}
        if any(not isinstance(name, str) or not _SAFE.fullmatch(name) or name.casefold() in {"agent", "bash", "read", "write", "edit", "web"} for name in names):
            _fail("tool inventory is invalid")
        self._allowed, self._fixture_root, self._limit = frozenset(names), _root(fixture_root) if fixture_root is not None else None, max_events
        self._events, self._kinds, self._starts, self._completes, self._children = [], Counter(), [], [], []
        self._active_tools, self._parent_hash = {}, None
        self._phases, self._child_complete = {}, set()
        self._task_count = self._pending_hash = self._pending = self._terminal = None
        self._requests, self._responses, self._denied, self._denial_free, self._recovery, self._denial_guard = [], [], False, False, False, False
        self._fixture, self._failed = None, False
    def _guard(self) -> None:
        if self._failed:
            _fail("observer is closed after an observation violation")
    def _session(self, value: object) -> None:
        found = value if isinstance(value, str) else _find(value, ("session_id",))
        if found is None: return
        digest = identity_hash("session_id", _id(found, "session_id"))
        if self._session_hash is not None and digest != self._session_hash: _fail("session identity does not match")
        self._session_hash = digest
    def _call_check(self, method: str, params: Mapping[str, Any]) -> str | None:
        sid = params.get("session_id")
        if sid is not None:
            digest = identity_hash("session_id", _id(sid, "session_id"))
            if self._session_hash is None:
                self._session_hash = digest
            elif digest != self._session_hash:
                _fail("call session identity does not match")
        if method != "approval.respond":
            return None
        if self._pending_hash is None:
            _fail("approval response has no pending request")
        request_id = params.get("request_id", params.get("approval_id"))
        if identity_hash("approval_id", _id(request_id, "approval request")) != self._pending_hash:
            _fail("approval response does not match the pending request")
        choice = params.get("choice", params.get("decision", "other"))
        if not isinstance(choice, str) or len(choice) > 64:
            _fail("approval choice is invalid")
        choice = choice.casefold()
        return {"allow": "allow", "approve": "allow", "yes": "allow", "deny": "deny", "reject": "deny", "no": "deny", "cancel": "deny"}.get(choice) or _fail("approval choice is unsupported")
    def _response(self, decision: str, value: object) -> None:
        resolved = value.get("resolved") if isinstance(value, Mapping) else value
        if isinstance(value, Mapping) and isinstance(value.get("result"), Mapping):
            resolved = value["result"].get("resolved")
        if type(resolved) is not int or resolved != 1:
            _fail("approval response did not resolve")
        self._responses.append({"decision_class": decision, "resolved": 1})
        self._pending_hash = self._pending = None
        if decision == "deny":
            self._denied, self._denial_free, self._denial_guard = True, True, True
        elif decision == "allow" and self._denied:
            self._recovery, self._denial_guard = True, False
    def _call_frame(self, method: str, decision: str | None, value: object) -> None:
        if method == "session.create":
            self._session(value)
        if method == "approval.respond" and decision is not None:
            self._response(decision, value)
    def call(self, method: str, params: Mapping[str, Any] | None = None, *, timeout: float = 30.0, projector: Callable[[object], object] | None = None) -> Mapping[str, Any]:
        self._guard()
        if not isinstance(method, str) or not _SAFE.fullmatch(method) or params is not None and not isinstance(params, Mapping):
            _fail("Gateway call is invalid")
        values, seen = dict(params or {}), False
        try:
            decision = self._call_check(method, values)
            def composed(value: object) -> object:
                nonlocal seen
                seen = True
                try: self._call_frame(method, decision, value)
                except V4GatewayObserverViolation: self._failed = True; raise
                return projector(value) if projector is not None else None
            result = self._gateway.call(method, values, timeout=timeout, projector=composed)
            if not seen:
                self._call_frame(method, decision, result)
            return result
        except V4GatewayObserverViolation:
            self._failed = True
            raise
    rpc = call
    def _event(self, value: object) -> None:
        params, kind, size, digest, terminal = _parts(value)
        if self._terminal is not None:
            _fail("event arrived after terminal")
        sid = params.get("session_id")
        if sid not in (None, ""):
            self._session(sid)
        if len(self._events) >= self._limit:
            _fail("event sequence exceeds the bounded count")
        lowered = kind.casefold()
        self._events.append({"kind": kind, "byte_length": size, "sha256": digest, "terminal_status": terminal})
        self._kinds[kind] += 1
        if lowered in _APPROVAL:
            if self._pending_hash is not None:
                _fail("approval request arrived before its response")
            request_id = _id(_find(value, ("request_id", "approval_id", "id")), "approval request")
            request_hash = identity_hash("approval_id", request_id)
            item = {"kind": kind, "byte_length": size, "sha256": digest, "request_id_sha256": request_hash}
            self._requests.append(item)
            self._pending_hash, self._pending = request_hash, item
        if lowered in _START or lowered in _COMPLETE:
            if self._pending_hash is not None or self._denial_guard:
                _fail("approval did not permit a tool event")
            name = _id(_find(value, ("name", "tool_name", "tool")), "tool name")
            if name not in self._allowed:
                _fail("tool name is outside the permitted inventory")
            call_hash = identity_hash("tool_call_id", _id(_find(value, ("tool_call_id", "call_id")), "tool call"))
            if lowered in _START:
                if call_hash in self._active_tools: _fail("tool call id was started twice")
                self._active_tools[call_hash] = name
                self._starts.append(name)
            elif call_hash not in self._active_tools or self._active_tools.pop(call_hash) != name:
                _fail("tool completion has no matching start")
            else:
                self._completes.append(name)
        index, count = _find(value, ("task_index",)), _find(value, ("task_count",))
        phase = "spawn_requested" if "spawn" in lowered and "request" in lowered else "start" if lowered.endswith((".start", ".started")) else "complete" if lowered.endswith((".complete", ".completed", ".result", ".done")) else None
        if lowered in _CHILD:
            previous = self._phases.get(index) if type(index) is int else None
            ordered = previous is None and phase == "spawn_requested" and index == len(self._phases) or previous is not None and phase is not None and ("spawn_requested", "start", "complete").index(phase) == ("spawn_requested", "start", "complete").index(previous) + 1
            if type(index) is not int or type(count) is not int or phase is None or count < 1 or not 0 <= index < count or not ordered or self._task_count not in (None, count):
                _fail("subagent task ordinals are not contiguous")
            self._task_count = count
            self._phases[index] = phase
            if phase == "complete": self._child_complete.add(index)
            item: dict[str, object] = {"task_index": index, "task_count": count, "phase": phase}
            for source, key in (("parent_id", "parent_id_sha256"), ("child_id", "child_id_sha256"), ("delegation_id", "delegation_id_sha256")):
                aliases = [alias for alias, mapped in _ALIASES.items() if mapped == source and _find(value, (alias,)) is not None]
                hashes = {identity_hash(source, _id(_find(value, (alias,)), source)) for alias in aliases}
                if len(hashes) > 1:
                    _fail("subagent identities disagree")
                if hashes:
                    item[key] = next(iter(hashes))
            if any(item[key] != old[key] for old in self._children if old["task_index"] == index for key in ("parent_id_sha256", "child_id_sha256", "delegation_id_sha256") if key in item and key in old):
                _fail("subagent identity changed during lifecycle")
            if "parent_id_sha256" in item:
                if self._parent_hash not in (None, item["parent_id_sha256"]): _fail("subagent parent identity changed")
                self._parent_hash = item["parent_id_sha256"]
            self._children.append(item)
        if terminal is not None:
            if self._pending_hash is not None or self._active_tools or self._task_count is not None and len(self._child_complete) != self._task_count or self._task_count and self._task_count > 1 and len({item["task_index"] for item in self._children if item.get("parent_id_sha256") == self._parent_hash}) != self._task_count:
                _fail("terminal event has incomplete observed evidence")
            self._terminal = terminal
    def next_event(self, *, timeout: float = 30.0, projector: Callable[[object], object] | None = None) -> EventProjection | Mapping[str, Any]:
        self._guard()
        seen = False
        def composed(value: object) -> object:
            nonlocal seen
            seen = True
            try: self._event(value)
            except V4GatewayObserverViolation: self._failed = True; raise
            return projector(value) if projector is not None else None
        try:
            result = self._gateway.next_event(timeout=timeout, projector=composed)
            if not seen and isinstance(result, Mapping):
                self._event(result)
            return result
        except V4GatewayObserverViolation:
            self._failed = True
            raise
    next_message = next_event
    def wait_event(self, event_type: str, *, session_id: str | None = None, timeout: float = 30.0, projector: Callable[[object], object] | None = None) -> EventProjection | Mapping[str, Any]:
        self._guard()
        if session_id is not None:
            self._session({"session_id": _id(session_id, "session_id")})
        seen = False
        def composed(value: object) -> object:
            nonlocal seen
            seen = True
            try: self._event(value)
            except V4GatewayObserverViolation: self._failed = True; raise
            return projector(value) if projector is not None else None
        try:
            result = self._gateway.wait_event(event_type, session_id=session_id, timeout=timeout, projector=composed)
            if not seen and isinstance(result, Mapping):
                self._event(result)
            return result
        except V4GatewayObserverViolation:
            self._failed = True
            raise
    wait_for_event = wait_event
    def fixture_snapshot(self, task_root: str | Path | None = None, *, max_bytes: int = MAX_FIXTURE_BYTES) -> dict[str, object]:
        root = self._fixture_root if task_root is None else _root(task_root)
        if root is None:
            _fail("fixture root is required")
        self._fixture = snapshot_fixture_state(root, max_bytes=max_bytes)
        return dict(self._fixture)
    snapshot_fixture_state = fixture_snapshot
    def collect_delegation_observation(self) -> dict[str, object]:
        self._guard()
        children = [{"task_index": index, "task_count": self._task_count, "phases": [item["phase"] for item in self._children if item["task_index"] == index], **{key: next(iter(values)) for key in ("parent_id_sha256", "child_id_sha256", "delegation_id_sha256") if (values := {item[key] for item in self._children if item["task_index"] == index and key in item}) and len(values) == 1}} for index in sorted(self._phases)]
        if any(child["phases"] != ["spawn_requested", "start", "complete"] for child in children) or len(children) > 1 and len({child.get("parent_id_sha256") for child in children}) != 1: _fail("child projection is not closed")
        parents = [item["parent_id_sha256"] for item in children if "parent_id_sha256" in item]
        return {"count": len(children), "children": children, "background_count": sum("background" in str(event["kind"]).casefold() for event in self._events), "lifecycle": "none" if not children else self._terminal or "running", "parent_link_sha256": parents[0] if len(set(parents)) == 1 else None}
    def snapshot(self, *, require_terminal: bool = True) -> dict[str, object]:
        self._guard()
        if self._pending_hash is not None or Counter(self._starts) != Counter(self._completes) or require_terminal and self._terminal is None:
            _fail("observation is incomplete")
        return {"schema_version": 1, "event_count": len(self._events), "event_kinds": dict(sorted(self._kinds.items())), "events": [dict(event) for event in self._events], "approval": {"request_count": len(self._requests), "response_count": len(self._responses), "requests": [dict(item) for item in self._requests], "responses": [dict(item) for item in self._responses], "denial_tool_free": self._denial_free, "recovery_succeeded": self._denied and self._recovery and self._terminal == "completed"}, "tools": {"started": list(self._starts), "completed": list(self._completes)}, "subagents": [dict(item) for item in self._children], "terminal_status": self._terminal, "terminal_count": 1 if self._terminal is not None else 0, "fixture_state": None if self._fixture is None else dict(self._fixture)}
    observation = snapshot
    collect_observation = snapshot
    def start(self, *args: Any, **kwargs: Any) -> Any:
        self._guard()
        return self._gateway.start(*args, **kwargs)
    def close(self, *args: Any, **kwargs: Any) -> Any:
        return self._gateway.close(*args, **kwargs)
GatewayObserver = V4GatewayObserver
__all__ = ["GatewayObserver", "MAX_FIXTURE_BYTES", "STATE_FILE", "V4GatewayObserver", "V4GatewayObserverViolation", "fixture_state_snapshot", "identity_hash", "snapshot_fixture_state"]
