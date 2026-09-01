"""Strict, secret-free receipts for cross-stage Eva parity evidence."""

from __future__ import annotations

import json
import string
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .hashing import json_compatible, sha256_value


_MAX_RECEIPT_BYTES = 64 * 1024
_HEX = frozenset(string.hexdigits.lower())
_ROLLBACK_SOURCE_SHA = "98198a12bea152c055fbc4587d1016bcc5e5b618"
_LEAN_FIELDS = frozenset(
    {
        "schema_version",
        "candidate_hash",
        "contract_hash",
        "catalog_hash",
        "plugin_sha",
        "host_sha",
        "sdk_version",
        "profile_hash",
        "inventory_hash",
        "sample_count",
        "p95_fable_non_cache_share_ppm",
        "threshold_ppm",
        "max_fable_turns",
        "total_native_claude_children",
        "total_fable_input_tokens",
        "total_fable_output_tokens",
        "total_fable_cache_read_tokens",
        "total_fable_cache_write_tokens",
        "total_worker_input_tokens",
        "total_worker_output_tokens",
        "total_worker_cache_read_tokens",
        "total_worker_cache_write_tokens",
        "route_counts",
        "billing_modes",
        "fallback_count",
        "metered_count",
        "unknown_billing_count",
        "window_seconds",
        "passed",
        "status",
        "summary_hash",
    }
)
_SHARED_OPS_FIELDS = frozenset(
    {
        "schema_version",
        "candidate_hash",
        "contract_hash",
        "catalog_hash",
        "plugin_sha",
        "host_sha",
        "sdk_version",
        "profile_hash",
        "inventory_hash",
        "process_set_hash_before",
        "process_set_hash_after",
        "process_count",
        "stopped_count",
        "restarted_count",
        "gateway_code_sha",
        "service_health",
        "eva_provider_before",
        "eva_model_before",
        "eva_provider_after",
        "eva_model_after",
        "default_profile_hash_before",
        "default_profile_hash_after",
        "bsmoke_profile_hash_before",
        "bsmoke_profile_hash_after",
        "rollback_source_sha",
        "git_ref_resolves",
        "dependency_restore_dry_run",
        "profile_restore_dry_run",
        "service_restore_dry_run",
        "status",
        "receipt_hash",
    }
)


class ExternalReceiptError(ValueError):
    """A cross-stage receipt cannot support the claimed capability."""


def _bounded_int(value: Any, *, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= (1 << 63) - 1


def _hex_digest(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and set(value.lower()) <= _HEX
    )


def _load(path: Path, fields: frozenset[str]) -> dict[str, Any]:
    if (
        not path.is_file()
        or path.is_symlink()
        or path.stat().st_size > _MAX_RECEIPT_BYTES
    ):
        raise ExternalReceiptError("receipt path is unavailable")
    try:
        raw = json_compatible(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ExternalReceiptError("receipt is not bounded canonical JSON") from exc
    if not isinstance(raw, Mapping) or set(raw) != fields:
        raise ExternalReceiptError("receipt fields do not match the versioned schema")
    return dict(raw)


def _validate_candidate(
    raw: Mapping[str, Any],
    *,
    candidate_hash: str,
    contract_hash: str,
    catalog_hash: str,
    plugin_sha: str,
    host_sha: str,
    sdk_version: str,
    profile_hash: str,
    inventory_hash: str,
) -> None:
    expected = {
        "schema_version": 1,
        "candidate_hash": candidate_hash,
        "contract_hash": contract_hash,
        "catalog_hash": catalog_hash,
        "plugin_sha": plugin_sha,
        "host_sha": host_sha,
        "sdk_version": sdk_version,
        "profile_hash": profile_hash,
        "inventory_hash": inventory_hash,
    }
    if any(raw.get(key) != value for key, value in expected.items()):
        raise ExternalReceiptError("receipt candidate binding does not match")


def _validate_self_hash(raw: Mapping[str, Any], field: str) -> None:
    supplied = raw[field]
    if not _hex_digest(supplied, 64):
        raise ExternalReceiptError("receipt hash is malformed")
    payload = dict(raw)
    del payload[field]
    if supplied != sha256_value(payload):
        raise ExternalReceiptError("receipt hash does not match its payload")


def load_lean_orchestration_receipt(
    path: str | Path,
    **candidate: str,
) -> dict[str, Any]:
    """Validate the 100-job, 48-hour Fable-to-Hermes-worker aggregate."""

    supplied = Path(path).expanduser()
    if supplied.is_symlink():
        raise ExternalReceiptError("receipt path is unavailable")
    raw = _load(supplied.resolve(), _LEAN_FIELDS)
    _validate_candidate(raw, **candidate)
    _validate_self_hash(raw, "summary_hash")
    route_counts = raw["route_counts"]
    billing_modes = raw["billing_modes"]
    integer_fields = _LEAN_FIELDS - {
        "candidate_hash",
        "contract_hash",
        "catalog_hash",
        "plugin_sha",
        "host_sha",
        "sdk_version",
        "profile_hash",
        "inventory_hash",
        "route_counts",
        "billing_modes",
        "passed",
        "status",
        "summary_hash",
    }
    if not all(_bounded_int(raw[field]) for field in integer_fields):
        raise ExternalReceiptError("lean receipt contains an invalid aggregate")
    if (
        raw["sample_count"] != 100
        or raw["threshold_ppm"] != 250_000
        or raw["p95_fable_non_cache_share_ppm"] > raw["threshold_ppm"]
        or raw["max_fable_turns"] > 2
        or raw["total_native_claude_children"] != 0
        or raw["total_worker_input_tokens"] + raw["total_worker_output_tokens"] <= 0
        or raw["fallback_count"] != 0
        or raw["metered_count"] != 0
        or raw["unknown_billing_count"] != 0
        or raw["window_seconds"] < 48 * 60 * 60
        or raw["passed"] is not True
        or raw["status"] != "PASS"
        or not isinstance(route_counts, Mapping)
        or set(route_counts) != {"codex-luna", "codex-sol"}
        or not all(_bounded_int(value, minimum=1) for value in route_counts.values())
        or not isinstance(billing_modes, Mapping)
        or dict(billing_modes)
        != {
            "claude-agent-sdk": "subscription_included",
            "openai-codex": "subscription_included",
        }
    ):
        raise ExternalReceiptError("lean orchestration contract did not pass")
    return raw


def load_shared_operations_receipt(
    path: str | Path,
    **candidate: str,
) -> dict[str, Any]:
    """Validate sanitized shared-Eva cutover/default/rollback evidence."""

    supplied = Path(path).expanduser()
    if supplied.is_symlink():
        raise ExternalReceiptError("receipt path is unavailable")
    raw = _load(supplied.resolve(), _SHARED_OPS_FIELDS)
    _validate_candidate(raw, **candidate)
    _validate_self_hash(raw, "receipt_hash")
    if (
        not _hex_digest(raw["process_set_hash_before"], 64)
        or raw["process_set_hash_before"] != raw["process_set_hash_after"]
        or not _bounded_int(raw["process_count"], minimum=1)
        or raw["stopped_count"] != raw["process_count"]
        or raw["restarted_count"] != raw["process_count"]
        or raw["gateway_code_sha"] != raw["host_sha"]
        or raw["service_health"] != "healthy"
        or raw["eva_provider_before"] != "openai-codex"
        or raw["eva_provider_after"] != "openai-codex"
        or raw["eva_model_before"] != "gpt-5.6-sol"
        or raw["eva_model_after"] != "gpt-5.6-sol"
        or not _hex_digest(raw["default_profile_hash_before"], 64)
        or raw["default_profile_hash_before"] != raw["default_profile_hash_after"]
        or not _hex_digest(raw["bsmoke_profile_hash_before"], 64)
        or raw["bsmoke_profile_hash_before"] != raw["bsmoke_profile_hash_after"]
        or raw["rollback_source_sha"] != _ROLLBACK_SOURCE_SHA
        or any(
            raw[field] is not True
            for field in (
                "git_ref_resolves",
                "dependency_restore_dry_run",
                "profile_restore_dry_run",
                "service_restore_dry_run",
            )
        )
        or raw["status"] != "PASS"
    ):
        raise ExternalReceiptError("shared operations contract did not pass")
    return raw


__all__ = [
    "ExternalReceiptError",
    "load_lean_orchestration_receipt",
    "load_shared_operations_receipt",
]
