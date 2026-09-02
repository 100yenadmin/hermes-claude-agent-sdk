"""Deterministic v4 packet validation and grading.

This runner is deliberately an evidence grader.  It never imports the Claude
SDK, starts a host process, calls a provider, or invents an executor.  A
packet is accepted only when its exact Hermes-owned candidate, predecessor
link, ownership preflights, billing, and sanitized streaming proof all bind.
"""

from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .hashing import sha256_value
from .v4_contract import (
    OWNERSHIP_PREFLIGHTS,
    V3_HASHES,
    V4_CLI_VERSION,
    V4_MODEL,
    V4_RUNNER_ID,
    V4_RUNNER_VERSION,
    V4_SDK_DISTRIBUTION,
    V4_SDK_VERSION,
    V4_VERSION,
    V4ContractViolation,
    validate_v4_contract,
)

CLASSIFICATIONS = frozenset({"PENDING", "EXPECTED_NEGATIVE", "ENVIRONMENT_BLOCKED", "VERIFIED_FAILURE", "COMPLETE"})
BILLING = frozenset({"subscription_included", "explicitly_free", "none"})
PATHS = frozenset({"positive", "denial", "recovery"})
EVENT_KINDS = frozenset({"preflight", "start", "state", "delegate", "background", "stream", "approval", "tool", "restart", "terminal", "approval_requested", "approval_decision", "tool_requested", "tool_result", "usage", "compaction"})
EVENT_FIELDS = frozenset({"sequence", "kind", "status", "terminal_outcome", "event_sha256", "name_hash", "request_hash", "state_hash", "usage_hash", "schema_hash", "tool_hash", "parent_hash", "metadata_hash"})
TERMINAL_OUTCOMES = frozenset({"completed", "denied", "failed", "cancelled"})
HEX40 = set("0123456789abcdef")
HEX64 = set("0123456789abcdef")
PACKET_FIELDS = frozenset({
    "schema_version", "contract_version", "contract_sha256", "predecessor",
    "source_pack", "source_item_id", "execution_id", "successor_id", "path",
    "classification", "candidate", "candidate_hash", "billing_classification",
    "silent_fallback", "preflight_results", "proof_hashes", "events", "trial_index", "turn_count",
    "packet_sha256",
})


class V4ResultViolation(ValueError):
    """A result packet cannot be admitted to deterministic v4 grading."""


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise V4ResultViolation(f"{field} must be a mapping")
    return dict(value)


def _digest(value: Any, field: str, length: int = 64) -> str:
    if not isinstance(value, str) or len(value) != length or set(value) - (HEX64 if length == 64 else HEX40):
        raise V4ResultViolation(f"{field} is not a lowercase digest")
    return value


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise V4ResultViolation(f"{field} is not a bounded identifier")
    if any(marker in value.casefold() for marker in ("raw_prompt", "raw_content", "transcript", "session_id", "credential", "token")):
        raise V4ResultViolation(f"{field} contains a forbidden raw-data marker")
    return value


def _reject_raw(value: Any, location: str = "packet") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in {"raw_prompt", "raw_content", "messages", "raw_transcript", "session_id", "credential", "credentials", "access_token", "refresh_token", "api_key"}:
                raise V4ResultViolation(f"{location} contains forbidden raw data")
            _reject_raw(child, f"{location}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_raw(child, f"{location}[{index}]")


def _candidate(value: Any) -> dict[str, Any]:
    raw = _mapping(value, "candidate")
    allowed = {"plugin_sha", "host_sha", "wheel_sha256", "profile_sha256", "sdk_distribution", "sdk_version", "cli_version", "model", "runner_id", "runner_version"}
    if set(raw) - allowed or not {"plugin_sha", "host_sha", "wheel_sha256", "profile_sha256"} <= set(raw):
        raise V4ResultViolation("candidate fields are incomplete or unknown")
    normalized = {
        "plugin_sha": _digest(raw["plugin_sha"], "candidate.plugin_sha", 40),
        "host_sha": _digest(raw["host_sha"], "candidate.host_sha", 40),
        "wheel_sha256": _digest(raw["wheel_sha256"], "candidate.wheel_sha256"),
        "profile_sha256": _digest(raw["profile_sha256"], "candidate.profile_sha256"),
        "sdk_distribution": raw.get("sdk_distribution", V4_SDK_DISTRIBUTION),
        "sdk_version": raw.get("sdk_version", V4_SDK_VERSION),
        "cli_version": raw.get("cli_version", V4_CLI_VERSION),
        "model": raw.get("model", V4_MODEL),
        "runner_id": raw.get("runner_id", V4_RUNNER_ID),
        "runner_version": raw.get("runner_version", V4_RUNNER_VERSION),
    }
    if normalized["sdk_distribution"] != V4_SDK_DISTRIBUTION or normalized["sdk_version"] != V4_SDK_VERSION or normalized["cli_version"] != V4_CLI_VERSION or normalized["model"] != V4_MODEL or normalized["runner_id"] != V4_RUNNER_ID or normalized["runner_version"] != V4_RUNNER_VERSION:
        raise V4ResultViolation("candidate target or runner identity does not match v4")
    return normalized


def _events(value: Any, classification: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise V4ResultViolation("events must be a non-empty sanitized list")
    result = []
    terminal = []
    for ordinal, raw in enumerate(value, 1):
        item = _mapping(raw, f"events[{ordinal - 1}]")
        if set(item) - EVENT_FIELDS or item.get("sequence") != ordinal or item.get("kind") not in EVENT_KINDS:
            raise V4ResultViolation("events are not contiguous or sanitized")
        if "status" in item:
            _id(item["status"], f"events[{ordinal - 1}].status")
        if "event_sha256" in item:
            _digest(item["event_sha256"], f"events[{ordinal - 1}].event_sha256")
        for field, field_value in item.items():
            if field.endswith("_hash"):
                _digest(field_value, f"events[{ordinal - 1}].{field}")
        if item["kind"] == "terminal":
            if item.get("terminal_outcome") not in TERMINAL_OUTCOMES:
                raise V4ResultViolation("terminal event has no supported outcome")
            terminal.append(item)
        elif "terminal_outcome" in item:
            raise V4ResultViolation("non-terminal event carries terminal outcome")
        result.append(item)
    if classification in {"PENDING", "ENVIRONMENT_BLOCKED"}:
        if terminal:
            raise V4ResultViolation("pending packets cannot contain a terminal")
    elif len(terminal) != 1:
        raise V4ResultViolation("terminal packet must contain exactly one terminal")
    expected = {"COMPLETE": "completed", "EXPECTED_NEGATIVE": "denied", "VERIFIED_FAILURE": None}.get(classification)
    if expected is not None and terminal[0]["terminal_outcome"] != expected:
        raise V4ResultViolation("classification and terminal outcome disagree")
    if classification == "VERIFIED_FAILURE" and terminal[0]["terminal_outcome"] not in {"failed", "cancelled"}:
        raise V4ResultViolation("verified failure requires failed or cancelled terminal")
    return result


def _row_for_packet(packet: Mapping[str, Any], contract: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = contract["source_rows"]
    key = (packet.get("source_pack"), packet.get("source_item_id"))
    for row in rows:
        if (row["source_pack"], row["source_item_id"]) == key:
            return row
    runtime = contract["runtime_soak"]
    if key == (runtime["source_pack"], runtime["source_item_id"]):
        return runtime
    raise V4ResultViolation("packet source row is not in the v4 contract")


def build_result_packet(contract: Mapping[str, Any], row: Mapping[str, Any], *, path: str, classification: str, candidate: Mapping[str, Any], billing_classification: str, preflight_results: Mapping[str, str], proof_hashes: Mapping[str, str], events: Sequence[Mapping[str, Any]], trial_index: int, turn_count: int = 0, reason_code: str | None = None) -> dict[str, Any]:
    """Build one sanitized packet; callers still need no provider or SDK."""

    validate_v4_contract(contract)
    if path not in PATHS or classification not in CLASSIFICATIONS or billing_classification not in BILLING:
        raise V4ResultViolation("unsupported path, classification, or billing")
    if reason_code is not None:
        _id(reason_code, "reason_code")
    if type(trial_index) is not int or trial_index < 1:
        raise V4ResultViolation("trial_index must be a positive integer")
    if type(turn_count) is not int or not 0 <= turn_count <= 180:
        raise V4ResultViolation("turn_count must be in [0, 180]")
    normalized_candidate = _candidate(candidate)
    source = _row_for_packet({"source_pack": row.get("source_pack"), "source_item_id": row.get("source_item_id")}, contract)
    if path not in source["mandatory_paths"]:
        raise V4ResultViolation("packet path is not mandatory for its source row")
    if source.get("source_pack") == "runtime_active" and turn_count != 100:
        raise V4ResultViolation("runtime soak packets must bind exactly 100 turns")
    preflights = dict(preflight_results)
    if set(preflights) != set(OWNERSHIP_PREFLIGHTS) or any(value != "PASS" for value in preflights.values()):
        raise V4ResultViolation("all Hermes ownership preflights must pass")
    proofs = dict(proof_hashes)
    if set(proofs) != {"primary", "secondary", "transcript", "stream"}:
        raise V4ResultViolation("proof hashes are incomplete")
    for key, value in proofs.items():
        _digest(value, f"proof_hashes.{key}")
    packet = {
        "schema_version": 4,
        "contract_version": V4_VERSION,
        "contract_sha256": sha256_value(contract["contract"]),
        "predecessor": {
            "contract_sha256": V3_HASHES["contract_sha256"],
            "ledger_sha256": V3_HASHES["boundary_ledger_sha256"],
            "result_schema_sha256": V3_HASHES["result_schema_sha256"],
            "execution_id": source["predecessor_execution_id"],
            "path": path,
        },
        "source_pack": source["source_pack"],
        "source_item_id": source["source_item_id"],
        "execution_id": source["predecessor_execution_id"],
        "successor_id": source["successor_id"],
        "path": path,
        "classification": classification,
        "candidate": normalized_candidate,
        "candidate_hash": sha256_value(normalized_candidate),
        "billing_classification": billing_classification,
        "silent_fallback": False,
        "preflight_results": preflights,
        "proof_hashes": proofs,
        "events": [dict(event) for event in events],
        "trial_index": trial_index,
        "turn_count": turn_count,
    }
    if reason_code is not None:
        packet["reason_code"] = reason_code
    packet["packet_sha256"] = sha256_value(packet)
    return validate_result_packet(packet, contract=contract)


def validate_result_packet(packet: Mapping[str, Any], *, contract: Mapping[str, Any]) -> dict[str, Any]:
    """Recalculate all packet identities and reject unsafe result states."""

    try:
        _reject_raw(packet)
        raw = _mapping(packet, "packet")
        # reason_code is intentionally optional for failure/pending packets.
        expected_fields = PACKET_FIELDS | ({"reason_code"} if "reason_code" in raw else set())
        if set(raw) != expected_fields:
            raise V4ResultViolation("packet fields are not closed")
        validate_v4_contract(contract)
        if raw["schema_version"] != 4 or raw["contract_version"] != V4_VERSION or raw["contract_sha256"] != sha256_value(contract["contract"]):
            raise V4ResultViolation("packet contract identity is wrong")
        predecessor = _mapping(raw["predecessor"], "predecessor")
        if set(predecessor) != {"contract_sha256", "ledger_sha256", "result_schema_sha256", "execution_id", "path"} or predecessor["contract_sha256"] != V3_HASHES["contract_sha256"] or predecessor["ledger_sha256"] != V3_HASHES["boundary_ledger_sha256"] or predecessor["result_schema_sha256"] != V3_HASHES["result_schema_sha256"]:
            raise V4ResultViolation("packet predecessor hashes are wrong")
        row = _row_for_packet(raw, contract)
        if raw["execution_id"] != row["predecessor_execution_id"] or raw["successor_id"] != row["successor_id"] or predecessor["execution_id"] != raw["execution_id"] or predecessor["path"] != raw["path"]:
            raise V4ResultViolation("packet predecessor/path identity is wrong")
        if raw["path"] not in row["mandatory_paths"]:
            raise V4ResultViolation("packet path is not mandatory")
        classification = raw["classification"]
        if classification not in CLASSIFICATIONS or classification in {"NOT_RUN", "PARTIAL"}:
            raise V4ResultViolation("packet classification is unsupported")
        if raw["billing_classification"] not in BILLING or raw["silent_fallback"] is not False:
            raise V4ResultViolation("unsafe billing or fallback evidence")
        normalized_candidate = _candidate(raw["candidate"])
        if raw["candidate_hash"] != sha256_value(normalized_candidate):
            raise V4ResultViolation("candidate hash does not match exact identity")
        if not isinstance(raw["preflight_results"], Mapping) or dict(raw["preflight_results"]) != {name: "PASS" for name in OWNERSHIP_PREFLIGHTS}:
            raise V4ResultViolation("ownership preflights did not all pass")
        proofs = _mapping(raw["proof_hashes"], "proof_hashes")
        if set(proofs) != {"primary", "secondary", "transcript", "stream"}:
            raise V4ResultViolation("proof hashes are incomplete")
        for key, value in proofs.items():
            _digest(value, f"proof_hashes.{key}")
        events = _events(raw["events"], classification)
        trial_index = raw["trial_index"]
        if type(trial_index) is not int or trial_index < 1:
            raise V4ResultViolation("trial_index must be a positive integer")
        turn_count = raw["turn_count"]
        if type(turn_count) is not int or not 0 <= turn_count <= 180:
            raise V4ResultViolation("turn_count is outside the bounded budget")
        if row.get("source_pack") == "runtime_active" and turn_count != 100:
            raise V4ResultViolation("runtime packet must bind exactly 100 turns")
        if classification in {"COMPLETE", "EXPECTED_NEGATIVE"} and any(value is None for value in proofs.values()):
            raise V4ResultViolation("passing packet must carry all proof hashes")
        if "reason_code" in raw:
            _id(raw["reason_code"], "reason_code")
        without_hash = dict(raw)
        without_hash.pop("packet_sha256")
        if raw["packet_sha256"] != sha256_value(without_hash):
            raise V4ResultViolation("packet hash does not match content")
        result = copy.deepcopy(raw)
        result["candidate"] = normalized_candidate
        result["events"] = events
        return result
    except (KeyError, TypeError, V4ContractViolation) as exc:
        if isinstance(exc, V4ResultViolation):
            raise
        raise V4ResultViolation("packet validation failed") from exc


def grade_result_packets(packets: Sequence[Mapping[str, Any]], *, contract: Mapping[str, Any], lane: str = "rc") -> dict[str, Any]:
    """Grade a packet set without treating missing evidence as a pass."""

    validate_v4_contract(contract)
    if not isinstance(packets, Sequence) or isinstance(packets, (str, bytes, bytearray)):
        raise V4ResultViolation("packets must be a sequence")
    valid = [validate_result_packet(packet, contract=contract) for packet in packets]
    if lane not in {"rc", "runtime"}:
        raise V4ResultViolation("grade lane must be rc or runtime")
    rows = list(contract["source_rows"] if lane == "rc" else ())
    expected = {(row["source_pack"], row["source_item_id"], path) for row in rows for path in row["mandatory_paths"]}
    if lane == "runtime":
        expected = {(contract["runtime_soak"]["source_pack"], contract["runtime_soak"]["source_item_id"], path) for path in contract["runtime_soak"]["mandatory_paths"]}
    seen: set[tuple[str, str, str]] = set()
    identities: set[str] = set()
    for packet in valid:
        key = (packet["source_pack"], packet["source_item_id"], packet["path"])
        if key in seen:
            raise V4ResultViolation("duplicate source/path packet")
        if key not in expected:
            raise V4ResultViolation("packet names an unknown or non-mandatory path")
        seen.add(key)
        identities.add(packet["candidate_hash"])
    if len(identities) > 1:
        raise V4ResultViolation("one grade cannot combine candidate identities")
    details = []
    complete = pending = failed = 0
    for key in sorted(expected):
        packet = next((item for item in valid if (item["source_pack"], item["source_item_id"], item["path"]) == key), None)
        if packet is None or packet["classification"] in {"PENDING", "ENVIRONMENT_BLOCKED"}:
            status = "PENDING"
            pending += 1
        else:
            required_class = "EXPECTED_NEGATIVE" if key[2] == "denial" else "COMPLETE"
            status = "COMPLETE" if packet["classification"] == required_class else "VERIFIED_FAILURE"
            if status == "COMPLETE":
                complete += 1
            else:
                failed += 1
        details.append({"source_pack": key[0], "source_item_id": key[1], "path": key[2], "status": status})
    report_status = "VERIFIED_FAILURE" if failed else "PENDING" if pending else "COMPLETE"
    return {
        "schema_version": 4,
        "status": report_status,
        "exit_code": 1 if failed else 75 if pending else 0,
        "required_paths": len(expected),
        "complete_paths": complete,
        "pending_paths": pending,
        "failed_paths": failed,
        "candidate_hash": next(iter(identities), None),
        "disposition_totals": dict(Counter(row["disposition"] for row in rows)),
        "path_results": details,
        "proof_boundary": "Deterministic v4 packet grading only; this report does not prove provider-live execution, installation, release, runtime, fleet, or customer readiness.",
    }


__all__ = ["V4ResultViolation", "build_result_packet", "grade_result_packets", "validate_result_packet"]
