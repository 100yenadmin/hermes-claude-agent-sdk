"""Build strict, provider-free v4 ownership receipts from recorded evidence.

The builder consumes only a validated predecessor :class:`ResultPacket` and
sanitized projection documents.  It deliberately does not execute a preflight
or infer one from a status string.  The returned mapping is the small receipt
shape consumed by :func:`bind_v4_evidence`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .hashing import json_compatible, sha256_value
from .results import ResultPacket, ResultViolation
from .v4_contract import (
    OWNERSHIP_PREFLIGHTS,
    V3_RESULT_CATALOG_HASH,
    V3_RESULT_CONTRACT_HASH,
    V4_CLI_VERSION,
    V4_MODEL,
    V4_RUNNER_ID,
    V4_RUNNER_VERSION,
    V4_SDK_DISTRIBUTION,
    V4_SDK_VERSION,
)

Candidate = dict[str, Any]
Projection = dict[str, Any]

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+#\-]{0,255}$")
_CANDIDATE_FIELDS = frozenset("plugin_sha host_sha wheel_sha256 profile_sha256 sdk_distribution sdk_version cli_version model runner_id runner_version".split())
_SOURCE_FIELDS = frozenset({"executable", "source_ref", "test_id"})
_PROJECTION_FIELDS = frozenset("schema_version name candidate_hash status source observation".split())
_STREAM_FIELDS = _PROJECTION_FIELDS | frozenset({"trial_candidate_hash", "trial_index"})
_RAW_KEYS = frozenset("raw raw_prompt raw_content raw_transcript rawprompt rawcontent rawtranscript messages message prompt content transcript session session_id sessionid raw_session credential credentials password secret secrets token access_token accesstoken refresh_token refreshtoken api_key apikey auth auth_material authmaterial config configuration cookie cookies evidence_sha256 proof_hash proof_sha256 stream_hash".split())


class OwnershipReceiptViolation(ValueError):
    """A predecessor or sanitized projection cannot form a safe receipt."""


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OwnershipReceiptViolation(f"{field} must be a mapping")
    try:
        normalized = json_compatible(value)
    except TypeError as exc:
        raise OwnershipReceiptViolation(f"{field} is not JSON-compatible") from exc
    if not isinstance(normalized, dict):  # pragma: no cover
        raise OwnershipReceiptViolation(f"{field} must be a mapping")
    return normalized


def _reject_raw(value: Any, location: str = "projection") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise OwnershipReceiptViolation(f"{location} contains a non-string key")
            lowered = key.casefold().replace("-", "_")
            if lowered in _RAW_KEYS or lowered.startswith("raw_") or lowered.endswith("_raw"):
                raise OwnershipReceiptViolation(f"{location} contains forbidden raw-data field")
            _reject_raw(child, f"{location}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_raw(child, f"{location}[{index}]")


def _digest(value: Any, field: str, *, length: int = 64) -> str:
    pattern = _HEX40 if length == 40 else _HEX64
    if not isinstance(value, str) or pattern.fullmatch(value) is None or value == "0" * length:
        raise OwnershipReceiptViolation(f"{field} must be a nonzero lowercase digest")
    return value


def _safe_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise OwnershipReceiptViolation(f"{field} must be a bounded sanitized identity")
    return value


def _observation(value: Any, field: str = "observation") -> dict[str, Any]:
    raw = _mapping(value, field)
    if not raw:
        raise OwnershipReceiptViolation(f"{field} must contain sanitized observation data")
    _reject_raw(raw, field)

    def visit(item: Any, location: str) -> Any:
        if isinstance(item, Mapping):
            if not item:
                raise OwnershipReceiptViolation(f"{location} must not be empty")
            if len(item) > 128:
                raise OwnershipReceiptViolation(f"{location} has too many fields")
            normalized = {}
            for key, child in item.items():
                _safe_id(key, f"{location} key")
                normalized[key] = (_digest(child, f"{location}.{key}")
                                   if key.casefold().endswith("_hash")
                                   else visit(child, f"{location}.{key}"))
            return normalized
        if isinstance(item, list):
            if len(item) > 128:
                raise OwnershipReceiptViolation(f"{location} has too many entries")
            return [visit(child, f"{location}[{index}]") for index, child in enumerate(item)]
        if type(item) is bool:
            return item
        if type(item) is int:
            if item < -1 or item > 1_000_000_000:
                raise OwnershipReceiptViolation(f"{location} integer is outside the bounded range")
            return item
        if isinstance(item, str):
            return _safe_id(item, location)
        raise OwnershipReceiptViolation(f"{location} must be a sanitized scalar")

    normalized = visit(raw, field)
    if not isinstance(normalized, dict):  # pragma: no cover
        raise OwnershipReceiptViolation(f"{field} must be a mapping")
    return normalized


def _source(value: Any, field: str) -> dict[str, str]:
    source = _mapping(value, field)
    if set(source) != _SOURCE_FIELDS:
        raise OwnershipReceiptViolation(f"{field} identity fields are incomplete or unknown")
    return {key: _safe_id(source[key], f"{field}.{key}") for key in _SOURCE_FIELDS}


def _trial(value: Any) -> ResultPacket:
    try:
        return value if isinstance(value, ResultPacket) else ResultPacket.from_dict(value)
    except (ResultViolation, TypeError, ValueError) as exc:
        raise OwnershipReceiptViolation("underlying predecessor trial is absent or invalid") from exc


def _candidate(value: Any, trial: ResultPacket) -> tuple[Candidate, str]:
    candidate = _mapping(value, "candidate")
    if set(candidate) != _CANDIDATE_FIELDS:
        raise OwnershipReceiptViolation("candidate identity must contain exactly the ten v4 fields")
    for key, length in (("plugin_sha", 40), ("host_sha", 40), ("wheel_sha256", 64), ("profile_sha256", 64)):
        _digest(candidate[key], f"candidate.{key}", length=length)
    expected = {"sdk_distribution": V4_SDK_DISTRIBUTION, "sdk_version": V4_SDK_VERSION,
                "cli_version": V4_CLI_VERSION, "model": V4_MODEL, "runner_id": V4_RUNNER_ID,
                "runner_version": V4_RUNNER_VERSION}
    for key, value in expected.items():
        if candidate[key] != value:
            raise OwnershipReceiptViolation(f"candidate.{key} does not match the frozen v4 identity")
    if candidate["plugin_sha"] != trial.plugin_sha or candidate["host_sha"] != trial.host_sha:
        raise OwnershipReceiptViolation("candidate does not match predecessor plugin or host")
    if candidate["sdk_version"] != trial.sdk_version or candidate["profile_sha256"] != trial.profile_hash:
        raise OwnershipReceiptViolation("candidate does not match predecessor SDK or profile")
    if candidate["runner_version"] != trial.runner_version:
        raise OwnershipReceiptViolation("candidate does not match predecessor runner")
    return candidate, sha256_value(candidate)


def _projection(value: Any, name: str, candidate_hash: str) -> tuple[Projection, str]:
    projection = _mapping(value, f"preflight projection {name}")
    if set(projection) != _PROJECTION_FIELDS:
        raise OwnershipReceiptViolation(f"preflight projection {name} fields are not closed")
    _reject_raw(projection, f"preflight projection {name}")
    if projection["schema_version"] != 1 or projection["name"] != name or projection["status"] != "PASS":
        raise OwnershipReceiptViolation(f"preflight projection {name} is not a successful named v1 document")
    if _digest(projection["candidate_hash"], f"preflight projection {name}.candidate_hash") != candidate_hash:
        raise OwnershipReceiptViolation(f"preflight projection {name} is bound to another candidate")
    normalized = {
        "schema_version": 1,
        "name": name,
        "candidate_hash": candidate_hash,
        "status": "PASS",
        "source": _source(projection["source"], f"preflight projection {name}.source"),
        "observation": _observation(projection["observation"], f"preflight projection {name}.observation"),
    }
    return normalized, sha256_value(normalized)


def _stream_projection(value: Any, candidate_hash: str, trial: ResultPacket) -> tuple[Projection, str]:
    projection = _mapping(value, "stream projection")
    if set(projection) != _STREAM_FIELDS:
        raise OwnershipReceiptViolation("stream projection fields are not closed")
    _reject_raw(projection, "stream projection")
    if projection["schema_version"] != 1 or projection["name"] != "stream" or projection["status"] != "PASS":
        raise OwnershipReceiptViolation("stream projection is not a successful named v1 document")
    if _digest(projection["candidate_hash"], "stream projection.candidate_hash") != candidate_hash:
        raise OwnershipReceiptViolation("stream projection is bound to another candidate")
    if _digest(projection["trial_candidate_hash"], "stream projection.trial_candidate_hash") != trial.candidate_hash:
        raise OwnershipReceiptViolation("stream projection is bound to another predecessor candidate")
    if type(projection["trial_index"]) is not int or projection["trial_index"] != trial.trial_index:
        raise OwnershipReceiptViolation("stream projection trial repetition does not match predecessor")
    normalized = {
        "schema_version": 1,
        "name": "stream",
        "candidate_hash": candidate_hash,
        "trial_candidate_hash": trial.candidate_hash,
        "trial_index": trial.trial_index,
        "status": "PASS",
        "source": _source(projection["source"], "stream projection.source"),
        "observation": _observation(projection["observation"], "stream projection.observation"),
    }
    return normalized, sha256_value(normalized)


def _write_create_only(receipt: Mapping[str, Any], output: str | Path) -> None:
    destination = Path(output).expanduser()
    if destination.exists() or destination.is_symlink():
        raise OwnershipReceiptViolation("refusing to replace pre-existing ownership receipt")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(receipt, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
    except FileExistsError as exc:
        raise OwnershipReceiptViolation("refusing to replace pre-existing ownership receipt") from exc
    except (OSError, TypeError, ValueError) as exc:
        raise OwnershipReceiptViolation("ownership receipt could not be persisted") from exc


def build_ownership_receipt(
    trial: ResultPacket | Mapping[str, Any],
    candidate: Mapping[str, Any],
    preflight_projections: Mapping[str, Mapping[str, Any]],
    stream_projection: Mapping[str, Any],
    *,
    output: str | Path | None = None,
) -> dict[str, Any]:
    """Build one receipt from one validated trial and sanitized projections.

    No input digest is trusted: the eight preflight and stream digests are
    calculated from normalized projection values.  ``output`` is optional and,
    when supplied, is create-only so a prior artifact can never be replaced.
    """

    observed = _trial(trial)
    if observed.contract_hash != V3_RESULT_CONTRACT_HASH or observed.catalog_hash != V3_RESULT_CATALOG_HASH:
        raise OwnershipReceiptViolation("predecessor trial is not the immutable v3 envelope")
    if not observed.packet_hash or observed.packet_hash == "0" * 64:
        raise OwnershipReceiptViolation("predecessor trial packet hash is empty")
    if not observed.candidate_hash or observed.candidate_hash == "0" * 64:
        raise OwnershipReceiptViolation("predecessor trial candidate hash is empty")
    if observed.primary_proof_hash is None or observed.secondary_proof_hash is None:
        raise OwnershipReceiptViolation("predecessor trial lacks primary or secondary proof")
    _digest(observed.primary_proof_hash, "trial.primary_proof_hash")
    _digest(observed.secondary_proof_hash, "trial.secondary_proof_hash")
    _digest(observed.trace_hash, "trial.trace_hash")
    normalized_candidate, candidate_digest = _candidate(candidate, observed)
    if not isinstance(preflight_projections, Mapping):
        raise OwnershipReceiptViolation("preflight projections must be a mapping")
    if set(preflight_projections) != set(OWNERSHIP_PREFLIGHTS):
        raise OwnershipReceiptViolation("preflight projections must contain exactly the eight named checks")
    preflight_results: dict[str, dict[str, str]] = {}
    for name in OWNERSHIP_PREFLIGHTS:
        _, evidence_digest = _projection(preflight_projections[name], name, candidate_digest)
        preflight_results[name] = {"status": "PASS", "evidence_sha256": evidence_digest}
    _, stream_digest = _stream_projection(stream_projection, candidate_digest, observed)
    receipt = {
        "schema_version": 1,
        "candidate": normalized_candidate,
        "candidate_hash": candidate_digest,
        "trial_candidate_hash": observed.candidate_hash,
        "trial_index": observed.trial_index,
        "preflight_results": preflight_results,
        "proof_hashes": {
            "primary": observed.primary_proof_hash,
            "secondary": observed.secondary_proof_hash,
            "transcript": observed.trace_hash,
            "stream": stream_digest,
        },
    }
    if output is not None:
        _write_create_only(receipt, output)
    return receipt


build_v4_ownership_receipt = build_ownership_receipt
OwnershipReceiptError = OwnershipReceiptViolation
V4ReceiptViolation = OwnershipReceiptViolation

__all__ = [
    "OWNERSHIP_PREFLIGHTS",
    "OwnershipReceiptError",
    "OwnershipReceiptViolation",
    "V4ReceiptViolation",
    "build_ownership_receipt",
    "build_v4_ownership_receipt",
]
