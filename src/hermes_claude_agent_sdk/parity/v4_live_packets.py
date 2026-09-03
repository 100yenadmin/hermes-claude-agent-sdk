"""Provider-free binding of one v4 live attempt into v3 and v4 packets."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .hashing import json_compatible, sha256_file, sha256_value
from .results import ExecutionClassification, ResultPacket
from .v4_contract import (
    OWNERSHIP_PREFLIGHTS, V3_RESULT_CATALOG_HASH, V3_RESULT_CONTRACT_HASH,
    V4_MODEL, V4_SDK_VERSION, V4_RUNNER_VERSION, required_trial_indexes,
    validate_v4_contract,
)
from .v4_evidence import bind_v4_evidence
from .v4_live_map import load_v4_live_execution_map, validate_v4_live_execution_map
from .v4_receipts import build_ownership_receipt

LIVE_MAP_SHA256 = "16a9e8e3bb2a540b74c2b070b2b84f8d0d588778b615c4b5f91d3597a407140b"
_HEX40, _HEX64 = re.compile(r"^[0-9a-f]{40}$"), re.compile(r"^[0-9a-f]{64}$")
_SAFE = re.compile(r"^[A-Za-z0-9_.:@/+\-]{1,256}$")
_RAW = frozenset("raw raw_prompt raw_content raw_transcript messages prompt content transcript session session_id credential credentials password secret secrets token access_token refresh_token api_key cookie cookies".split())
_ID = frozenset("candidate_hash preflight_hash live_map_sha256 row_key predecessor_execution_id path trial_index".split())
_CANDIDATE = frozenset("plugin_sha host_sha wheel_sha256 profile_sha256 sdk_distribution sdk_version cli_version model runner_id runner_version".split())
_ATTEMPT = frozenset("identity candidate classification terminal_status event_count event_kinds events control_calls_used provider_calls turns_used approval".split())
_EVENT = frozenset({"kind", "byte_length", "sha256", "terminal_status", "projection"})
_TERMINALS = frozenset({"completed", "denied", "failed", "cancelled"})
_HOST = frozenset("identity runtime provider effective_model canonical_model billing_mode cost_status fallback_used api_calls api_call_budget tool_request_count tool_result_count invariant_violations state_hash profile_id inventory_hash proof_hashes preflight_projections stream_projection".split())
_COUNTS = (frozenset({"transcript_count", "terminal_count"}), frozenset({"persisted_transcript_count", "persisted_terminal_count"}))


class V4LivePacketViolation(ValueError):
    """A sanitized live attempt cannot form a safe packet."""


def _m(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise V4LivePacketViolation(f"{field} must be a mapping")
    try: result = json_compatible(value)
    except TypeError as exc: raise V4LivePacketViolation(f"{field} is not JSON-compatible") from exc
    if not isinstance(result, dict): raise V4LivePacketViolation(f"{field} must be a mapping")
    return result


def _raw(value: Any, location: str = "value") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str): raise V4LivePacketViolation(f"{location} has an invalid key")
            lowered = key.casefold().replace("-", "_")
            if lowered in _RAW or lowered.startswith("raw_") or lowered.endswith("_raw"): raise V4LivePacketViolation(f"{location} contains forbidden raw data")
            _raw(child, f"{location}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value): _raw(child, f"{location}[{index}]")


def _safe(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SAFE.fullmatch(value) is None: raise V4LivePacketViolation(f"{field} is not a safe identifier")
    return value


def _digest(value: Any, field: str, size: int = 64) -> str:
    pattern = _HEX40 if size == 40 else _HEX64
    if not isinstance(value, str) or pattern.fullmatch(value) is None or value == "0" * size: raise V4LivePacketViolation(f"{field} is not a nonzero lowercase digest")
    return value


def _identity(value: Any, field: str = "identity") -> dict[str, Any]:
    result = _m(value, field); _raw(result, field)
    if set(result) != _ID: raise V4LivePacketViolation(f"{field} fields are not closed")
    for key in ("candidate_hash", "preflight_hash", "live_map_sha256"): _digest(result[key], f"{field}.{key}")
    for key in ("row_key", "predecessor_execution_id"): _safe(result[key], f"{field}.{key}")
    if result["path"] not in {"positive", "denial", "recovery"} or type(result["trial_index"]) is not int or result["trial_index"] < 1: raise V4LivePacketViolation(f"{field} path or trial is invalid")
    return result


def _candidate(value: Any) -> tuple[dict[str, Any], str]:
    result = _m(value, "candidate"); _raw(result, "candidate")
    if set(result) != _CANDIDATE: raise V4LivePacketViolation("candidate fields are not closed")
    for key, size in (("plugin_sha", 40), ("host_sha", 40), ("wheel_sha256", 64), ("profile_sha256", 64)): _digest(result[key], f"candidate.{key}", size)
    expected = {"sdk_distribution": "claude-agent-sdk", "sdk_version": V4_SDK_VERSION, "cli_version": "2.1.258", "model": V4_MODEL, "runner_id": "hermes-parity-v4", "runner_version": V4_RUNNER_VERSION}
    if any(result[key] != value for key, value in expected.items()): raise V4LivePacketViolation("candidate target is not frozen v4")
    return result, sha256_value(result)


def _kind(value: Any) -> str:
    name = _safe(value, "event.kind").casefold()
    exact = {"start":"start", "message.start":"start", "session.start":"start", "run.start":"start", "task.start":"start", "approval.request":"approval_requested", "approval.requested":"approval_requested", "approval.decision":"approval_decision", "approval.decided":"approval_decision", "approval.responded":"approval_decision", "tool.request":"tool_requested", "tool.requested":"tool_requested", "tool.start":"tool_requested", "tool.result":"tool_result", "tool.complete":"tool_result", "tool.completed":"tool_result", "state":"state", "session.state":"state", "message.state":"state", "usage":"usage", "message.usage":"usage", "compaction":"compaction", "background":"background", "restart":"restart", "terminal":"terminal", "message.complete":"terminal", "session.complete":"terminal", "run.complete":"terminal", "task.complete":"terminal"}
    if name in exact: return exact[name]
    if name.endswith((".terminal", ".finished", ".done")): return "terminal"
    raise V4LivePacketViolation("event projection kind is unsupported")


def _events(value: Any, classification: str) -> tuple[tuple[dict[str, Any], ...], Counter[str], dict[str, int]]:
    if not isinstance(value, list) or not value: raise V4LivePacketViolation("attempt events must be a non-empty list")
    normalized, mapped, raw_counts, seen, terminals = [], Counter(), Counter(), set(), []
    for index, raw in enumerate(value, 1):
        item = _m(raw, f"attempt.events[{index - 1}]"); _raw(item, f"attempt.events[{index - 1}]")
        if set(item) - _EVENT or not {"kind", "byte_length", "sha256", "terminal_status"} <= set(item): raise V4LivePacketViolation("event projection fields are not closed")
        kind = _kind(item["kind"]); raw_counts[item["kind"]] += 1
        if type(item["byte_length"]) is not int or not 1 <= item["byte_length"] <= 1_048_576: raise V4LivePacketViolation("event projection byte length is invalid")
        digest = _digest(item["sha256"], f"attempt.events[{index - 1}].sha256")
        if digest in seen: raise V4LivePacketViolation("event projections are reused")
        seen.add(digest)
        if "projection" in item:
            projection = _m(item["projection"], "opaque event projection")
            if set(projection) != {"kind", "byte_length", "sha256"} or type(projection["byte_length"]) is not int or not 0 <= projection["byte_length"] <= 1_048_576: raise V4LivePacketViolation("opaque event projection is malformed")
            _safe(projection["kind"], "opaque projection.kind"); _digest(projection["sha256"], "opaque projection.sha256")
        status = item["terminal_status"]
        if status is not None and status not in _TERMINALS: raise V4LivePacketViolation("event terminal status is unsupported")
        if kind == "terminal":
            if status is None: raise V4LivePacketViolation("terminal event lacks outcome")
            terminals.append(status)
        elif status is not None: raise V4LivePacketViolation("non-terminal event carries outcome")
        out = {"sequence": index, "kind": kind, "metadata_hash": digest}
        if kind == "terminal": out.update(status=status, terminal_outcome=status)
        else: out["status"] = "expected_negative" if classification == "EXPECTED_NEGATIVE" else "observed"
        normalized.append(out); mapped[kind] += 1
    expected = {"COMPLETE": "completed", "EXPECTED_NEGATIVE": "denied"}.get(classification)
    if len(terminals) != 1 or expected is not None and terminals[0] != expected or classification == "VERIFIED_FAILURE" and terminals[0] not in {"failed", "cancelled"}: raise V4LivePacketViolation("terminal outcome does not match classification")
    if mapped["approval_requested"] != mapped["approval_decision"] or mapped["tool_requested"] != mapped["tool_result"]: raise V4LivePacketViolation("event request/result pairing is incomplete")
    return tuple(normalized), mapped, dict(sorted(raw_counts.items()))


def _attempt(value: Any) -> tuple[dict[str, Any], dict[str, Any], tuple[dict[str, Any], ...], Counter[str], dict[str, Any]]:
    result = _m(value, "attempt"); _raw(result, "attempt")
    if set(result) != _ATTEMPT: raise V4LivePacketViolation("attempt fields are not closed")
    identity = _identity(result["identity"], "attempt.identity"); candidate, candidate_digest = _candidate(result["candidate"])
    if identity["candidate_hash"] != candidate_digest: raise V4LivePacketViolation("attempt candidate hash is not exact")
    if result["classification"] not in ({item.value for item in ExecutionClassification} - {"PENDING", "ENVIRONMENT_BLOCKED"}): raise V4LivePacketViolation("attempt classification is not terminal")
    for key, low, high in (("event_count", 1, 10_000), ("control_calls_used", 0, 16), ("provider_calls", 1, 136), ("turns_used", 1, 1)):
        if type(result[key]) is not int or not low <= result[key] <= high: raise V4LivePacketViolation("attempt accounting is invalid")
    approval = _m(result["approval"], "attempt.approval")
    if set(approval) != {"decision_class", "decision_count"} or approval["decision_class"] not in {"allow", "deny", "other"} or type(approval["decision_count"]) is not int or approval["decision_count"] < 0: raise V4LivePacketViolation("attempt approval accounting is invalid")
    events, kinds, raw_kinds = _events(result["events"], result["classification"])
    if not isinstance(result["event_kinds"], Mapping) or any(type(value) is not int or value < 0 for value in result["event_kinds"].values()) or result["event_count"] != len(events) or result["event_kinds"] != raw_kinds: raise V4LivePacketViolation("attempt event accounting is not exact")
    if approval["decision_count"] != kinds["approval_requested"]: raise V4LivePacketViolation("attempt approval accounting is not exact")
    if result["terminal_status"] != next(item["terminal_outcome"] for item in events if item["kind"] == "terminal"): raise V4LivePacketViolation("attempt terminal receipt is not exact")
    return identity, candidate, events, kinds, result


def _host(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    result = _m(value, "host_observation"); _raw(result, "host_observation")
    counts = next((item for item in _COUNTS if item <= set(result)), None)
    allowed = _HOST | (counts or frozenset()) | (frozenset({"reason_code"}) if "reason_code" in result else frozenset())
    if counts is None or set(result) != allowed: raise V4LivePacketViolation("host observation fields are not closed")
    identity = _identity(result["identity"], "host_observation.identity")
    expected = {"runtime": "claude-agent-sdk", "provider": "anthropic", "effective_model": V4_MODEL, "canonical_model": V4_MODEL, "billing_mode": "subscription_included", "cost_status": "included"}
    if any(result[key] != value for key, value in expected.items()) or result["fallback_used"] is not False or type(result["fallback_used"]) is not bool: raise V4LivePacketViolation("host observation does not prove the frozen subscription route")
    for key in ("api_calls", "api_call_budget", "tool_request_count", "tool_result_count"):
        if type(result[key]) is not int or result[key] < 0: raise V4LivePacketViolation("host accounting is invalid")
    if not 1 <= result["api_call_budget"] <= 136 or result["api_calls"] < 1: raise V4LivePacketViolation("host API budget is invalid")
    transcript = result.get("transcript_count", result.get("persisted_transcript_count")); terminal = result.get("terminal_count", result.get("persisted_terminal_count"))
    if type(transcript) is not int or type(terminal) is not int or transcript != 1 or terminal != 1: raise V4LivePacketViolation("persisted transcript or terminal count is incomplete")
    _digest(result["state_hash"], "host state_hash"); _safe(result["profile_id"], "host profile_id"); _digest(result["inventory_hash"], "host inventory_hash")
    proofs = _m(result["proof_hashes"], "host proof_hashes")
    if set(proofs) != {"primary", "secondary"}: raise V4LivePacketViolation("host proof hashes are incomplete")
    _digest(proofs["primary"], "host primary proof"); _digest(proofs["secondary"], "host secondary proof")
    if result["invariant_violations"] != []: raise V4LivePacketViolation("host invariant violations are present")
    if "reason_code" in result and result["reason_code"] is not None: _safe(result["reason_code"], "host reason_code")
    return identity, result


def _row(contract: Mapping[str, Any], live_map: Mapping[str, Any], identity: Mapping[str, Any]) -> Mapping[str, Any]:
    key = identity["row_key"]; rows = [row for row in contract["source_rows"] if f"{row.get('source_pack')}/{row.get('source_item_id')}" == key]; mapped = [row for row in live_map["rows"] if f"{row.get('source_pack')}/{row.get('source_item_id')}" == key]
    if len(rows) != 1 or len(mapped) != 1: raise V4LivePacketViolation("row identity is not unique")
    row, live = rows[0], mapped[0]
    if not row["provider_live_required"] or live["predecessor_execution_id"] != row["predecessor_execution_id"] or live["mandatory_paths"] != row["mandatory_paths"] or live["required_trial_indexes"] != list(required_trial_indexes(row)): raise V4LivePacketViolation("live map row is not bound to the v4 contract")
    if identity["predecessor_execution_id"] != row["predecessor_execution_id"] or identity["path"] not in row["mandatory_paths"] or identity["trial_index"] not in required_trial_indexes(row): raise V4LivePacketViolation("attempt path or trial is not mandatory")
    return row


def _preflight_hash(projections: Mapping[str, Any], candidate_digest: str) -> str:
    if set(projections) != set(OWNERSHIP_PREFLIGHTS): raise V4LivePacketViolation("host preflight projections are incomplete")
    identities = {}
    for name in OWNERSHIP_PREFLIGHTS:
        item = _m(projections[name], f"preflight.{name}")
        if set(item) != {"schema_version", "name", "candidate_hash", "status", "source", "observation"} or item["schema_version"] != 1 or item["name"] != name or item["candidate_hash"] != candidate_digest or item["status"] != "PASS": raise V4LivePacketViolation("preflight projection is not an exact PASS")
        source, observation = _m(item["source"], "preflight source"), _m(item["observation"], "preflight observation")
        if set(source) != {"executable", "source_ref", "test_id"} or not observation: raise V4LivePacketViolation("preflight projection metadata is incomplete")
        identities[name] = {"candidate_hash": candidate_digest, "status": "PASS", "source_hash": sha256_value(source), "observation_hash": sha256_value(observation)}
    return sha256_value(identities)


def build_v4_live_packets(contract: Mapping[str, Any], live_map: Mapping[str, Any] | str | Path, attempt: Mapping[str, Any], host_observation: Mapping[str, Any], *, map_path: str | Path | None = None) -> dict[str, Any]:
    """Return ``trial``, ``ownership_receipt``, and bound ``packet`` keys."""
    try:
        validate_v4_contract(contract)
        if isinstance(live_map, (str, Path)):
            source = Path(live_map).expanduser().resolve(); document = load_v4_live_execution_map(source); map_hash = sha256_file(source)
        else:
            document = _m(live_map, "live_map"); source = Path(map_path).expanduser().resolve() if map_path is not None else Path(__file__).resolve().parents[3] / "qa" / "parity-v4-live-execution-map.yaml"; map_hash = validate_v4_live_execution_map(document, map_path=source).get("map_sha256")
        if map_hash != LIVE_MAP_SHA256: raise V4LivePacketViolation("live map is not frozen")
        identity, candidate, events, kinds, raw_attempt = _attempt(attempt); host_identity, observation = _host(host_observation)
        if identity != host_identity or identity["live_map_sha256"] != map_hash: raise V4LivePacketViolation("attempt and host identities differ")
        row = _row(contract, document, identity)
        if tuple(item["kind"] for item in events) != tuple(row["expected_trace"]): raise V4LivePacketViolation("event projection does not match the frozen trace")
        persisted_terminal = observation.get("terminal_count", observation.get("persisted_terminal_count"))
        if observation["api_calls"] != raw_attempt["provider_calls"] or observation["api_calls"] > observation["api_call_budget"] or observation["tool_request_count"] != kinds["tool_requested"] or observation["tool_result_count"] != kinds["tool_result"] or persisted_terminal != kinds["terminal"]: raise V4LivePacketViolation("host accounting does not match attempt")
        if _preflight_hash(observation["preflight_projections"], identity["candidate_hash"]) != identity["preflight_hash"]: raise V4LivePacketViolation("host preflight hash does not match attempt")
        proofs = observation["proof_hashes"]
        trial = ResultPacket.build(capability_id=row["predecessor_capability_id"], source_pack=row["source_pack"], lane="rc", path=identity["path"], execution_id=row["predecessor_execution_id"], classification=raw_attempt["classification"], contract_hash=V3_RESULT_CONTRACT_HASH, catalog_hash=V3_RESULT_CATALOG_HASH, plugin_sha=candidate["plugin_sha"], host_sha=candidate["host_sha"], sdk_version=candidate["sdk_version"], profile_id=observation["profile_id"], profile_hash=candidate["profile_sha256"], runner_version=candidate["runner_version"], inventory_hash=observation["inventory_hash"], billing_classification="subscription_included", turn_count=1, trial_index=identity["trial_index"], normalized_events=events, primary_proof_hash=proofs["primary"], secondary_proof_hash=proofs["secondary"], reason_code=observation.get("reason_code"))
        receipt = build_ownership_receipt(trial, candidate, observation["preflight_projections"], observation["stream_projection"]); packet = bind_v4_evidence(contract, trial, receipt)
        if (packet["source_pack"], packet["source_item_id"], packet["path"], packet["trial_index"]) != (row["source_pack"], row["source_item_id"], identity["path"], identity["trial_index"]): raise V4LivePacketViolation("bound packet identity changed")
        return {"trial": trial, "ownership_receipt": receipt, "packet": packet}
    except V4LivePacketViolation: raise
    except Exception as exc: raise V4LivePacketViolation("live packet binding failed closed") from exc


build_v4_live_packet_bundle = build_v4_live_packets
build_live_packets = build_v4_live_packets
LivePacketViolation = V4LivePacketViolation
__all__ = ["LIVE_MAP_SHA256", "LivePacketViolation", "V4LivePacketViolation", "build_live_packets", "build_v4_live_packet_bundle", "build_v4_live_packets"]
