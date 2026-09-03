"""Strict, provider-free Revision-4 Phase-A release receipt validation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

V4_RECEIPT_SCHEMA_VERSION = 4
V4_ISSUE = 9
V4_SDK_DISTRIBUTION = "claude-agent-sdk"
V4_SDK_VERSION = "0.2.151"
V4_CLI_VERSION = "2.1.258"
V4_MODEL = "claude-fable-5-1"
V4_CONTRACT_SHA256 = "53864834496403388f3475291475fea70acfa3105609ad49f5edf75ad1c67d94"
V4_LIVE_MAP_SHA256 = "85583a44b797a58e6a3f6fcc9f4f5234b445b49c5ab6bf38b153e872473a16ff"
V4_PROOF_BOUNDARY = "Phase-A evidence receipt includes exact local provider-live runtime-safe evidence only; no merge, tag, release, publication, fleet, or customer proof."
OWNERSHIP_PREFLIGHTS = (
    "zero_native_absence", "exact_prompt_settings_tools_mcp", "no_native_events_projector",
    "delegate_owner", "background_owner", "canonical_transcript_content", "streaming_owner",
    "redaction_fail_closed",
)

_ROOT = frozenset({
    "schema_version", "issue", "status", "phase", "publication_authorized",
    "merge_performed", "tag_created", "release_created", "artifact_immutable", "plugin_sha",
    "host_sha", "sdk_distribution", "sdk_version", "cli_version", "model", "wheel_sha256",
    "sdist_sha256", "profile_sha256", "contract_sha256", "map_sha256", "artifact_sha256",
    "ownership_preflights", "parity", "ci", "semantic_checkers", "package_lifecycle",
    "installed_subscription_gate", "parent_calls", "child_calls", "total_calls", "reserve_calls", "direct_sdk_calls", "alternate_route_calls", "proof_boundary",
})
_PARITY = frozenset({
    "required_paths", "observed_paths", "required_trial_packets", "observed_trial_packets",
    "complete_paths", "failed_paths", "pending_paths", "partial_paths", "not_run_paths",
    "environment_blocked_paths",
})
_CI = frozenset({"status", "head_sha"})
_CHECKER = frozenset({"blind", "status", "score"})
_LIFECYCLE = frozenset({"install", "uninstall", "reinstall", "rollback"})
_INSTALLED = frozenset({
    "status", "billing_classification", "silent_fallback", "native_tools", "native_settings",
    "native_preset", "native_agent_events",
})
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_KEYS = frozenset({
    "raw", "raw_prompt", "raw_content", "raw_transcript", "prompt", "content", "transcript",
    "message", "messages", "secret", "credential", "credentials", "password", "token", "cookie",
    "cookies", "session", "session_id", "auth_material", "api_key", "access_token", "refresh_token",
})


class V4ReleaseReadyViolation(ValueError):
    """A Revision-4 receipt is malformed, incomplete, or unsafe."""


V4ReleaseReadyError = V4ReleaseReadyViolation


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise V4ReleaseReadyViolation(f"{field} must be an object")
    return dict(value)


def _closed(value: Mapping[str, Any], fields: frozenset[str], field: str) -> None:
    if set(value) != fields:
        raise V4ReleaseReadyViolation(f"{field} has unknown or missing fields")


def _reject_raw(value: Any, field: str = "receipt") -> None:
    """Reject raw/secret-shaped keys before any receipt can be persisted."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = key.casefold().replace("-", "_") if isinstance(key, str) else ""
            if (not isinstance(key, str) or normalized in _FORBIDDEN_KEYS
                    or normalized.startswith(("raw_", "secret_", "credential_", "session_"))
                    or normalized.endswith(("_raw", "_secret", "_credential"))):
                raise V4ReleaseReadyViolation(f"{field} contains a forbidden field")
            _reject_raw(child, f"{field}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_raw(child, f"{field}[{index}]")


def _digest(value: Any, field: str, length: int) -> str:
    pattern = _HEX40 if length == 40 else _HEX64
    if not isinstance(value, str) or pattern.fullmatch(value) is None or value == "0" * length:
        raise V4ReleaseReadyViolation(f"{field} must be a nonzero lowercase SHA-{length * 4}")
    return value


def _count(value: Any, field: str, expected: int) -> None:
    if type(value) is not int or value != expected:
        raise V4ReleaseReadyViolation(f"{field} must equal {expected}")


def _validate_preflights(value: Any) -> dict[str, str]:
    preflights = _object(value, "ownership_preflights")
    expected = frozenset(OWNERSHIP_PREFLIGHTS)
    _closed(preflights, expected, "ownership_preflights")
    if any(preflights[name] != "PASS" for name in OWNERSHIP_PREFLIGHTS):
        raise V4ReleaseReadyViolation("every ownership preflight must be PASS")
    return {name: "PASS" for name in OWNERSHIP_PREFLIGHTS}


def _validate_parity(value: Any) -> dict[str, int]:
    parity = _object(value, "parity")
    _closed(parity, _PARITY, "parity")
    for field, expected in (
        ("required_paths", 220), ("observed_paths", 220), ("required_trial_packets", 390),
        ("observed_trial_packets", 390), ("complete_paths", 220), ("failed_paths", 0),
        ("pending_paths", 0), ("partial_paths", 0), ("not_run_paths", 0),
        ("environment_blocked_paths", 0),
    ):
        _count(parity[field], f"parity.{field}", expected)
    return {field: parity[field] for field in _PARITY}


def _validate_ci(value: Any, plugin_sha: str, host_sha: str) -> dict[str, dict[str, str]]:
    ci = _object(value, "ci")
    _closed(ci, frozenset({"plugin", "host"}), "ci")
    result: dict[str, dict[str, str]] = {}
    for name, expected_sha in (("plugin", plugin_sha), ("host", host_sha)):
        item = _object(ci[name], f"ci.{name}")
        _closed(item, _CI, f"ci.{name}")
        if item["status"] != "success" or _digest(item["head_sha"], f"ci.{name}.head_sha", 40) != expected_sha:
            raise V4ReleaseReadyViolation(f"ci.{name} is not successful at the candidate head")
        result[name] = {"status": "success", "head_sha": expected_sha}
    return result


def _validate_checkers(value: Any) -> dict[str, dict[str, Any]]:
    checkers = _object(value, "semantic_checkers")
    _closed(checkers, frozenset({"checker_a", "checker_b"}), "semantic_checkers")
    result: dict[str, dict[str, Any]] = {}
    for name in ("checker_a", "checker_b"):
        item = _object(checkers[name], f"semantic_checkers.{name}")
        _closed(item, _CHECKER, f"semantic_checkers.{name}")
        if item["blind"] is not True or item["status"] != "PASS" or type(item["score"]) is not int or not 95 <= item["score"] <= 100:
            raise V4ReleaseReadyViolation("both blind semantic checkers must PASS at a score of at least 95")
        result[name] = {"blind": True, "status": "PASS", "score": item["score"]}
    return result


def _validate_lifecycle(value: Any) -> dict[str, str]:
    lifecycle = _object(value, "package_lifecycle")
    _closed(lifecycle, _LIFECYCLE, "package_lifecycle")
    if any(lifecycle[name] != "PASS" for name in _LIFECYCLE):
        raise V4ReleaseReadyViolation("install, uninstall, reinstall, and rollback must all PASS")
    return {name: "PASS" for name in ("install", "uninstall", "reinstall", "rollback")}


def _validate_installed(value: Any) -> dict[str, Any]:
    installed = _object(value, "installed_subscription_gate")
    _closed(installed, _INSTALLED, "installed_subscription_gate")
    expected = {
        "status": "PASS", "billing_classification": "subscription_included", "silent_fallback": False,
        "native_tools": False, "native_settings": False, "native_preset": False,
        "native_agent_events": False,
    }
    if installed != expected:
        raise V4ReleaseReadyViolation("installed thin subscription gate is not an exact safe PASS")
    return dict(expected)


def validate_v4_release_ready(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a complete Phase-A receipt without executing any external call."""

    receipt = _object(value, "receipt")
    _reject_raw(receipt)
    _closed(receipt, _ROOT, "receipt")
    constants = {
        "schema_version": V4_RECEIPT_SCHEMA_VERSION,
        "issue": V4_ISSUE, "status": "release_ready", "phase": "A",
        "publication_authorized": False, "merge_performed": False, "tag_created": False,
        "release_created": False, "artifact_immutable": True, "sdk_distribution": V4_SDK_DISTRIBUTION,
        "sdk_version": V4_SDK_VERSION, "cli_version": V4_CLI_VERSION, "model": V4_MODEL,
        "parent_calls": 120, "child_calls": 16, "total_calls": 136, "reserve_calls": 44, "direct_sdk_calls": 0, "alternate_route_calls": 0, "proof_boundary": V4_PROOF_BOUNDARY,
    }
    if any(type(receipt[field]) is not type(expected) or receipt[field] != expected for field, expected in constants.items()):
        raise V4ReleaseReadyViolation("receipt identity, Phase-A barrier, or proof boundary is invalid")
    plugin_sha = _digest(receipt["plugin_sha"], "plugin_sha", 40)
    host_sha = _digest(receipt["host_sha"], "host_sha", 40)
    for field in ("wheel_sha256", "sdist_sha256", "profile_sha256", "artifact_sha256"):
        _digest(receipt[field], field, 64)
    if receipt["contract_sha256"] != V4_CONTRACT_SHA256:
        raise V4ReleaseReadyViolation("contract_sha256 does not bind immutable v4")
    if receipt["map_sha256"] != V4_LIVE_MAP_SHA256:
        raise V4ReleaseReadyViolation("map_sha256 does not bind the corrected provider-live map")
    normalized = dict(constants)
    normalized.update({
        "plugin_sha": plugin_sha, "host_sha": host_sha,
        "wheel_sha256": receipt["wheel_sha256"], "sdist_sha256": receipt["sdist_sha256"],
        "profile_sha256": receipt["profile_sha256"], "contract_sha256": receipt["contract_sha256"],
        "map_sha256": receipt["map_sha256"], "artifact_sha256": receipt["artifact_sha256"],
        "ownership_preflights": _validate_preflights(receipt["ownership_preflights"]),
        "parity": _validate_parity(receipt["parity"]), "ci": _validate_ci(receipt["ci"], plugin_sha, host_sha),
        "semantic_checkers": _validate_checkers(receipt["semantic_checkers"]),
        "package_lifecycle": _validate_lifecycle(receipt["package_lifecycle"]),
        "installed_subscription_gate": _validate_installed(receipt["installed_subscription_gate"]),
    })
    return normalized


def load_v4_release_ready(path: str | Path) -> dict[str, Any]:
    """Load and validate one bounded JSON receipt."""

    unresolved = Path(path).expanduser()
    if unresolved.is_symlink():
        raise V4ReleaseReadyViolation("receipt is not a bounded regular file")
    source = unresolved.resolve()
    if not source.is_file() or source.stat().st_size > 64 * 1024:
        raise V4ReleaseReadyViolation("receipt is not a bounded regular file")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V4ReleaseReadyViolation("receipt cannot be read as JSON") from exc
    return validate_v4_release_ready(value)


def write_v4_release_ready(value: Mapping[str, Any], output: str | Path) -> Path:
    """Validate and create a receipt, refusing to replace an existing artifact."""

    normalized = validate_v4_release_ready(value)
    destination = Path(output).expanduser()
    if destination.exists() or destination.is_symlink():
        raise V4ReleaseReadyViolation("refusing to replace a pre-existing receipt")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(normalized, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
    except (FileExistsError, OSError, TypeError, ValueError) as exc:
        raise V4ReleaseReadyViolation("receipt could not be persisted create-only") from exc
    return destination


validate_release_ready_receipt_v4 = validate_v4_release_ready
load_release_ready_receipt_v4 = load_v4_release_ready
write_release_ready_receipt_v4 = write_v4_release_ready
validate_v4_release_ready_receipt = validate_v4_release_ready
load_v4_release_ready_receipt = load_v4_release_ready
write_v4_release_ready_receipt = write_v4_release_ready

__all__ = [
    "OWNERSHIP_PREFLIGHTS", "V4ReleaseReadyError", "V4ReleaseReadyViolation", "load_v4_release_ready",
    "load_v4_release_ready_receipt", "load_release_ready_receipt_v4", "validate_release_ready_receipt_v4",
    "validate_v4_release_ready", "validate_v4_release_ready_receipt", "write_release_ready_receipt_v4",
    "write_v4_release_ready", "write_v4_release_ready_receipt",
]
