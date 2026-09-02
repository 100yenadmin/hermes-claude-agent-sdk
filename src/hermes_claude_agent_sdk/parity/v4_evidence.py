"""Bind one completed existing-runner trial to the closed v4 packet schema."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .hashing import sha256_value
from .results import ResultPacket
from .v4_contract import (
    OWNERSHIP_PREFLIGHTS,
    V4_CLI_VERSION,
    V4_MODEL,
    V4_RUNNER_ID,
    V4_RUNNER_VERSION,
    V4_SDK_DISTRIBUTION,
    V4_SDK_VERSION,
    validate_v4_contract,
)
from .v4_runner import CLASSIFICATIONS, build_result_packet

V3_CONTRACT_HASH = "aaddc44c53b5648202e34c5682a5c0ee599fa52b896c0530d0945cac95eb3244"
V3_CATALOG_HASH = "768c2d8f99077f8557a192d1053fc80401e83dee80d77475d12119df75b63abb"
_HEX = frozenset("0123456789abcdef")
_CANDIDATE_FIELDS = frozenset({"plugin_sha", "host_sha", "wheel_sha256", "profile_sha256", "sdk_distribution", "sdk_version", "cli_version", "model", "runner_id", "runner_version"})
_RECEIPT_FIELDS = frozenset({"schema_version", "candidate", "candidate_hash", "trial_candidate_hash", "trial_index", "preflight_results", "proof_hashes"})
_PROOF_FIELDS = frozenset({"primary", "secondary", "transcript", "stream"})


class V4EvidenceViolation(ValueError):
    """Supplied predecessor or ownership evidence cannot be bound safely."""


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise V4EvidenceViolation(f"{field} must be a mapping")
    return dict(value)


def _digest(value: Any, field: str, length: int = 64, *, nonzero: bool = False) -> str:
    if not isinstance(value, str) or len(value) != length or set(value) - _HEX or nonzero and value == "0" * length:
        raise V4EvidenceViolation(f"{field} must be a nonzero lowercase digest" if nonzero else f"{field} must be a lowercase digest")
    return value


def _trial(value: Any) -> ResultPacket:
    value = value.to_dict() if isinstance(value, ResultPacket) else value
    try:
        return ResultPacket.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise V4EvidenceViolation("underlying trial evidence is absent or invalid") from exc


def _candidate(receipt: Mapping[str, Any], trial: ResultPacket) -> dict[str, Any]:
    candidate = _mapping(receipt.get("candidate"), "receipt.candidate")
    if set(candidate) != _CANDIDATE_FIELDS:
        raise V4EvidenceViolation("candidate identity is incomplete or has unknown fields")
    for key, size in (("plugin_sha", 40), ("host_sha", 40), ("wheel_sha256", 64), ("profile_sha256", 64)):
        _digest(candidate[key], f"candidate.{key}", size, nonzero=True)
    target = {"sdk_distribution": V4_SDK_DISTRIBUTION, "sdk_version": V4_SDK_VERSION, "cli_version": V4_CLI_VERSION, "model": V4_MODEL, "runner_id": V4_RUNNER_ID, "runner_version": V4_RUNNER_VERSION}
    shared = {"plugin_sha": trial.plugin_sha, "host_sha": trial.host_sha, "profile_sha256": trial.profile_hash, "sdk_version": trial.sdk_version, "runner_version": trial.runner_version}
    if any(candidate[key] != value for key, value in target.items()) or any(candidate[key] != value for key, value in shared.items()):
        raise V4EvidenceViolation("trial and ownership receipt candidate identities differ")
    _digest(receipt["candidate_hash"], "receipt.candidate_hash", nonzero=True)
    if receipt["candidate_hash"] != sha256_value(candidate):
        raise V4EvidenceViolation("ownership receipt candidate hash is not exact")
    _digest(receipt["trial_candidate_hash"], "receipt.trial_candidate_hash", nonzero=True)
    if receipt["trial_candidate_hash"] != trial.candidate_hash:
        raise V4EvidenceViolation("ownership receipt does not bind the trial candidate")
    return candidate


def _receipt(value: Any, trial: ResultPacket) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    receipt = _mapping(value, "ownership receipt")
    if set(receipt) != _RECEIPT_FIELDS or receipt["schema_version"] != 1:
        raise V4EvidenceViolation("ownership receipt fields are not closed")
    candidate = _candidate(receipt, trial)
    if type(receipt["trial_index"]) is not int or receipt["trial_index"] < 1 or receipt["trial_index"] != trial.trial_index:
        raise V4EvidenceViolation("ownership receipt trial repetition does not match evidence")
    raw_preflights = _mapping(receipt["preflight_results"], "receipt.preflight_results")
    if set(raw_preflights) != set(OWNERSHIP_PREFLIGHTS):
        raise V4EvidenceViolation("ownership preflight evidence is incomplete")
    statuses: dict[str, str] = {}
    for name in OWNERSHIP_PREFLIGHTS:
        item = _mapping(raw_preflights[name], f"receipt.preflight_results.{name}")
        if set(item) != {"status", "evidence_sha256"} or item["status"] != "PASS":
            raise V4EvidenceViolation(f"ownership preflight {name} lacks external PASS evidence")
        _digest(item["evidence_sha256"], f"receipt.preflight_results.{name}.evidence_sha256", nonzero=True)
        statuses[name] = item["status"]
    proofs = _mapping(receipt["proof_hashes"], "receipt.proof_hashes")
    if set(proofs) != _PROOF_FIELDS:
        raise V4EvidenceViolation("ownership proof hashes are incomplete")
    for name in _PROOF_FIELDS:
        _digest(proofs[name], f"receipt.proof_hashes.{name}", nonzero=True)
    if (proofs["primary"], proofs["secondary"], proofs["transcript"]) != (trial.primary_proof_hash, trial.secondary_proof_hash, trial.trace_hash):
        raise V4EvidenceViolation("ownership proof hashes do not match the observed trial")
    return candidate, statuses, proofs


def _source_row(contract: Mapping[str, Any], trial: ResultPacket) -> Mapping[str, Any]:
    if trial.source_pack == "runtime_active" and trial.capability_id == "runtime:active-100-turn":
        runtime = contract["runtime_soak"]
        if trial.execution_id == runtime["predecessor_execution_id"]:
            return runtime
    matches = [row for row in contract["source_rows"] if row["predecessor_capability_id"] == trial.capability_id]
    if len(matches) != 1:
        raise V4EvidenceViolation("trial has no unique v4 predecessor row")
    row = matches[0]
    if (trial.source_pack, trial.execution_id) != (row["source_pack"], row["predecessor_execution_id"]):
        raise V4EvidenceViolation("trial predecessor identity does not match v4 source row")
    return row


def bind_v4_evidence(contract: Mapping[str, Any], trial: ResultPacket | Mapping[str, Any], ownership_receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Create one v4 packet from one already-recorded trial with valid evidence.

    This function performs no execution and copies the validated trial events
    unchanged.  Missing evidence never becomes a synthetic v4 outcome.
    """
    try:
        validate_v4_contract(contract)
        observed = _trial(trial)
        if observed.contract_hash != V3_CONTRACT_HASH:
            raise V4EvidenceViolation("trial contract hash is not the immutable v3 envelope")
        if observed.catalog_hash != V3_CATALOG_HASH:
            raise V4EvidenceViolation("trial catalog hash is not the immutable v3 catalog")
        if observed.classification.value not in CLASSIFICATIONS:
            raise V4EvidenceViolation("trial classification is unsupported by v4")
        row = _source_row(contract, observed)
        if observed.path not in row["mandatory_paths"]:
            raise V4EvidenceViolation("trial path is not mandatory for its v4 row")
        candidate, statuses, proofs = _receipt(ownership_receipt, observed)
        events = [dict(event) for event in observed.normalized_events]
        packet = build_result_packet(contract, row, path=observed.path, classification=observed.classification.value, candidate=candidate, billing_classification=observed.billing_classification, preflight_results=statuses, proof_hashes=proofs, events=events, trial_index=observed.trial_index, turn_count=observed.turn_count, reason_code=observed.reason_code)
        if (packet["classification"], packet["path"], packet["trial_index"], packet["turn_count"], packet["events"]) != (observed.classification.value, observed.path, observed.trial_index, observed.turn_count, events):
            raise V4EvidenceViolation("adapter changed observed trial evidence")
        if packet["candidate_hash"] != ownership_receipt["candidate_hash"]:
            raise V4EvidenceViolation("adapter produced an unbound candidate identity")
        return packet
    except V4EvidenceViolation:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise V4EvidenceViolation("v4 evidence binding failed closed") from exc


bind_v4_packet = bind_v4_evidence
bind_v4_result_packet = bind_v4_evidence
EvidenceBindingViolation = V4EvidenceViolation

__all__ = ["V3_CATALOG_HASH", "V3_CONTRACT_HASH", "EvidenceBindingViolation", "V4EvidenceViolation", "bind_v4_evidence", "bind_v4_packet", "bind_v4_result_packet"]
