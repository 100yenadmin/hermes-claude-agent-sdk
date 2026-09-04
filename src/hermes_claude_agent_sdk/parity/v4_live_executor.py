"""One provider-free, map-bound normal-Hermes v4 live attempt."""
from __future__ import annotations
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from .hashing import sha256_value
from .v4_contract import (
    OWNERSHIP_PREFLIGHTS, V4_CLI_VERSION, V4_MODEL, V4_RUNNER_ID,
    V4_RUNNER_VERSION, V4_SDK_DISTRIBUTION, V4_SDK_VERSION,
)
from .v4_gateway import EventAccumulator, EventProjection, GatewayClosed, GatewayTimeout, MAX_EVENTS
from .v4_live_map import (
    TOTAL_CALL_COUNT, TURN_BUDGET, load_v4_live_execution_map,
    validate_v4_live_execution_map,
)
_HEX40, _HEX64 = re.compile(r"^[0-9a-f]{40}$"), re.compile(r"^[0-9a-f]{64}$")
_RAW = frozenset({"raw_prompt", "raw_content", "raw_transcript", "session_id", "credential", "token", "api_key", "password", "cookie"})
_CANDIDATE = frozenset({"plugin_sha", "host_sha", "wheel_sha256", "profile_sha256", "sdk_distribution", "sdk_version", "cli_version", "model", "runner_id", "runner_version"})
_SESSION = frozenset({"cols", "cwd", "hidden", "source", "title"})
_APPROVAL = frozenset({"approval.request", "approval.requested"})
_RESULT_KINDS = frozenset({"null", "boolean", "number", "string", "array", "object"})
_MAX_RESULT_BYTES = 1_048_576
CONTROL_CALL_LIMIT = 16
class V4LiveExecutorViolation(ValueError):
    """An attempt cannot be admitted or did not close safely."""
class V4LiveGateway(Protocol):
    """Structural subset implemented by the frozen gateway and test fakes."""
    def start(self) -> None: ...
    def call(self, method: str, params: Mapping[str, Any], **kwargs: Any) -> Mapping[str, Any]: ...
    def next_event(self, **kwargs: Any) -> EventProjection | Mapping[str, Any]: ...
    def close(self) -> None: ...
@dataclass(frozen=True, slots=True)
class V4AttemptIdentity:
    candidate_hash: str
    preflight_hash: str
    live_map_sha256: str
    source_pack: str
    source_item_id: str
    predecessor_execution_id: str
    path: str
    trial_index: int
    @property
    def row_key(self) -> str: return f"{self.source_pack}/{self.source_item_id}"

    def to_dict(self) -> dict[str, object]:
        return {"candidate_hash": self.candidate_hash, "preflight_hash": self.preflight_hash, "live_map_sha256": self.live_map_sha256, "row_key": self.row_key, "predecessor_execution_id": self.predecessor_execution_id, "path": self.path, "trial_index": self.trial_index}
def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping): raise V4LiveExecutorViolation(f"{field} must be a mapping")
    return dict(value)
def _safe_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in value) or any(mark in value.casefold().replace("-", "_") for mark in _RAW):
        raise V4LiveExecutorViolation(f"{field} is not bounded")
    return value
def _reject_raw(value: Any, location: str = "value", depth: int = 0) -> None:
    if depth > 8: raise V4LiveExecutorViolation(f"{location} is too deeply nested")
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str): raise V4LiveExecutorViolation(f"{location} has an invalid key")
            lowered = key.casefold().replace("-", "_")
            if any(mark in lowered for mark in _RAW) or lowered.startswith("raw_") or lowered.endswith("_raw"): raise V4LiveExecutorViolation(f"{location} contains forbidden data")
            _reject_raw(child, f"{location}.{key}", depth + 1)
    elif isinstance(value, list):
        for index, child in enumerate(value): _reject_raw(child, f"{location}[{index}]", depth + 1)
    elif isinstance(value, str) and len(value.encode()) > 1_048_576:
        raise V4LiveExecutorViolation(f"{location} is too large")

def _candidate(value: Any) -> tuple[dict[str, str], str]:
    candidate = _mapping(value, "candidate")
    if set(candidate) != _CANDIDATE: raise V4LiveExecutorViolation("candidate fields are not closed")
    _reject_raw(candidate, "candidate")
    for field, size in (("plugin_sha", 40), ("host_sha", 40), ("wheel_sha256", 64), ("profile_sha256", 64)):
        pattern = _HEX40 if size == 40 else _HEX64
        if not isinstance(candidate[field], str) or pattern.fullmatch(candidate[field]) is None or candidate[field] == "0" * size: raise V4LiveExecutorViolation(f"candidate.{field} is invalid")
    expected = {"sdk_distribution": V4_SDK_DISTRIBUTION, "sdk_version": V4_SDK_VERSION, "cli_version": V4_CLI_VERSION, "model": V4_MODEL, "runner_id": V4_RUNNER_ID, "runner_version": V4_RUNNER_VERSION}
    if any(candidate.get(field) != expected[field] for field in expected): raise V4LiveExecutorViolation("candidate target is not frozen v4")
    normalized = {field: _safe_id(candidate[field], f"candidate.{field}") for field in candidate}
    return normalized, sha256_value(normalized)

def _preflight_hash(value: Any, candidate_hash: str) -> str:
    projections = _mapping(value, "preflight_projections")
    if set(projections) != set(OWNERSHIP_PREFLIGHTS): raise V4LiveExecutorViolation("preflight projections are incomplete")
    identities = {}
    for name in OWNERSHIP_PREFLIGHTS:
        item = _mapping(projections[name], f"preflight.{name}"); _reject_raw(item, f"preflight.{name}")
        if set(item) != {"schema_version", "name", "candidate_hash", "status", "source", "observation"} or item.get("schema_version") != 1 or item.get("name") != name or item.get("status") != "PASS" or item.get("candidate_hash") != candidate_hash: raise V4LiveExecutorViolation(f"preflight.{name} is not bound")
        source, observation = _mapping(item.get("source"), f"preflight.{name}.source"), _mapping(item.get("observation"), f"preflight.{name}.observation")
        if set(source) != {"executable", "source_ref", "test_id"} or not observation: raise V4LiveExecutorViolation(f"preflight.{name} metadata is incomplete")
        try: identities[name] = {"candidate_hash": candidate_hash, "status": "PASS", "source_hash": sha256_value(source), "observation_hash": sha256_value(observation)}
        except (TypeError, ValueError): raise V4LiveExecutorViolation(f"preflight.{name} metadata is not hashable") from None
    return sha256_value(identities)

def _event_type(event: Mapping[str, Any]) -> str | None:
    params = event.get("params")
    return params.get("type") if isinstance(params, Mapping) and isinstance(params.get("type"), str) else None

def _approval_id(event: Mapping[str, Any]) -> str:
    params = event.get("params"); payload = params.get("payload") if isinstance(params, Mapping) else None
    for source in (params, payload):
        if isinstance(source, Mapping):
            for key in ("request_id", "approval_id", "id"):
                if isinstance(source.get(key), str) and source[key]: return _safe_id(source[key], "approval_id")
    raise V4LiveExecutorViolation("approval request lacks a usable ID")

def _approval_request_projection(projection: EventProjection) -> dict[str, object]:
    """Keep only the gateway's content-free approval event projection."""
    if (
        not isinstance(projection.event_type, str)
        or not projection.event_type
        or type(projection.byte_length) is not int
        or not 0 <= projection.byte_length <= _MAX_RESULT_BYTES
        or not isinstance(projection.sha256, str)
        or _HEX64.fullmatch(projection.sha256) is None
    ):
        raise V4LiveExecutorViolation("approval request projection is invalid")
    return {
        "kind": projection.event_type,
        "byte_length": projection.byte_length,
        "sha256": projection.sha256,
    }

def _approval_decision_projection(response: Mapping[str, Any], decision_class: str) -> dict[str, object]:
    """Keep only the safe result envelope returned by ``approval.respond``."""
    ok, result_kind, result_bytes, result_sha256 = (
        response.get("ok"),
        response.get("result_kind"),
        response.get("result_bytes"),
        response.get("result_sha256"),
    )
    if (
        type(ok) is not bool
        or not isinstance(result_kind, str)
        or result_kind not in _RESULT_KINDS
        or type(result_bytes) is not int
        or not 0 <= result_bytes <= _MAX_RESULT_BYTES
        or not isinstance(result_sha256, str)
        or _HEX64.fullmatch(result_sha256) is None
    ):
        raise V4LiveExecutorViolation("approval response envelope is invalid")
    return {
        "decision_class": decision_class,
        "ok": ok,
        "result_kind": result_kind,
        "result_bytes": result_bytes,
        "result_sha256": result_sha256,
    }

class V4LiveExecutor:
    """Admit and execute exactly one normal-Hermes gateway attempt."""

    def __init__(self, *, gateway: V4LiveGateway, candidate: Mapping[str, Any], preflight_projections: Mapping[str, Mapping[str, Any]], live_map: Mapping[str, Any] | str | Path, map_path: str | Path | None = None, source_pack: str, source_item_id: str, path: str, trial_index: int, planned_calls: int = 1, planned_turns: int = TURN_BUDGET, expected_live_map_sha256: str | None = None, session_params: Mapping[str, Any] | None = None) -> None:
        normalized, candidate_hash = _candidate(candidate); preflight_hash = _preflight_hash(preflight_projections, candidate_hash)
        try:
            if isinstance(live_map, (str, Path)):
                map_source = Path(live_map).expanduser().resolve(); document = load_v4_live_execution_map(map_source)
            else:
                document = _mapping(live_map, "live_map"); map_source = Path(map_path).expanduser().resolve() if map_path is not None else Path(__file__).resolve().parents[3] / "qa" / "parity-v4-live-execution-map.yaml"
        except Exception: raise V4LiveExecutorViolation("live map could not be loaded") from None
        try: accounting = validate_v4_live_execution_map(document, map_path=map_source)
        except Exception: raise V4LiveExecutorViolation("live map validation failed") from None
        map_hash = accounting.get("map_sha256")
        if not isinstance(map_hash, str) or not _HEX64.fullmatch(map_hash) or expected_live_map_sha256 is not None and expected_live_map_sha256 != map_hash: raise V4LiveExecutorViolation("live map identity is invalid")
        if type(planned_calls) is not int or not 1 <= planned_calls <= TOTAL_CALL_COUNT or type(planned_turns) is not int or not 1 <= planned_turns <= TURN_BUDGET: raise V4LiveExecutorViolation("attempt budget exceeds v4 envelope")
        pack, item = _safe_id(source_pack, "source_pack"), _safe_id(source_item_id, "source_item_id"); row_key = f"{pack}/{item}"
        rows = {f"{row['source_pack']}/{row['source_item_id']}": row for row in document.get("rows", []) if isinstance(row, Mapping)}; row = rows.get(row_key)
        if not isinstance(path, str) or path not in {"positive", "denial", "recovery"}: raise V4LiveExecutorViolation("attempt path is unsupported")
        if row is None or path not in row.get("mandatory_paths", []) or type(trial_index) is not int or trial_index not in row.get("required_trial_indexes", []): raise V4LiveExecutorViolation("attempt path or trial is not mandatory")
        if path != "positive":
            raise V4LiveExecutorViolation(
                "provider execution is admitted only for the positive path"
            )
        params = dict(session_params or {})
        if set(params) - _SESSION: raise V4LiveExecutorViolation("session parameters contain unsupported fields")
        _reject_raw(params, "session_params")
        self._gateway, self._candidate = gateway, normalized; self._identity = V4AttemptIdentity(candidate_hash, preflight_hash, map_hash, pack, item, _safe_id(row.get("predecessor_execution_id"), "predecessor_execution_id"), path, trial_index)
        self._planned_provider_calls, self._session_params, self._control_calls, self._provider_calls, self._approval_count, self._used = planned_calls, params, 0, 0, 0, False
    @property
    def identity(self) -> V4AttemptIdentity: return self._identity
    def _call(self, method: str, params: Mapping[str, Any], projector: Any = None) -> Mapping[str, Any]:
        self._control_calls += 1
        if self._control_calls > CONTROL_CALL_LIMIT: raise V4LiveExecutorViolation("control call budget exhausted")
        if method == "prompt.submit" and self._provider_calls >= self._planned_provider_calls: raise V4LiveExecutorViolation("provider call budget exhausted")
        try: result = self._gateway.call(method, params, projector=projector)
        except Exception: raise V4LiveExecutorViolation("gateway call failed") from None
        if not isinstance(result, Mapping): raise V4LiveExecutorViolation("gateway call returned an invalid envelope")
        expected = (("candidate_hash", self._identity.candidate_hash), ("preflight_hash", self._identity.preflight_hash), ("live_map_sha256", self._identity.live_map_sha256), ("row_key", self._identity.row_key), ("path", self._identity.path), ("trial_index", self._identity.trial_index))
        for envelope in (result, result.get("identity")):
                if isinstance(envelope, Mapping) and any(key in envelope and envelope[key] != value for key, value in expected): raise V4LiveExecutorViolation("gateway response identity does not match")
        if method == "prompt.submit": self._provider_calls += 1
        return result
    def _next(self, session_id: str, choice: str, decision_class: str, accumulator: EventAccumulator, captured: dict[str, Mapping[str, Any]], approval_receipts: list[dict[str, dict[str, object]]], *, timeout: float | None = None) -> EventProjection:
        def project(raw: object) -> None:
            if isinstance(raw, Mapping): captured["value"] = raw
        try:
            kwargs = {"projector": project}
            if timeout is not None:
                kwargs["timeout"] = timeout
            value = self._gateway.next_event(**kwargs)
        except Exception: raise V4LiveExecutorViolation("gateway event sequence failed") from None
        raw = captured.get("value")
        if raw is not None:
            params = raw.get("params")
            if isinstance(params, Mapping) and params.get("session_id") is not None and params.get("session_id") != "" and params.get("session_id") != session_id: raise V4LiveExecutorViolation("event session identity does not match")
        if isinstance(value, EventProjection): projection = value; kind = value.event_type
        elif isinstance(value, Mapping): projection = accumulator.add(value); kind = _event_type(value)
        else: raise V4LiveExecutorViolation("gateway event has an invalid shape")
        if kind in _APPROVAL:
            if raw is None: raise V4LiveExecutorViolation("approval request lacks a usable ID")
            approval = _approval_id(raw); params = {"session_id": session_id, "choice": choice, "request_id": approval}
            response = self._call("approval.respond", params)
            approval_receipts.append({
                "request": _approval_request_projection(projection),
                "decision": _approval_decision_projection(response, decision_class),
            })
            self._approval_count += 1
        return projection

    def _execute_turn(self, prompt: str | None, *, session_id: str, approval_choice: str, submit_prompt: bool = True, expect_followup: bool = False) -> dict[str, Any]:
        terminal: str | None = None; events: list[EventProjection] = []; accumulator = EventAccumulator(); approval_receipts: list[dict[str, dict[str, object]]] = []
        decision = approval_choice.casefold(); decision_class = "allow" if decision in {"allow", "approve", "yes"} else "deny" if decision in {"deny", "reject", "no", "cancel"} else "other"
        if submit_prompt:
            if not isinstance(prompt, str):
                raise V4LiveExecutorViolation("submitted turn requires a prompt")
            submitted_session: list[str] = []
            def project_submit(raw: object) -> None:
                if isinstance(raw, Mapping) and isinstance(raw.get("session_id"), str): submitted_session.append(raw["session_id"])
            submitted = self._call("prompt.submit", {"session_id": session_id, "text": prompt}, project_submit)
            returned_session = submitted_session[0] if submitted_session else submitted.get("session_id")
            if isinstance(returned_session, str) and returned_session != session_id: raise V4LiveExecutorViolation("prompt response session identity does not match")
        while terminal is None:
            captured: dict[str, Mapping[str, Any]] = {}; projection = self._next(session_id, approval_choice, decision_class, accumulator, captured, approval_receipts, timeout=600.0 if not submit_prompt else None); events.append(projection)
            if projection.terminal_status is not None: terminal = projection.terminal_status
            if len(events) > MAX_EVENTS: raise V4LiveExecutorViolation("event sequence exceeds the bounded count")
        if not expect_followup:
            try: trailing = self._gateway.next_event(timeout=0.01)
            except (GatewayClosed, GatewayTimeout, TimeoutError): trailing = None
            if trailing is not None: raise V4LiveExecutorViolation("event arrived after terminal")
        if terminal != "completed":
            raise V4LiveExecutorViolation(
                "positive provider execution did not complete"
            )
        if not submit_prompt:
            kinds = [event.event_type for event in events]
            starts = [index for index, kind in enumerate(kinds) if kind == "message.start"]
            if not starts or not any(kind in {"message.usage", "session.usage"} for kind in kinds[starts[0] + 1:]):
                raise V4LiveExecutorViolation("automatic Hermes delivery turn lacks start or usage proof")
            self._provider_calls = 1
        classification = "COMPLETE"
        counts = Counter(event.event_type for event in events)
        approval = {
            "decision_class": decision_class,
            "request_count": len(approval_receipts),
            "decision_count": self._approval_count,
            "requests": [item["request"] for item in approval_receipts],
            "decisions": [item["decision"] for item in approval_receipts],
        }
        return {"identity": self._identity.to_dict(), "candidate": dict(self._candidate), "classification": classification, "terminal_status": terminal, "event_count": len(events), "event_kinds": dict(sorted(counts.items())), "events": [event.to_dict() for event in events], "control_calls_used": self._control_calls, "provider_calls": self._provider_calls, "turns_used": 1, "approval": approval}

    def run_on_session(self, prompt: str, *, session_id: str, approval_choice: str = "deny", expect_followup: bool = False) -> dict[str, Any]:
        if self._used: raise V4LiveExecutorViolation("attempt is already terminal")
        self._used = True
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 1_048_576: raise V4LiveExecutorViolation("prompt is empty or too large")
        if not isinstance(approval_choice, str) or not approval_choice.strip() or len(approval_choice) > 64: raise V4LiveExecutorViolation("approval choice is invalid")
        session_id = _safe_id(session_id, "session_id")
        if type(expect_followup) is not bool:
            raise V4LiveExecutorViolation("follow-up expectation is invalid")
        return self._execute_turn(prompt, session_id=session_id, approval_choice=approval_choice, expect_followup=expect_followup)

    def observe_on_session(self, *, session_id: str, approval_choice: str = "deny") -> dict[str, Any]:
        """Observe one Hermes-owned automatic delivery turn without submitting input."""
        if self._used: raise V4LiveExecutorViolation("attempt is already terminal")
        self._used = True
        if not isinstance(approval_choice, str) or not approval_choice.strip() or len(approval_choice) > 64: raise V4LiveExecutorViolation("approval choice is invalid")
        session_id = _safe_id(session_id, "session_id")
        return self._execute_turn(None, session_id=session_id, approval_choice=approval_choice, submit_prompt=False)

    def run(self, prompt: str, *, approval_choice: str = "deny") -> dict[str, Any]:
        if self._used: raise V4LiveExecutorViolation("attempt is already terminal")
        self._used = True
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 1_048_576: raise V4LiveExecutorViolation("prompt is empty or too large")
        if not isinstance(approval_choice, str) or not approval_choice.strip() or len(approval_choice) > 64: raise V4LiveExecutorViolation("approval choice is invalid")
        session_id: str | None = None
        try:
            self._gateway.start(); captured_session: list[str] = []
            def project_session(raw: object) -> None:
                if isinstance(raw, Mapping) and isinstance(raw.get("session_id"), str): captured_session.append(raw["session_id"])
            created = self._call("session.create", self._session_params, project_session); session_id = captured_session[0] if captured_session else created.get("session_id")
            if not isinstance(session_id, str) or not session_id: raise V4LiveExecutorViolation("session identity was not returned")
            return self._execute_turn(prompt, session_id=session_id, approval_choice=approval_choice)
        finally:
            try: self._gateway.close()
            except Exception: raise V4LiveExecutorViolation("gateway close failed") from None
__all__ = ["V4AttemptIdentity", "V4LiveExecutor", "V4LiveExecutorViolation", "V4LiveGateway"]
