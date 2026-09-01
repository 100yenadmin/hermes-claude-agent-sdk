from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.external_receipts import (
    ExternalReceiptError,
    load_lean_orchestration_receipt,
    load_shared_operations_receipt,
)
from hermes_claude_agent_sdk.parity.hashing import sha256_value


_CANDIDATE = {
    "candidate_hash": "1" * 64,
    "contract_hash": "2" * 64,
    "catalog_hash": "3" * 64,
    "plugin_sha": "4" * 40,
    "host_sha": "5" * 40,
    "sdk_version": "0.2.144",
    "profile_hash": "6" * 64,
    "inventory_hash": "7" * 64,
}
_ENV_CANDIDATE_FIELDS = {
    "candidate_hash": "HERMES_PARITY_CANDIDATE_HASH",
    "contract_hash": "HERMES_PARITY_CONTRACT_HASH",
    "catalog_hash": "HERMES_PARITY_CATALOG_HASH",
    "plugin_sha": "HERMES_PARITY_PLUGIN_SHA",
    "host_sha": "HERMES_AGENT_HOST_SHA",
    "sdk_version": "HERMES_PARITY_SDK_VERSION",
    "profile_hash": "HERMES_PARITY_PROFILE_HASH",
    "inventory_hash": "HERMES_PARITY_INVENTORY_HASH",
}


def _write(path: Path, payload: dict, hash_field: str) -> Path:
    payload[hash_field] = sha256_value(payload)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _lean_payload() -> dict:
    return {
        "schema_version": 1,
        **_CANDIDATE,
        "sample_count": 100,
        "p95_fable_non_cache_share_ppm": 200_000,
        "threshold_ppm": 250_000,
        "max_fable_turns": 2,
        "total_native_claude_children": 0,
        "total_fable_input_tokens": 1_000,
        "total_fable_output_tokens": 200,
        "total_fable_cache_read_tokens": 8_000,
        "total_fable_cache_write_tokens": 500,
        "total_worker_input_tokens": 4_000,
        "total_worker_output_tokens": 800,
        "total_worker_cache_read_tokens": 1_100,
        "total_worker_cache_write_tokens": 300,
        "route_counts": {"codex-luna": 50, "codex-sol": 50},
        "billing_modes": {
            "claude-agent-sdk": "subscription_included",
            "openai-codex": "subscription_included",
        },
        "fallback_count": 0,
        "metered_count": 0,
        "unknown_billing_count": 0,
        "window_seconds": 172_800,
        "passed": True,
        "status": "PASS",
    }


def _shared_ops_payload() -> dict:
    return {
        "schema_version": 1,
        **_CANDIDATE,
        "process_set_hash_before": "8" * 64,
        "process_set_hash_after": "8" * 64,
        "process_count": 3,
        "stopped_count": 3,
        "restarted_count": 3,
        "gateway_code_sha": _CANDIDATE["host_sha"],
        "service_health": "healthy",
        "eva_provider_before": "openai-codex",
        "eva_model_before": "gpt-5.6-sol",
        "eva_provider_after": "openai-codex",
        "eva_model_after": "gpt-5.6-sol",
        "default_profile_hash_before": "9" * 64,
        "default_profile_hash_after": "9" * 64,
        "bsmoke_profile_hash_before": "a" * 64,
        "bsmoke_profile_hash_after": "a" * 64,
        "rollback_source_sha": "98198a12bea152c055fbc4587d1016bcc5e5b618",
        "git_ref_resolves": True,
        "dependency_restore_dry_run": True,
        "profile_restore_dry_run": True,
        "service_restore_dry_run": True,
        "status": "PASS",
    }


def _candidate_from_environment() -> dict[str, str]:
    values = {key: os.environ.get(env_name, "") for key, env_name in _ENV_CANDIDATE_FIELDS.items()}
    if not all(values.values()):
        pytest.skip("cross-stage candidate binding is not configured")
    return values


def test_valid_lean_orchestration_receipt_is_exact_and_safe(tmp_path: Path) -> None:
    path = _write(tmp_path / "lean.json", _lean_payload(), "summary_hash")

    receipt = load_lean_orchestration_receipt(path, **_CANDIDATE)

    assert receipt["sample_count"] == 100
    assert receipt["total_native_claude_children"] == 0
    assert receipt["route_counts"] == {"codex-luna": 50, "codex-sol": 50}
    assert not any("prompt" in key or "session" in key for key in receipt)


def test_lean_receipt_rejects_overuse_missing_attribution_and_short_window(
    tmp_path: Path,
) -> None:
    for index, update in enumerate(
        (
            {"total_native_claude_children": 1},
            {"max_fable_turns": 3},
            {"total_worker_input_tokens": 0, "total_worker_output_tokens": 0},
            {"window_seconds": 172_799},
            {"unknown_billing_count": 1},
        )
    ):
        payload = _lean_payload()
        payload.update(update)
        path = _write(tmp_path / f"lean-bad-{index}.json", payload, "summary_hash")
        with pytest.raises(ExternalReceiptError, match="did not pass"):
            load_lean_orchestration_receipt(path, **_CANDIDATE)


def test_valid_shared_operations_receipt_covers_all_restore_components(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path / "ops.json", _shared_ops_payload(), "receipt_hash")

    receipt = load_shared_operations_receipt(path, **_CANDIDATE)

    assert receipt["stopped_count"] == receipt["restarted_count"] == 3
    assert receipt["eva_provider_after"] == "openai-codex"
    assert receipt["eva_model_after"] == "gpt-5.6-sol"


def test_shared_operations_receipt_rejects_partial_restore_or_default_drift(
    tmp_path: Path,
) -> None:
    for index, update in enumerate(
        (
            {"restarted_count": 2},
            {"eva_model_after": "claude-fable-5"},
            {"dependency_restore_dry_run": False},
            {"process_set_hash_after": "b" * 64},
        )
    ):
        payload = _shared_ops_payload()
        payload.update(update)
        path = _write(tmp_path / f"ops-bad-{index}.json", payload, "receipt_hash")
        with pytest.raises(ExternalReceiptError, match="did not pass"):
            load_shared_operations_receipt(path, **_CANDIDATE)


def test_external_lean_receipt_from_environment() -> None:
    path = os.environ.get("HERMES_PARITY_LEAN_RECEIPT")
    if not path:
        pytest.skip("lean orchestration receipt is not configured")
    receipt = load_lean_orchestration_receipt(path, **_candidate_from_environment())
    assert receipt["status"] == "PASS"


def test_external_shared_operations_receipt_from_environment() -> None:
    path = os.environ.get("HERMES_PARITY_SHARED_OPS_RECEIPT")
    if not path:
        pytest.skip("shared operations receipt is not configured")
    receipt = load_shared_operations_receipt(path, **_candidate_from_environment())
    assert receipt["status"] == "PASS"
