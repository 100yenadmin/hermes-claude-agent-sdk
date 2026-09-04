"""Provider-free binding of one observed v4 row/trial scenario bundle."""
from __future__ import annotations
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from .hashing import canonical_json_bytes, json_compatible, sha256_value
from .results import ResultPacket
from .v4_contract import (
    OWNERSHIP_PREFLIGHTS, V3_RESULT_CATALOG_HASH, V3_RESULT_CONTRACT_HASH,
    V4_CLI_VERSION, V4_MODEL, V4_RUNNER_ID, V4_RUNNER_VERSION,
    V4_SDK_DISTRIBUTION, V4_SDK_VERSION, required_trial_indexes,
    validate_v4_contract,
)
from .v4_evidence import bind_v4_evidence
from .v4_live_map import load_v4_live_execution_map, validate_v4_live_execution_map
from .v4_live_scenarios import (
    LIVE_MAP_SHA256, V4LiveScenario, V4LiveScenarioCatalog,
    build_v4_live_scenario_catalog, validate_v4_live_scenario_catalog,
)
from .v4_receipts import build_ownership_receipt
_HEX40, _HEX64 = re.compile(r"^[0-9a-f]{40}$"), re.compile(r"^[0-9a-f]{64}$")
_SAFE = re.compile(r"^[A-Za-z0-9_.:@/+#\-]{1,256}$")
_RAW = frozenset("raw raw_prompt raw_content raw_transcript messages prompt content transcript session session_id tool_call_id tool_calls tool_result tool_results approval_id request_id correlation_id credential credentials password secret secrets token access_token refresh_token api_key cookie cookies".split())
_ID = frozenset("candidate_hash preflight_hash live_map_sha256 row_key predecessor_execution_id path trial_index".split())
_CANDIDATE = frozenset("plugin_sha host_sha wheel_sha256 profile_sha256 sdk_distribution sdk_version cli_version model runner_id runner_version".split())
_ATTEMPT = frozenset("identity candidate classification terminal_status event_count event_kinds events control_calls_used provider_calls turns_used approval turn_index".split())
_EVENT = frozenset({"kind", "byte_length", "sha256", "terminal_status", "projection"})
_HOST = frozenset({"schema_version", "status", "runtime", "invariant_violations", "expected_turn_count", "transcript", "runtime_state", "runtime_usage"})
_LOCAL = frozenset({"schema_version", "status", "path", "host_local", "provider_calls", "terminal_status", "events", "observation", "proof_hashes"})
_DELEGATION = frozenset({"count", "background_count", "lifecycle", "parent_link_sha256"})
_TRACE = frozenset({"schema_version", "row_key", "predecessor_execution_id", "path", "trial_index", "events"})
_TRACE_EVENT = frozenset({"kind", "byte_length", "sha256", "terminal_status", "evidence"})
_TRACE_ATTEMPT_EVIDENCE = frozenset({"source", "attempt_index", "source_sha256"})
_TRACE_HOST_EVIDENCE = frozenset({"source", "component", "source_sha256"})
_TERMINALS = frozenset({"completed", "denied", "failed", "cancelled"})
_CONTENT = frozenset({"message.delta", "message.content", "content", "text", "message.text"})
class V4LivePacketViolation(ValueError):
    """A scenario bundle cannot form a safe immutable packet set."""
def _raw(value: Any, location: str = "value") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise V4LivePacketViolation(f"{location} has an invalid key")
            lowered = key.casefold().replace("-", "_")
            host_transcript = lowered == "transcript" and location.endswith("host_observation")
            sanitized_host_row = lowered in {"tool_call", "tool_result"} and location.endswith("transcript.canonical_rows")
            if (lowered in _RAW and not host_transcript and not sanitized_host_row) or lowered.startswith("raw_") or lowered.endswith("_raw"):
                raise V4LivePacketViolation(f"{location} contains forbidden raw data")
            _raw(child, f"{location}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _raw(child, f"{location}[{index}]")
def _copy(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise V4LivePacketViolation(f"{field} must be a mapping")
    try:
        result = json_compatible(value)
    except TypeError as exc:
        raise V4LivePacketViolation(f"{field} is not JSON-compatible") from exc
    if not isinstance(result, dict):
        raise V4LivePacketViolation(f"{field} must be a mapping")
    _raw(result, field)
    return result
def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SAFE.fullmatch(value) is None:
        raise V4LivePacketViolation(f"{field} is not a safe identifier")
    return value
def _digest(value: Any, field: str, size: int = 64) -> str:
    pattern = _HEX40 if size == 40 else _HEX64
    if not isinstance(value, str) or pattern.fullmatch(value) is None or value == "0" * size:
        raise V4LivePacketViolation(f"{field} is not a nonzero lowercase digest")
    return value
def _identity(value: Any, field: str = "identity") -> dict[str, Any]:
    result = _copy(value, field)
    if set(result) != _ID:
        raise V4LivePacketViolation(f"{field} fields are not closed")
    for key in ("candidate_hash", "preflight_hash", "live_map_sha256"):
        _digest(result[key], f"{field}.{key}")
    for key in ("row_key", "predecessor_execution_id"):
        _id(result[key], f"{field}.{key}")
    if result["path"] not in {"positive", "denial", "recovery"} or type(result["trial_index"]) is not int or result["trial_index"] < 1:
        raise V4LivePacketViolation(f"{field} path or trial is invalid")
    return result
def _candidate(value: Any) -> tuple[dict[str, Any], str]:
    result = _copy(value, "candidate")
    if set(result) != _CANDIDATE:
        raise V4LivePacketViolation("candidate fields are not closed")
    for key, size in (("plugin_sha", 40), ("host_sha", 40), ("wheel_sha256", 64), ("profile_sha256", 64)):
        _digest(result[key], f"candidate.{key}", size)
    expected = {"sdk_distribution": V4_SDK_DISTRIBUTION, "sdk_version": V4_SDK_VERSION, "cli_version": V4_CLI_VERSION, "model": V4_MODEL, "runner_id": V4_RUNNER_ID, "runner_version": V4_RUNNER_VERSION}
    if any(result[key] != item for key, item in expected.items()):
        raise V4LivePacketViolation("candidate target is not frozen v4")
    return result, sha256_value(result)
def _kind(value: Any) -> str:
    name = _id(value, "event.kind").casefold()
    exact = {
        "start": "start", "message.start": "start", "session.start": "start", "run.start": "start", "task.start": "start",
        "state": "state", "message.state": "state", "session.state": "state",
        "usage": "usage", "message.usage": "usage", "session.usage": "usage", "compaction": "compaction", "background": "background", "restart": "restart",
        "approval.request": "approval_requested", "approval.requested": "approval_requested", "approval_requested": "approval_requested", "approval.decision": "approval_decision", "approval.decided": "approval_decision", "approval.responded": "approval_decision", "approval_decision": "approval_decision",
        "tool.request": "tool_requested", "tool.requested": "tool_requested", "tool.start": "tool_requested", "tool_requested": "tool_requested", "tool.result": "tool_result", "tool.complete": "tool_result", "tool.completed": "tool_result", "tool_result": "tool_result",
        "terminal": "terminal", "message.complete": "terminal", "session.complete": "terminal", "run.complete": "terminal", "task.complete": "terminal",
    }
    if name in exact:
        return exact[name]
    if name.endswith((".terminal", ".finished", ".done")):
        return "terminal"
    raise V4LivePacketViolation("event projection kind is unsupported")
def _events(value: Any, classification: str) -> tuple[tuple[dict[str, Any], ...], dict[str, int], tuple[str, ...]]:
    if not isinstance(value, list) or not value:
        raise V4LivePacketViolation("event projection list is empty")
    result, counts, content, seen, terminals = [], {}, [], set(), []
    for ordinal, raw in enumerate(value, 1):
        item = _copy(raw, f"events[{ordinal - 1}]")
        if set(item) - _EVENT or not {"kind", "byte_length", "sha256", "terminal_status"} <= set(item):
            raise V4LivePacketViolation("event projection fields are not closed")
        raw_kind = _id(item["kind"], f"events[{ordinal - 1}].kind")
        digest = _digest(item["sha256"], f"events[{ordinal - 1}].sha256")
        if digest in seen:
            raise V4LivePacketViolation("event projections are reused")
        seen.add(digest)
        if type(item["byte_length"]) is not int or not 1 <= item["byte_length"] <= 1_048_576:
            raise V4LivePacketViolation("event projection byte length is invalid")
        counts[raw_kind] = counts.get(raw_kind, 0) + 1
        if raw_kind.casefold() in _CONTENT:
            if item["terminal_status"] is not None:
                raise V4LivePacketViolation("content projection carries a terminal")
            content.append(digest)
            continue
        kind = _kind(raw_kind); status = item["terminal_status"]
        if status is not None and status not in _TERMINALS:
            raise V4LivePacketViolation("event terminal status is unsupported")
        if kind == "terminal":
            if status is None:
                raise V4LivePacketViolation("terminal event lacks outcome")
            terminals.append(status)
        elif status is not None:
            raise V4LivePacketViolation("non-terminal event carries outcome")
        output = {"sequence": len(result) + 1, "kind": kind, "metadata_hash": digest}
        if kind == "terminal":
            output.update(status=status, terminal_outcome=status)
        else:
            output["status"] = "expected_negative" if classification == "EXPECTED_NEGATIVE" else "observed"
        result.append(output)
    expected = {"COMPLETE": "completed", "EXPECTED_NEGATIVE": "denied", "VERIFIED_FAILURE": None}.get(classification)
    if len(terminals) != 1 or expected is not None and terminals[0] != expected or classification == "VERIFIED_FAILURE" and terminals[0] not in {"failed", "cancelled"}:
        raise V4LivePacketViolation("terminal outcome does not match classification")
    mapped = {name: sum(item["kind"] == name for item in result) for name in ("approval_requested", "approval_decision", "tool_requested", "tool_result")}
    if mapped["tool_requested"] != mapped["tool_result"]:
        raise V4LivePacketViolation("tool event request/result pairing is incomplete")
    if mapped["approval_decision"] not in {0, mapped["approval_requested"]}:
        raise V4LivePacketViolation("approval event request/decision pairing is incomplete")
    return tuple(result), counts, tuple(content)
def _approval(value: Any) -> dict[str, Any]:
    result = _copy(value, "attempt.approval")
    fields = {"decision_class", "request_count", "decision_count", "requests", "decisions"}
    if set(result) != fields or result["decision_class"] not in {"allow", "deny", "other"} or type(result["request_count"]) is not int or type(result["decision_count"]) is not int or result["request_count"] != result["decision_count"] or result["request_count"] < 0 or not isinstance(result["requests"], list) or not isinstance(result["decisions"], list) or len(result["requests"]) != result["request_count"] or len(result["decisions"]) != result["decision_count"]:
        raise V4LivePacketViolation("approval receipt is not a closed request/respond projection")
    for request, decision in zip(result["requests"], result["decisions"], strict=True):
        if set(request) != {"kind", "byte_length", "sha256"} or _id(request["kind"], "approval.request.kind") is None or type(request["byte_length"]) is not int or not 0 <= request["byte_length"] <= 1_048_576:
            raise V4LivePacketViolation("approval request projection is invalid")
        _digest(request["sha256"], "approval.request.sha256")
        if set(decision) != {"decision_class", "ok", "result_kind", "result_bytes", "result_sha256"} or decision["decision_class"] != result["decision_class"] or type(decision["ok"]) is not bool or not isinstance(decision["result_kind"], str) or type(decision["result_bytes"]) is not int or not 0 <= decision["result_bytes"] <= 1_048_576:
            raise V4LivePacketViolation("approval response projection is invalid")
        _id(decision["result_kind"], "approval.decision.result_kind"); _digest(decision["result_sha256"], "approval.decision.result_sha256")
    return result
def _delegation(value: Any, scenario: V4LiveScenario, trial_index: int) -> dict[str, Any]:
    result = _copy(value, "delegation summary")
    if set(result) != _DELEGATION:
        raise V4LivePacketViolation("delegation summary fields are not closed")
    expected_count = sum(1 for _, bound_trial, _, _, path in scenario.child_bindings if bound_trial == trial_index and path == "positive")
    if type(result["count"]) is not int or not 0 <= result["count"] <= 10_000 or result["count"] != expected_count:
        raise V4LivePacketViolation("delegation count is not scenario-bound")
    if type(result["background_count"]) is not int or not 0 <= result["background_count"] <= result["count"]:
        raise V4LivePacketViolation("delegation background count is invalid")
    if result["lifecycle"] not in {"none", "pending", "running", "completed", "failed", "cancelled", "delivered", "dropped"}:
        raise V4LivePacketViolation("delegation lifecycle is unsupported")
    parent_hash = result["parent_link_sha256"]
    if parent_hash is not None:
        _digest(parent_hash, "delegation.parent_link_sha256")
    if result["count"] == 0 and (result["lifecycle"] != "none" or parent_hash is not None):
        raise V4LivePacketViolation("empty delegation summary carries an outcome")
    if result["count"] > 0 and (result["lifecycle"] == "none" or parent_hash is None):
        raise V4LivePacketViolation("delegation summary lacks lifecycle or parent-link proof")
    return result
def _preflight_hash(value: Any, candidate_hash: str) -> str:
    projections = _copy(value, "preflight_projections")
    if set(projections) != set(OWNERSHIP_PREFLIGHTS):
        raise V4LivePacketViolation("preflight projections are incomplete")
    identities = {}
    for name in OWNERSHIP_PREFLIGHTS:
        item = _copy(projections[name], f"preflight.{name}")
        if set(item) != {"schema_version", "name", "candidate_hash", "status", "source", "observation"} or item["schema_version"] != 1 or item["name"] != name or item["candidate_hash"] != candidate_hash or item["status"] != "PASS":
            raise V4LivePacketViolation("preflight projection is not an exact PASS")
        source, observation = _copy(item["source"], "preflight.source"), _copy(item["observation"], "preflight.observation")
        if set(source) != {"executable", "source_ref", "test_id"} or not observation:
            raise V4LivePacketViolation("preflight projection metadata is incomplete")
        identities[name] = {"candidate_hash": candidate_hash, "status": "PASS", "source_hash": sha256_value(source), "observation_hash": sha256_value(observation)}
    return sha256_value(identities)
def _attempt(value: Any, scenario: V4LiveScenario, candidate_hash: str) -> tuple[dict[str, Any], dict[str, Any], tuple[dict[str, Any], ...], tuple[str, ...]]:
    result = _copy(value, "positive attempt")
    if set(result) - _ATTEMPT or set(_ATTEMPT) - set(result) or "turn_index" not in result:
        raise V4LivePacketViolation("positive attempt fields are not closed")
    identity = _identity(result["identity"], "attempt.identity")
    candidate, digest = _candidate(result["candidate"])
    if digest != candidate_hash or identity["candidate_hash"] != candidate_hash or identity["path"] != "positive" or identity["row_key"] != scenario.row_key or identity["predecessor_execution_id"] != scenario.predecessor_execution_id or identity["trial_index"] not in scenario.trial_indexes or identity["live_map_sha256"] != LIVE_MAP_SHA256:
        raise V4LivePacketViolation("positive attempt identity is not scenario-bound")
    if type(result["turn_index"]) is not int or not 1 <= result["turn_index"] <= scenario.turn_count or result["classification"] != "COMPLETE" or result["terminal_status"] != "completed":
        raise V4LivePacketViolation("positive attempt is not a completed turn")
    approval = _approval(result["approval"])
    result["approval"] = approval
    if type(result["control_calls_used"]) is not int or not 0 <= result["control_calls_used"] <= 16 or type(result["provider_calls"]) is not int or result["provider_calls"] != 1 or type(result["turns_used"]) is not int or result["turns_used"] != 1:
        raise V4LivePacketViolation("positive attempt provider accounting is not one call/turn")
    if type(result["event_count"]) is not int or result["event_count"] != len(result["events"]):
        raise V4LivePacketViolation("positive attempt event count is not exact")
    events, raw_counts, content = _events(result["events"], "COMPLETE")
    if result["event_kinds"] != raw_counts:
        raise V4LivePacketViolation("positive attempt event kinds are not exact")
    if approval["decision_count"] != sum(count for kind, count in raw_counts.items() if kind.casefold() not in _CONTENT and _kind(kind) == "approval_requested"):
        raise V4LivePacketViolation("approval request count does not match event projection")
    return result, candidate, events, content
def _scenario_trace(value: Any, scenario: V4LiveScenario, expected_trace: Sequence[str], attempts: Sequence[Mapping[str, Any]], host: Mapping[str, Any]) -> tuple[tuple[dict[str, Any], ...], str]:
    trace = _copy(value, "scenario_trace")
    if set(trace) != _TRACE or trace["schema_version"] != 1 or trace["row_key"] != scenario.row_key or trace["predecessor_execution_id"] != scenario.predecessor_execution_id or trace["path"] != "positive" or trace["trial_index"] != attempts[0]["identity"]["trial_index"] or not isinstance(trace["events"], list) or len(trace["events"]) != len(expected_trace):
        raise V4LivePacketViolation("scenario trace envelope is not closed or row-bound")
    source_events = {(item["turn_index"], event["sha256"]): event for item in attempts for event in item["events"]}
    output, used, terminal_count = [], set(), 0
    for ordinal, raw in enumerate(trace["events"], 1):
        item = _copy(raw, f"scenario_trace.events[{ordinal - 1}]")
        if set(item) != _TRACE_EVENT:
            raise V4LivePacketViolation("scenario trace event fields are not closed")
        kind, digest = _kind(item["kind"]), _digest(item["sha256"], f"scenario_trace.events[{ordinal - 1}].sha256")
        evidence = _copy(item["evidence"], f"scenario_trace.events[{ordinal - 1}].evidence")
        source_key: tuple[str, str]
        if evidence.get("source") == "attempt":
            if set(evidence) != _TRACE_ATTEMPT_EVIDENCE or type(evidence["attempt_index"]) is not int or not 1 <= evidence["attempt_index"] <= len(attempts) or evidence["source_sha256"] != digest:
                raise V4LivePacketViolation("scenario trace evidence is not an attempt-bound projection")
            source = source_events.get((evidence["attempt_index"], digest))
            if source is None or _kind(source["kind"]) != kind or source["byte_length"] != item["byte_length"] or source["terminal_status"] != item["terminal_status"]:
                raise V4LivePacketViolation("scenario trace component is absent or mismatched")
            source_key = ("attempt", digest)
        elif evidence.get("source") == "host_observation":
            if set(evidence) != _TRACE_HOST_EVIDENCE or evidence.get("component") not in {"runtime_state", "runtime_usage"} or evidence["source_sha256"] != digest:
                raise V4LivePacketViolation("scenario trace evidence is not a host-bound projection")
            component_name = evidence["component"]
            if component_name == "runtime_state":
                source = host.get("runtime_state")
                expected_kind = "state"
                if not isinstance(source, Mapping) or source.get("present") is not True:
                    raise V4LivePacketViolation("scenario trace host state evidence is absent")
            else:
                usage = host.get("runtime_usage")
                source = usage.get("latest") if isinstance(usage, Mapping) else None
                expected_kind = "usage"
                if not isinstance(source, Mapping):
                    raise V4LivePacketViolation("scenario trace host usage evidence is absent")
            if kind != expected_kind or source.get("sha256") != digest or len(canonical_json_bytes(source)) != item["byte_length"] or item["terminal_status"] is not None:
                raise V4LivePacketViolation("scenario trace host component is mismatched")
            source_key = (f"host:{component_name}", digest)
        else:
            raise V4LivePacketViolation("scenario trace evidence source is unsupported")
        if source_key in used:
            raise V4LivePacketViolation("scenario trace component is reused")
        if type(item["byte_length"]) is not int or not 1 <= item["byte_length"] <= 1_048_576:
            raise V4LivePacketViolation("scenario trace byte length is invalid")
        if kind == "terminal":
            if item["terminal_status"] != "completed":
                raise V4LivePacketViolation("scenario trace terminal is not completed")
            terminal_count += 1
            event = {"sequence": ordinal, "kind": kind, "metadata_hash": digest, "status": "completed", "terminal_outcome": "completed"}
        elif item["terminal_status"] is not None:
            raise V4LivePacketViolation("scenario trace non-terminal carries an outcome")
        else:
            event = {"sequence": ordinal, "kind": kind, "metadata_hash": digest, "status": "observed"}
        if kind != expected_trace[ordinal - 1]:
            raise V4LivePacketViolation("scenario trace does not match the immutable row trace")
        used.add(source_key); output.append(event)
    if terminal_count != 1:
        raise V4LivePacketViolation("scenario trace lacks one completed terminal")
    return tuple(output), sha256_value(trace)
def _host(value: Any, turn_count: int, provider_calls: int) -> tuple[dict[str, Any], dict[str, str]]:
    host = _copy(value, "host_observation")
    if set(host) != _HOST or host["schema_version"] != 1 or host["status"] != "PASS" or host["runtime"] != V4_SDK_DISTRIBUTION or host["invariant_violations"] != [] or host["expected_turn_count"] != turn_count:
        raise V4LivePacketViolation("host observation is not a closed PASS")
    transcript, usage = _copy(host["transcript"], "transcript"), _copy(host["runtime_usage"], "runtime_usage")
    terminal = _copy(transcript.get("terminal"), "transcript.terminal")
    rows = _copy(transcript.get("canonical_rows"), "transcript.canonical_rows")
    if type(terminal.get("count")) is not int or terminal["count"] != turn_count or terminal.get("persisted") is not True or not _digest(terminal.get("sha256"), "transcript terminal") or type(rows.get("user", {}).get("count")) is not int or rows["user"]["count"] != turn_count or type(rows.get("assistant", {}).get("count")) is not int or rows["assistant"]["count"] < turn_count:
        raise V4LivePacketViolation("persisted transcript/terminal evidence is incomplete")
    ordered = usage.get("ordered")
    if usage.get("receipt_count") != turn_count or not isinstance(ordered, list) or len(ordered) != turn_count or usage.get("latest") != ordered[-1]:
        raise V4LivePacketViolation("runtime usage receipt count is not exact")
    for item in ordered:
        if not isinstance(item, Mapping) or item.get("provider") != "anthropic" or item.get("model") != V4_MODEL or item.get("selected_model") != V4_MODEL or item.get("effective_model") != V4_MODEL or item.get("canonical_model") != V4_MODEL or item.get("model_resolution") != "exact" or item.get("billing_mode") != "subscription_included" or item.get("cost_status") != "included" or item.get("fallback_used") is not False or item.get("api_call_count") != provider_calls:
            raise V4LivePacketViolation("runtime usage billing/model evidence is unsafe")
        _digest(item.get("sha256"), "runtime usage receipt")
    state = _copy(host["runtime_state"], "runtime_state")
    if state.get("present") is True:
        _digest(state.get("sha256"), "runtime state")
    elif state != {"present": False, "schema_version": None, "sha256": None}:
        raise V4LivePacketViolation("runtime state evidence is malformed")
    return host, {"primary": terminal["sha256"], "secondary": ordered[-1]["sha256"]}
def _local(value: Any, path: str, expected_trace: Sequence[str], *, expected_row_key: str, expected_trial_index: int) -> tuple[tuple[dict[str, Any], ...], dict[str, str]]:
    item = _copy(value, f"{path} observation")
    if set(item) != {"schema_version", "status", "path", "host_local", "provider_calls", "terminal_status", "events", "observation", "proof_hashes"} or item["schema_version"] != 1 or item["status"] != "PASS" or item["path"] != path or item["host_local"] is not True or item["provider_calls"] != 0 or item["terminal_status"] not in _TERMINALS or not _copy(item["observation"], f"{path}.observation"):
        raise V4LivePacketViolation(f"{path} host-local observation is incomplete")
    observation = _copy(item["observation"], f"{path}.observation")
    identity = _copy(observation.get("identity"), f"{path}.observation.identity")
    if set(identity) != {"row_key", "path", "trial_index"} or not _id(identity["row_key"], f"{path}.observation.identity.row_key") or identity["path"] != path or type(identity["trial_index"]) is not int or identity["trial_index"] < 1:
        raise V4LivePacketViolation(f"{path} local observation identity is incomplete")
    if identity["row_key"] != expected_row_key:
        raise V4LivePacketViolation(f"{path} local observation identity is not row-bound")
    if identity["trial_index"] != expected_trial_index:
        raise V4LivePacketViolation(f"{path} local observation identity is not trial-bound")
    classification = "EXPECTED_NEGATIVE" if path == "denial" else "COMPLETE"
    events, _, _ = _events(item["events"], classification)
    if tuple(event["kind"] for event in events) != tuple(expected_trace):
        raise V4LivePacketViolation(f"{path} event projection does not match the frozen trace")
    if item["terminal_status"] != ("denied" if path == "denial" else "completed"):
        raise V4LivePacketViolation(f"{path} terminal outcome is wrong")
    proofs = _copy(item["proof_hashes"], f"{path}.proof_hashes")
    if set(proofs) != {"primary", "secondary"}:
        raise V4LivePacketViolation(f"{path} proof hashes are incomplete")
    _digest(proofs["primary"], f"{path} primary proof"); _digest(proofs["secondary"], f"{path} secondary proof")
    expected_primary = sha256_value(observation)
    expected_secondary = sha256_value({"identity": identity, "events": item["events"]})
    if proofs != {"primary": expected_primary, "secondary": expected_secondary}:
        raise V4LivePacketViolation(f"{path} local proof hashes do not match identity-bound evidence")
    return events, {"primary": proofs["primary"], "secondary": proofs["secondary"]}
def _map(value: Mapping[str, Any] | str | Path | None, map_path: str | Path | None) -> tuple[dict[str, Any], Path]:
    source = Path(map_path).expanduser().resolve() if map_path is not None else Path(value).expanduser().resolve() if isinstance(value, (str, Path)) else Path(__file__).resolve().parents[3] / "qa" / "parity-v4-live-execution-map.yaml"
    document = load_v4_live_execution_map(value) if isinstance(value, (str, Path)) else load_v4_live_execution_map(source) if value is None else _copy(value, "live_map")
    accounting = validate_v4_live_execution_map(document, map_path=source)
    if accounting.get("map_sha256") != LIVE_MAP_SHA256:
        raise V4LivePacketViolation("live map is not the corrected frozen map")
    return document, source
def _scenario(value: Any, catalog: V4LiveScenarioCatalog) -> V4LiveScenario:
    expected = {item.row_key: item for item in catalog.scenarios}
    if isinstance(value, V4LiveScenario):
        chosen = expected.get(value.row_key)
        if chosen is None or value != chosen:
            raise V4LivePacketViolation("scenario is not the frozen catalog row")
        return chosen
    key = value if isinstance(value, str) else value.get("row_key") if isinstance(value, Mapping) else None
    chosen = expected.get(key)
    if chosen is None or isinstance(value, Mapping) and dict(value) != chosen.to_dict():
        raise V4LivePacketViolation("scenario identity or semantics drifted")
    return chosen
def _stream(base: Any, candidate_hash: str, trial: ResultPacket, scenario_hash: str, content_hash: str) -> dict[str, Any]:
    stream = _copy(base, "stream_projection")
    fields = {"schema_version", "name", "candidate_hash", "trial_candidate_hash", "trial_index", "status", "source", "observation"}
    if set(stream) != fields or stream["schema_version"] != 1 or stream["name"] != "stream" or stream["status"] != "PASS":
        raise V4LivePacketViolation("stream projection is not closed PASS evidence")
    if (stream["candidate_hash"], stream["trial_candidate_hash"], stream["trial_index"]) != (candidate_hash, trial.candidate_hash, trial.trial_index):
        raise V4LivePacketViolation("stream projection identity is mismatched")
    observation = _copy(stream["observation"], "stream.observation")
    observation.update({"scenario_receipt_hash": scenario_hash, "content_projection_hash": content_hash})
    stream["observation"] = observation
    return stream
def build_v4_live_packets(contract: Mapping[str, Any], scenario: V4LiveScenario | Mapping[str, Any] | str, positive_receipt: Mapping[str, Any], path_observations: Mapping[str, Mapping[str, Any]] | None = None, *, live_map: Mapping[str, Any] | str | Path | None = None, map_path: str | Path | None = None, scenario_catalog: V4LiveScenarioCatalog | Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build p/d/r packets from one positive session receipt and local observations."""
    try:
        validate_v4_contract(contract)
        document, source = _map(live_map, map_path)
        catalog = build_v4_live_scenario_catalog(document, map_path=source) if scenario_catalog is None else scenario_catalog
        validate_v4_live_scenario_catalog(catalog, live_map=document, map_path=source)
        chosen_catalog = catalog if isinstance(catalog, V4LiveScenarioCatalog) else build_v4_live_scenario_catalog(document, map_path=source)
        selected = _scenario(scenario, chosen_catalog)
        rows = [row for row in contract["source_rows"] if f"{row['source_pack']}/{row['source_item_id']}" == selected.row_key]
        if len(rows) != 1:
            raise V4LivePacketViolation("scenario has no unique contract row")
        row = rows[0]
        if row["predecessor_execution_id"] != selected.predecessor_execution_id or not row["provider_live_required"] or tuple(required_trial_indexes(row)) != tuple(selected.trial_indexes):
            raise V4LivePacketViolation("scenario row is not bound to the immutable contract")
        receipt = _copy(positive_receipt, "positive_receipt")
        fields = {"schema_version", "candidate", "preflight_projections", "attempts", "host_observation", "profile_id", "inventory_hash", "stream_projection", "scenario_trace", "delegation"}
        if set(receipt) != fields or receipt["schema_version"] != 1:
            raise V4LivePacketViolation("positive receipt fields are not closed")
        candidate, candidate_hash = _candidate(receipt["candidate"])
        if not isinstance(receipt["attempts"], list) or len(receipt["attempts"]) != selected.turn_count:
            raise V4LivePacketViolation("positive receipt does not contain the row turn bundle")
        attempts, content_hashes, indexes, seen_events = [], [], [], set()
        for raw in receipt["attempts"]:
            attempt, observed_candidate, events, content = _attempt(raw, selected, candidate_hash)
            if observed_candidate != candidate:
                raise V4LivePacketViolation("positive attempts use different candidates")
            hashes = {item["sha256"] for item in attempt["events"]}
            if seen_events & hashes:
                raise V4LivePacketViolation("positive event projections are reused across turns")
            seen_events.update(hashes)
            attempts.append(attempt); content_hashes.extend(content); indexes.append(attempt["turn_index"])
        if indexes != list(range(1, selected.turn_count + 1)):
            raise V4LivePacketViolation("positive turn indexes are not contiguous")
        first_identity = _identity(attempts[0]["identity"], "positive identity")
        if any(_identity(item["identity"], "positive identity") != first_identity for item in attempts) or first_identity["live_map_sha256"] != LIVE_MAP_SHA256 or first_identity["row_key"] != selected.row_key or first_identity["predecessor_execution_id"] != selected.predecessor_execution_id:
            raise V4LivePacketViolation("positive scenario identities are not reused exactly")
        if _preflight_hash(receipt["preflight_projections"], candidate_hash) != first_identity["preflight_hash"]:
            raise V4LivePacketViolation("positive preflight identity is not exact")
        _id(receipt["profile_id"], "profile_id"); _digest(receipt["inventory_hash"], "inventory_hash")
        host, host_proofs = _host(receipt["host_observation"], selected.turn_count, selected.turn_count)
        scenario_events, scenario_trace_hash = _scenario_trace(receipt["scenario_trace"], selected, row["expected_trace"], attempts, host)
        delegation = _delegation(receipt["delegation"], selected, first_identity["trial_index"])
        content_hash = sha256_value(tuple(content_hashes))
        catalog_hash = chosen_catalog.catalog_sha256
        core = {"schema_version": 1, "row_key": selected.row_key, "predecessor_execution_id": selected.predecessor_execution_id, "trial_index": first_identity["trial_index"], "scenario_catalog_hash": catalog_hash, "live_map_sha256": LIVE_MAP_SHA256, "candidate_hash": candidate_hash, "preflight_hash": first_identity["preflight_hash"], "attempt_hashes": [sha256_value(item) for item in attempts], "approval_projection_hashes": [sha256_value(item["approval"]) for item in attempts], "host_observation_hash": sha256_value(host), "scenario_trace_hash": scenario_trace_hash, "content_projection_hash": content_hash, "delegation_summary": delegation, "delegation_summary_hash": sha256_value(delegation), "turn_count": selected.turn_count, "provider_calls": selected.turn_count}
        scenario_hash = sha256_value(core)
        observations = {} if path_observations is None else dict(path_observations)
        if "positive" in observations or set(observations) - set(selected.mandatory_paths):
            raise V4LivePacketViolation("path observations contain a non-mandatory path")
        paths, packets, trials = {}, {}, {}
        for path in selected.mandatory_paths:
            if path == "positive":
                events, proofs, classification, turns = scenario_events, host_proofs, "COMPLETE", selected.turn_count
            elif path in observations:
                events, proofs = _local(observations[path], path, row["expected_trace"], expected_row_key=selected.row_key, expected_trial_index=first_identity["trial_index"])
                classification, turns = ("EXPECTED_NEGATIVE", 0) if path == "denial" else ("COMPLETE", 0)
            else:
                events, proofs, classification, turns = (), host_proofs, "PENDING", 0
            trial = ResultPacket.build(capability_id=row["predecessor_capability_id"], source_pack=row["source_pack"], lane="rc", path=path, execution_id=row["predecessor_execution_id"], classification=classification, contract_hash=V3_RESULT_CONTRACT_HASH, catalog_hash=V3_RESULT_CATALOG_HASH, plugin_sha=candidate["plugin_sha"], host_sha=candidate["host_sha"], sdk_version=candidate["sdk_version"], profile_id=receipt["profile_id"], profile_hash=candidate["profile_sha256"], runner_version=candidate["runner_version"], inventory_hash=receipt["inventory_hash"], billing_classification="subscription_included", turn_count=turns, trial_index=first_identity["trial_index"], normalized_events=events, primary_proof_hash=proofs["primary"], secondary_proof_hash=proofs["secondary"], reason_code=None if path == "positive" or path in observations else f"missing_{path}_host_observation")
            if classification == "PENDING":
                ownership, packet = None, None
            else:
                stream = _stream(receipt["stream_projection"], candidate_hash, trial, scenario_hash, content_hash)
                ownership = build_ownership_receipt(trial, candidate, receipt["preflight_projections"], stream)
                packet = bind_v4_evidence(contract, trial, ownership)
            if packet is not None and paths and next(iter(paths.values()))["packet"] is not None and packet["proof_hashes"]["stream"] != next(iter(paths.values()))["packet"]["proof_hashes"]["stream"]:
                raise V4LivePacketViolation("path packets do not share the scenario stream receipt")
            paths[path] = {"classification": classification, "turn_count": turns, "trial": trial, "ownership_receipt": ownership, "packet": packet}
            trials[path], packets[path] = trial, packet
        scenario_receipt = dict(core, receipt_hash=scenario_hash, provider_accounting={"positive_calls": selected.turn_count, "denial_calls": 0, "recovery_calls": 0, "total_calls": selected.turn_count})
        return {"scenario": selected.to_dict(), "scenario_receipt": scenario_receipt, "scenario_receipt_hash": scenario_hash, "paths": paths, "trials": trials, "packets": packets}
    except V4LivePacketViolation:
        raise
    except Exception as exc:
        raise V4LivePacketViolation("scenario packet binding failed closed") from exc
build_v4_live_packet_bundle = build_v4_live_packets
build_v4_scenario_packets = build_v4_live_packets
build_live_packets = build_v4_live_packets
LivePacketViolation = V4LivePacketViolation
__all__ = ["LIVE_MAP_SHA256", "LivePacketViolation", "V4LivePacketViolation", "build_live_packets", "build_v4_live_packet_bundle", "build_v4_scenario_packets", "build_v4_live_packets"]
