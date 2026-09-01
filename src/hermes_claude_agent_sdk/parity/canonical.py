"""Small, strict canonical JSON helpers used by the parity v3 contract.

The parity packet is an interchange format, rather than a Python object
dump.  Keeping canonicalisation here makes the hash boundary explicit and
keeps the contract and packet modules independent of the SDK and host.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Callable, Iterable


class CanonicalizationError(ValueError):
    """Raised when a value cannot safely become canonical JSON."""


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# The result trace deliberately has no open-ended ``code``/``actor`` values.
# Keeping this registry in the dependency-free canonical module lets catalog
# and packet validators share one source without importing either side.
TRACE_REGISTRY: dict[str, tuple[str, str]] = {
    "registration.accepted": ("runner", "preflight"),
    "selection.accepted": ("runner", "preflight"),
    "preflight.pass": ("plugin", "preflight"),
    "preflight.fail": ("plugin", "preflight"),
    "approval.requested": ("host", "before_output"),
    "approval.granted": ("host", "before_output"),
    "approval.denied": ("host", "before_output"),
    "approval.late_rejected": ("host", "before_output"),
    "tool.requested": ("plugin", "side_effects"),
    "tool.executed": ("plugin", "side_effects"),
    "tool.denied": ("plugin", "side_effects"),
    "tool.cancelled": ("plugin", "side_effects"),
    "tool.failed": ("plugin", "side_effects"),
    "tool.recovered": ("plugin", "side_effects"),
    "host.execute_tool": ("host", "side_effects"),
    "host.execute_tool_failed": ("host", "side_effects"),
    "state.bound": ("plugin", "lifecycle"),
    "state.invalid": ("plugin", "lifecycle"),
    "resume.supplied": ("plugin", "lifecycle"),
    "resume.accepted": ("plugin", "lifecycle"),
    "resume.rejected": ("plugin", "lifecycle"),
    "session.opened": ("plugin", "lifecycle"),
    "session.restarted": ("plugin", "lifecycle"),
    "session.isolated": ("plugin", "lifecycle"),
    "compaction.started": ("plugin", "lifecycle"),
    "compaction.completed": ("plugin", "lifecycle"),
    "compaction.failed": ("plugin", "lifecycle"),
    "compaction.watchdog": ("plugin", "lifecycle"),
    "usage.included": ("plugin", "lifecycle"),
    "usage.blocked": ("plugin", "lifecycle"),
    "usage.unknown": ("plugin", "lifecycle"),
    "sdk.precompact": ("sdk", "lifecycle"),
    "sdk.compact_boundary": ("sdk", "lifecycle"),
    "sdk.result": ("sdk", "lifecycle"),
    "sdk.tool_use": ("sdk", "side_effects"),
    "sdk.tool_result": ("sdk", "side_effects"),
    "sdk.resume": ("sdk", "lifecycle"),
    "sdk.query": ("sdk", "lifecycle"),
    "path.positive.begin": ("runner", "lifecycle"),
    "path.positive.end": ("runner", "lifecycle"),
    "path.denial.begin": ("runner", "lifecycle"),
    "path.denial.end": ("runner", "lifecycle"),
    "path.recovery.begin": ("runner", "lifecycle"),
    "path.recovery.end": ("runner", "lifecycle"),
    "terminal.complete": ("plugin", "terminal"),
    "terminal.cancelled": ("plugin", "terminal"),
    "terminal.failed": ("plugin", "terminal"),
    "recovery.started": ("host", "side_effects"),
    "recovery.completed": ("host", "side_effects"),
    "recovery.failed": ("host", "side_effects"),
    "inventory.exact": ("runner", "preflight"),
    "inventory.drift": ("runner", "preflight"),
    "package.imported": ("runner", "lifecycle"),
    "package.uninstalled": ("runner", "lifecycle"),
    "package.reinstalled": ("runner", "lifecycle"),
    "ledger.covered": ("runner", "lifecycle"),
    "ledger.equivalent": ("runner", "lifecycle"),
    "ledger.upgrade_required": ("runner", "lifecycle"),
    "ledger.not_applicable": ("runner", "lifecycle"),
}
SDK_EVENT_CODES: dict[str, str] = {
    "ClaudeSDKClient.query": "sdk.query",
    "PreCompact": "sdk.precompact",
    "SystemMessage.compact_boundary": "sdk.compact_boundary",
    "ResultMessage": "sdk.result",
    "ToolUseBlock": "sdk.tool_use",
    "ToolResultBlock": "sdk.tool_result",
    "ClaudeSDKClient.resume": "sdk.resume",
    "HostToolBridge.handler": "host.execute_tool",
}

# Catalog references and packet metadata are intentionally bounded.  These
# limits are not a serialization substitute; they stop accidental inclusion
# of a transcript, exception, or other unbounded value before hashing.
MAX_STRING_LENGTH = 4096
MAX_COLLECTION_LENGTH = 4096


def _is_control_bearing(value: str) -> bool:
    return _CONTROL_RE.search(value) is not None


def validate_identifier(value: Any, *, field: str = "identifier", max_length: int = 128) -> str:
    """Validate a catalog-owned identifier and return it unchanged.

    Identifiers are intentionally not normalised: case and punctuation are
    part of the frozen catalog identity.  Whitespace, controls, and empty
    values are rejected so they cannot be confused with output formatting.
    """

    if not isinstance(value, str) or isinstance(value, bool):
        raise CanonicalizationError(f"{field} must be a string")
    if not value or len(value.encode("utf-8")) > min(max_length, 128) or value != value.strip():
        raise CanonicalizationError(f"{field} is not a bounded identifier")
    if not value.isascii() or re.fullmatch(r"[A-Za-z0-9_.:-]+", value) is None:
        raise CanonicalizationError(f"{field} has an invalid grammar")
    if _is_control_bearing(value):
        raise CanonicalizationError(f"{field} contains a control character")
    return value


def validate_sha256(value: Any, *, field: str = "sha256") -> str:
    """Validate a lowercase SHA-256 hex digest."""

    if not isinstance(value, str) or _HEX64_RE.fullmatch(value) is None:
        raise CanonicalizationError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _canonical_value(value: Any, *, path: str = "$", max_depth: int = 64) -> Any:
    """Return a JSON-only copy, rejecting ambiguous or unsafe Python values."""

    if max_depth < 0:
        raise CanonicalizationError(f"{path} exceeds maximum nesting depth")
    if value is None or isinstance(value, (bool, int, str)):
        if isinstance(value, str):
            value = unicodedata.normalize("NFC", value)
            if len(value) > MAX_STRING_LENGTH:
                raise CanonicalizationError(f"{path} contains an oversized string")
            # JSON permits controls, but parity values do not need them and
            # rejecting them prevents control-bearing IDs from entering a
            # supposedly safe packet through a generic mapping.
            if _is_control_bearing(value):
                raise CanonicalizationError(f"{path} contains a control character")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        if len(value) > MAX_COLLECTION_LENGTH:
            raise CanonicalizationError(f"{path} contains too many object fields")
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or isinstance(key, bool):
                raise CanonicalizationError(f"{path} contains a non-string object key")
            if len(key) > MAX_STRING_LENGTH or _is_control_bearing(key):
                raise CanonicalizationError(f"{path} contains an unsafe object key")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in result:
                # A native dict cannot contain this state, but custom Mapping
                # implementations can expose duplicate keys.
                raise CanonicalizationError(f"{path} contains duplicate object keys")
            result[normalized_key] = _canonical_value(
                item, path=f"{path}.{key}", max_depth=max_depth - 1
            )
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_COLLECTION_LENGTH:
            raise CanonicalizationError(f"{path} contains too many array items")
        return [
            _canonical_value(item, path=f"{path}[{index}]", max_depth=max_depth - 1)
            for index, item in enumerate(value)
        ]
    raise CanonicalizationError(f"{path} contains an unsupported value")


def canonicalize(value: Any) -> Any:
    """Return a deep JSON-safe canonical value.

    Object key ordering is handled by the encoder.  Array order is preserved;
    callers with set semantics must sort their projection before calling this
    function.
    """

    return _canonical_value(value)


def canonical_json_bytes(value: Any, *, omit_keys: Iterable[str] = ()) -> bytes:
    """Encode JSON with the v3 deterministic UTF-8 settings.

    ``omit_keys`` applies only to the root object.  Hash fields nested inside
    a capability or inventory entry are data and must not silently disappear.
    """

    safe = canonicalize(value)
    omissions = tuple(omit_keys)
    if omissions:
        if not isinstance(safe, dict):
            raise CanonicalizationError("hash-field omission requires a root object")
        for key in omissions:
            validate_identifier(key, field="omitted key")
        safe = {key: item for key, item in safe.items() if key not in omissions}
    try:
        return json.dumps(
            safe,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:  # pragma: no cover - defensive
        raise CanonicalizationError("value cannot be encoded as canonical JSON") from exc


def canonical_json(value: Any, *, omit_keys: Iterable[str] = ()) -> str:
    """Return deterministic canonical JSON text."""

    return canonical_json_bytes(value, omit_keys=omit_keys).decode("utf-8")


def canonical_sha256(value: Any, *, omit_keys: Iterable[str] = ()) -> str:
    """Return a lowercase SHA-256 digest of canonical UTF-8 JSON."""

    return hashlib.sha256(canonical_json_bytes(value, omit_keys=omit_keys)).hexdigest()


def load_json(data: str | bytes, *, source: str = "JSON") -> Any:
    """Load JSON while rejecting duplicate keys, NaN, and Infinity."""

    if not isinstance(data, (str, bytes)):
        raise CanonicalizationError(f"{source} must be text or bytes")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CanonicalizationError(f"{source} contains duplicate object keys")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise CanonicalizationError(f"{source} contains non-JSON number {value}")

    try:
        loaded = json.loads(
            data,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except CanonicalizationError:
        raise
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise CanonicalizationError(f"{source} is not valid JSON") from exc
    return canonicalize(loaded)


def hash_tool_call_sequence(records: Sequence[Mapping[str, Any]]) -> str:
    """Hash a bounded normalized tool-call sequence without raw arguments.

    Every retained call is independently attributable to a declared schema;
    raw request IDs are never accepted, only their SHA-256 digests.
    """

    normalized: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            raise CanonicalizationError(f"tool call {index} is not an object")
        if set(record) != {"ordinal", "name", "schema_sha256", "outcome", "request_id_sha256"}:
            raise CanonicalizationError(f"tool call {index} has unexpected fields")
        if record["ordinal"] != index or isinstance(record["ordinal"], bool):
            raise CanonicalizationError(f"tool call {index} has invalid ordinal")
        name = validate_identifier(record["name"], field="tool name")
        outcome = validate_identifier(record["outcome"], field="tool outcome")
        schema_digest = validate_sha256(record["schema_sha256"], field="tool schema hash")
        request_digest = validate_sha256(record["request_id_sha256"], field="request id hash")
        normalized.append(
            {
                "ordinal": index,
                "name": name,
                "schema_sha256": schema_digest,
                "outcome": outcome,
                "request_id_sha256": request_digest,
            }
        )
    return canonical_sha256(normalized)


# Compatibility names for callers that prefer the design's wording.
stable_json_hash = canonical_sha256
parse_json = load_json


__all__ = [
    "CanonicalizationError",
    "MAX_COLLECTION_LENGTH",
    "MAX_STRING_LENGTH",
    "SDK_EVENT_CODES",
    "TRACE_REGISTRY",
    "canonical_json",
    "canonical_json_bytes",
    "canonical_sha256",
    "canonicalize",
    "hash_tool_call_sequence",
    "load_json",
    "parse_json",
    "stable_json_hash",
    "validate_identifier",
    "validate_sha256",
]
