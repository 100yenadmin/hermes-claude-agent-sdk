"""Sanitized parity result packets with exact candidate binding."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .hashing import json_compatible, sha256_value


class ResultViolation(ValueError):
    """A result packet violates the v3 evidence or safety contract."""


class ExecutionClassification(str, Enum):
    PENDING = "PENDING"
    EXPECTED_NEGATIVE = "EXPECTED_NEGATIVE"
    ENVIRONMENT_BLOCKED = "ENVIRONMENT_BLOCKED"
    VERIFIED_FAILURE = "VERIFIED_FAILURE"
    COMPLETE = "COMPLETE"


_HASH = re.compile(r"^[0-9a-f]{64}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,255}$")
_PACKET_FIELDS = frozenset(
    {
        "schema_version",
        "capability_id",
        "source_pack",
        "lane",
        "path",
        "execution_id",
        "classification",
        "contract_hash",
        "catalog_hash",
        "plugin_sha",
        "host_sha",
        "sdk_version",
        "profile_id",
        "profile_hash",
        "runner_version",
        "inventory_hash",
        "candidate_hash",
        "billing_classification",
        "turn_count",
        "trial_index",
        "normalized_events",
        "trace_hash",
        "primary_proof_hash",
        "secondary_proof_hash",
        "silent_fallback",
        "invariant_violations",
        "reason_code",
        "packet_hash",
    }
)
_EVENT_FIELDS = frozenset(
    {
        "sequence",
        "kind",
        "status",
        "name_hash",
        "request_hash",
        "state_hash",
        "usage_hash",
        "schema_hash",
        "tool_hash",
        "parent_hash",
        "terminal_outcome",
        "metadata_hash",
    }
)
_EVENT_KINDS = frozenset(
    {
        "start",
        "approval_requested",
        "approval_decision",
        "tool_requested",
        "tool_result",
        "state",
        "usage",
        "compaction",
        "background",
        "restart",
        "terminal",
    }
)
_TERMINAL_OUTCOMES = frozenset({"completed", "denied", "failed", "cancelled"})
_BILLING = frozenset({"subscription_included", "explicitly_free", "none", "unsafe", "unknown"})
_FORBIDDEN_KEYS = frozenset(
    {
        "raw_content",
        "raw_prompt",
        "raw_session",
        "messages",
        "auth_material",
        "credential",
        "credentials",
        "api_key",
        "access_token",
        "refresh_token",
        "session_id",
    }
)
_MAX_PACKET_BYTES = 2 * 1024 * 1024
_MAX_NORMALIZED_EVENTS = 10_000


def _safe_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ResultViolation(f"{field} is not a safe identifier")
    return value


def _hash(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ResultViolation(f"{field} must be a lowercase SHA-256 digest")
    return value


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ResultViolation(f"{field} must be a full lowercase Git SHA")
    return value


def _reject_forbidden_keys(value: Any, location: str = "packet") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in _FORBIDDEN_KEYS:
                raise ResultViolation(f"{location} contains forbidden field {key}")
            _reject_forbidden_keys(child, f"{location}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, f"{location}[{index}]")


def _normalize_events(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ResultViolation("normalized_events must be a list")
    if len(value) > _MAX_NORMALIZED_EVENTS:
        raise ResultViolation("normalized_events exceeds the bounded event count")
    events: list[Mapping[str, Any]] = []
    for index, raw in enumerate(value, 1):
        if not isinstance(raw, Mapping):
            raise ResultViolation(f"normalized_events[{index - 1}] must be a mapping")
        unknown = set(raw) - _EVENT_FIELDS
        if unknown:
            raise ResultViolation(
                f"normalized_events[{index - 1}] has unknown fields: {sorted(unknown)}"
            )
        if raw.get("sequence") != index:
            raise ResultViolation("normalized event sequence must be contiguous and one-based")
        kind = raw.get("kind")
        if kind not in _EVENT_KINDS:
            raise ResultViolation(f"normalized_events[{index - 1}].kind is unsupported")
        status = raw.get("status")
        if status is not None:
            _safe_id(status, f"normalized_events[{index - 1}].status")
        for field in _EVENT_FIELDS & set(raw):
            if field.endswith("_hash"):
                _hash(raw[field], f"normalized_events[{index - 1}].{field}")
        terminal_outcome = raw.get("terminal_outcome")
        if kind == "terminal":
            if terminal_outcome not in _TERMINAL_OUTCOMES:
                raise ResultViolation("terminal events require a supported terminal_outcome")
        elif terminal_outcome is not None:
            raise ResultViolation("terminal_outcome is allowed only on terminal events")
        events.append(MappingProxyType(dict(raw)))
    return tuple(events)


def candidate_hash(
    *,
    catalog_hash: str,
    plugin_sha: str,
    host_sha: str,
    sdk_version: str,
    profile_hash: str,
    runner_version: str,
    inventory_hash: str,
) -> str:
    """Bind every repeat-sensitive candidate dimension into one identity."""

    return sha256_value(
        {
            "catalog_hash": catalog_hash,
            "plugin_sha": plugin_sha,
            "host_sha": host_sha,
            "sdk_version": sdk_version,
            "profile_hash": profile_hash,
            "runner_version": runner_version,
            "inventory_hash": inventory_hash,
        }
    )


@dataclass(frozen=True, slots=True)
class ResultPacket:
    schema_version: int
    capability_id: str
    source_pack: str
    lane: str
    path: str
    execution_id: str
    classification: ExecutionClassification
    contract_hash: str
    catalog_hash: str
    plugin_sha: str
    host_sha: str
    sdk_version: str
    profile_id: str
    profile_hash: str
    runner_version: str
    inventory_hash: str
    candidate_hash: str
    billing_classification: str
    turn_count: int
    trial_index: int
    normalized_events: tuple[Mapping[str, Any], ...]
    trace_hash: str
    primary_proof_hash: str | None
    secondary_proof_hash: str | None
    silent_fallback: bool
    invariant_violations: tuple[str, ...]
    reason_code: str | None
    packet_hash: str

    @classmethod
    def build(
        cls,
        *,
        capability_id: str,
        source_pack: str,
        lane: str,
        path: str,
        execution_id: str,
        classification: ExecutionClassification | str,
        contract_hash: str,
        catalog_hash: str,
        plugin_sha: str,
        host_sha: str,
        sdk_version: str,
        profile_id: str,
        profile_hash: str,
        runner_version: str,
        inventory_hash: str,
        billing_classification: str,
        turn_count: int = 0,
        trial_index: int,
        normalized_events: Sequence[Mapping[str, Any]] = (),
        primary_proof_hash: str | None = None,
        secondary_proof_hash: str | None = None,
        silent_fallback: bool = False,
        invariant_violations: Sequence[str] = (),
        reason_code: str | None = None,
    ) -> "ResultPacket":
        events = [dict(event) for event in normalized_events]
        identity = candidate_hash(
            catalog_hash=catalog_hash,
            plugin_sha=plugin_sha,
            host_sha=host_sha,
            sdk_version=sdk_version,
            profile_hash=profile_hash,
            runner_version=runner_version,
            inventory_hash=inventory_hash,
        )
        raw: dict[str, Any] = {
            "schema_version": 1,
            "capability_id": capability_id,
            "source_pack": source_pack,
            "lane": lane,
            "path": path,
            "execution_id": execution_id,
            "classification": ExecutionClassification(classification).value,
            "contract_hash": contract_hash,
            "catalog_hash": catalog_hash,
            "plugin_sha": plugin_sha,
            "host_sha": host_sha,
            "sdk_version": sdk_version,
            "profile_id": profile_id,
            "profile_hash": profile_hash,
            "runner_version": runner_version,
            "inventory_hash": inventory_hash,
            "candidate_hash": identity,
            "billing_classification": billing_classification,
            "turn_count": turn_count,
            "trial_index": trial_index,
            "normalized_events": events,
            "trace_hash": sha256_value(events),
            "primary_proof_hash": primary_proof_hash,
            "secondary_proof_hash": secondary_proof_hash,
            "silent_fallback": silent_fallback,
            "invariant_violations": list(invariant_violations),
            "reason_code": reason_code,
        }
        raw["packet_hash"] = sha256_value(raw)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, value: Any) -> "ResultPacket":
        try:
            raw = json_compatible(value)
        except TypeError as exc:
            raise ResultViolation(f"packet is not JSON-compatible: {exc}") from exc
        if not isinstance(raw, Mapping) or set(raw) != _PACKET_FIELDS:
            missing = sorted(_PACKET_FIELDS - set(raw) if isinstance(raw, Mapping) else _PACKET_FIELDS)
            unknown = sorted(set(raw) - _PACKET_FIELDS if isinstance(raw, Mapping) else ())
            raise ResultViolation(f"packet fields do not match schema: missing={missing}, unknown={unknown}")
        _reject_forbidden_keys(raw)
        if raw["schema_version"] != 1:
            raise ResultViolation("packet schema_version must equal 1")
        try:
            classification = ExecutionClassification(raw["classification"])
        except (TypeError, ValueError) as exc:
            raise ResultViolation("classification is unsupported") from exc
        capability_id = _safe_id(raw["capability_id"], "capability_id")
        source_pack = _safe_id(raw["source_pack"], "source_pack")
        lane = raw["lane"]
        if lane not in {"rc", "runtime"}:
            raise ResultViolation("lane must be rc or runtime")
        path = raw["path"]
        if path not in {"positive", "denial", "recovery"}:
            raise ResultViolation("path must be positive, denial, or recovery")
        execution_id = _safe_id(raw["execution_id"], "execution_id")
        sdk_version = _safe_id(raw["sdk_version"], "sdk_version")
        profile_id = _safe_id(raw["profile_id"], "profile_id")
        runner_version = _safe_id(raw["runner_version"], "runner_version")
        billing = raw["billing_classification"]
        if billing not in _BILLING:
            raise ResultViolation("billing_classification is unsupported")
        if billing in {"unsafe", "unknown"}:
            raise ResultViolation("unsafe or unknown billing evidence fails closed")
        turn_count = raw["turn_count"]
        if type(turn_count) is not int or turn_count < 0 or turn_count > 180:
            raise ResultViolation("turn_count must be an integer in [0, 180]")
        trial_index = raw["trial_index"]
        if type(trial_index) is not int or trial_index < 1:
            raise ResultViolation("trial_index must be a positive integer")
        if type(raw["silent_fallback"]) is not bool:
            raise ResultViolation("silent_fallback must be a boolean")
        if raw["silent_fallback"]:
            raise ResultViolation("silent fallback is forbidden")
        violations_raw = raw["invariant_violations"]
        if not isinstance(violations_raw, Sequence) or isinstance(
            violations_raw, (str, bytes, bytearray)
        ):
            raise ResultViolation("invariant_violations must be a list")
        violations = tuple(_safe_id(item, "invariant_violations") for item in violations_raw)
        if violations:
            raise ResultViolation("invariant violations fail the contract")
        reason_code = raw["reason_code"]
        if reason_code is not None:
            reason_code = _safe_id(reason_code, "reason_code")
        events = _normalize_events(raw["normalized_events"])
        terminal = [event for event in events if event["kind"] == "terminal"]
        if classification in {
            ExecutionClassification.PENDING,
            ExecutionClassification.ENVIRONMENT_BLOCKED,
        }:
            if terminal:
                raise ResultViolation("pending or environment-blocked packets cannot contain a terminal")
        elif len(terminal) != 1:
            raise ResultViolation("completed executions require exactly one terminal event")
        if classification is ExecutionClassification.COMPLETE and terminal[0]["terminal_outcome"] != "completed":
            raise ResultViolation("COMPLETE requires a completed terminal")
        if classification is ExecutionClassification.EXPECTED_NEGATIVE and terminal[0]["terminal_outcome"] != "denied":
            raise ResultViolation("EXPECTED_NEGATIVE requires a denied terminal")
        if classification is ExecutionClassification.VERIFIED_FAILURE and terminal[0]["terminal_outcome"] not in {
            "failed",
            "cancelled",
        }:
            raise ResultViolation("VERIFIED_FAILURE requires a failed or cancelled terminal")

        primary = _hash(raw["primary_proof_hash"], "primary_proof_hash", optional=True)
        secondary = _hash(raw["secondary_proof_hash"], "secondary_proof_hash", optional=True)
        if classification in {
            ExecutionClassification.COMPLETE,
            ExecutionClassification.EXPECTED_NEGATIVE,
        } and (primary is None or secondary is None):
            raise ResultViolation("passing evidence requires primary and secondary proof hashes")
        trace_hash = _hash(raw["trace_hash"], "trace_hash")
        if trace_hash != sha256_value([dict(event) for event in events]):
            raise ResultViolation("trace_hash does not match normalized_events")
        catalog_hash = _hash(raw["catalog_hash"], "catalog_hash")
        profile_hash = _hash(raw["profile_hash"], "profile_hash")
        inventory_hash = _hash(raw["inventory_hash"], "inventory_hash")
        expected_candidate = candidate_hash(
            catalog_hash=catalog_hash,
            plugin_sha=_sha(raw["plugin_sha"], "plugin_sha"),
            host_sha=_sha(raw["host_sha"], "host_sha"),
            sdk_version=sdk_version,
            profile_hash=profile_hash,
            runner_version=runner_version,
            inventory_hash=inventory_hash,
        )
        packet_candidate = _hash(raw["candidate_hash"], "candidate_hash")
        if packet_candidate != expected_candidate:
            raise ResultViolation("candidate_hash does not match the bound candidate fields")
        packet_hash = _hash(raw["packet_hash"], "packet_hash")
        without_hash = dict(raw)
        without_hash.pop("packet_hash")
        if packet_hash != sha256_value(without_hash):
            raise ResultViolation("packet_hash does not match packet content")

        return cls(
            schema_version=1,
            capability_id=capability_id,
            source_pack=source_pack,
            lane=lane,
            path=path,
            execution_id=execution_id,
            classification=classification,
            contract_hash=_hash(raw["contract_hash"], "contract_hash"),
            catalog_hash=catalog_hash,
            plugin_sha=_sha(raw["plugin_sha"], "plugin_sha"),
            host_sha=_sha(raw["host_sha"], "host_sha"),
            sdk_version=sdk_version,
            profile_id=profile_id,
            profile_hash=profile_hash,
            runner_version=runner_version,
            inventory_hash=inventory_hash,
            candidate_hash=packet_candidate,
            billing_classification=billing,
            turn_count=turn_count,
            trial_index=trial_index,
            normalized_events=events,
            trace_hash=trace_hash,
            primary_proof_hash=primary,
            secondary_proof_hash=secondary,
            silent_fallback=False,
            invariant_violations=(),
            reason_code=reason_code,
            packet_hash=packet_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "capability_id": self.capability_id,
            "source_pack": self.source_pack,
            "lane": self.lane,
            "path": self.path,
            "execution_id": self.execution_id,
            "classification": self.classification.value,
            "contract_hash": self.contract_hash,
            "catalog_hash": self.catalog_hash,
            "plugin_sha": self.plugin_sha,
            "host_sha": self.host_sha,
            "sdk_version": self.sdk_version,
            "profile_id": self.profile_id,
            "profile_hash": self.profile_hash,
            "runner_version": self.runner_version,
            "inventory_hash": self.inventory_hash,
            "candidate_hash": self.candidate_hash,
            "billing_classification": self.billing_classification,
            "turn_count": self.turn_count,
            "trial_index": self.trial_index,
            "normalized_events": [dict(event) for event in self.normalized_events],
            "trace_hash": self.trace_hash,
            "primary_proof_hash": self.primary_proof_hash,
            "secondary_proof_hash": self.secondary_proof_hash,
            "silent_fallback": self.silent_fallback,
            "invariant_violations": list(self.invariant_violations),
            "reason_code": self.reason_code,
            "packet_hash": self.packet_hash,
        }

    def write(self, path: str | Path) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)


def read_result_packet(path: str | Path) -> ResultPacket:
    result_path = Path(path)
    if not result_path.is_file():
        raise ResultViolation(f"result packet is not a regular file: {result_path}")
    if result_path.stat().st_size > _MAX_PACKET_BYTES:
        raise ResultViolation("result packet exceeds the bounded file size")
    try:
        value = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResultViolation(f"result packet cannot be read safely: {exc}") from exc
    return ResultPacket.from_dict(value)


__all__ = [
    "ExecutionClassification",
    "ResultPacket",
    "ResultViolation",
    "candidate_hash",
    "read_result_packet",
]
