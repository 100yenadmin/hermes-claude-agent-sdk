from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from hermes_claude_agent_sdk.parity.canonical import TRACE_REGISTRY, canonical_sha256
from hermes_claude_agent_sdk.parity.packets import (
    OBSERVED,
    PacketValidationError,
    REDACTION_CATEGORIES,
    derive_path_qualification,
    hash_projection,
    validate_aggregate,
    validate_freeze,
    validate_path_status,
    validate_redaction,
    validate_result,
    validate_sdk_ledger,
)

D = "a" * 64
S1 = "b" * 40
S2 = "c" * 40


def _state(lifecycle: str = "fresh") -> dict[str, Any]:
    return {
        "lifecycle": lifecycle,
        "approval": "not_required",
        "tool": "none",
        "resume": "absent",
        "billing": "not_applicable",
        "side_effect_count": 0,
        "boundary_sha256": None,
    }


def _path(expected: str, *, required: bool = True) -> dict[str, Any]:
    terminal = {"kind": "complete", "count": 1} if expected == "PASS" else {"kind": "failed", "count": 1}
    if not required:
        terminal = {"kind": "not_applicable", "count": 0}
    return {
        "required": required,
        "expected_outcome": expected if required else "NOT_APPLICABLE",
        "trace_codes": [],
        "terminal": terminal,
        "tool_calls": [],
        "side_effect_count": 0,
        "sdk_events": [],
        "state_before": _state(),
        "state_after": _state("completed" if required else "closed"),
    }


def _path_status(status: str, expected: str) -> dict[str, Any]:
    terminal_kind = {
        "PASS": "complete",
        "EXPECTED_NEGATIVE": "failed",
        "VERIFIED_FAILURE": "failed",
        "ENVIRONMENT_BLOCKED": "not_applicable",
        "PENDING": "not_applicable",
        "NOT_APPLICABLE": "not_applicable",
    }[status]
    count = 0 if terminal_kind == "not_applicable" else 1
    return {
        "status": status,
        "observed_trace_codes": [] if count == 0 else [f"terminal.{terminal_kind}"],
        "terminal": {"kind": terminal_kind, "count": count},
        "qualification": derive_path_qualification(expected, status),
    }


def _candidate() -> dict[str, Any]:
    value = {
        "candidate_schema_version": 1,
        "plugin_sha": S1,
        "host_sha": S2,
        "wheel_sha256": D,
        "sdk_distribution": "claude-agent-sdk",
        "sdk_version": "0.2.144",
        "profile_sha256": D,
        "runner_id": "hermes-parity-v3",
        "runner_version": "1.0.0",
    }
    return value | {"candidate_sha256": canonical_sha256({key: value[key] for key in value if key != "candidate_schema_version"})}


def _inventory(candidate_sha256: str) -> dict[str, Any]:
    value = {
        "candidate_sha256": candidate_sha256,
        "declared_inventory_sha256": D,
        "tools": [],
        "mcp_servers": [],
        "unknown_names": [],
        "missing_names": [],
        "schema_drift_names": [],
    }
    return value | {"observed_inventory_sha256": canonical_sha256({key: value[key] for key in ("candidate_sha256", "tools", "mcp_servers")})}


def _grade(status: str = "PASS", *, verified_failure_count: int = 0, blocked: int = 0, pending: int = 0, expected_negative_count: int = 1) -> dict[str, Any]:
    return {
        "status": status,
        "cell_qualifications": {},
        "required_cell_count": 1,
        "observed_cell_count": 1,
        "required_pass_count": 2,
        "observed_pass_count": 2,
        "expected_negative_count": expected_negative_count,
        "verified_failure_count": verified_failure_count,
        "environment_blocked_count": blocked,
        "pending_count": pending,
        "pass_caret_3": {},
        "pass_at_3": {},
        "candidate_consistent": True,
        "terminal_events_exact": True,
        "inventory_exact": True,
        "billing_safe": True,
        "resume_safe": True,
        "isolation_safe": True,
        "sdk_ledger": {"ledger_sha256": D, "row_count": 23, "requires_0_3_239_rows": [], "upgrade_issue_ref": "issue:16", "status": "CLEAR"},
        "result_bijection": True,
    }


def _result(*, statuses: tuple[str, str, str] = ("PASS", "EXPECTED_NEGATIVE", "PASS"), grade: dict[str, Any] | None = None) -> dict[str, Any]:
    candidate = _candidate()
    paths = {name: _path(expected) for name, expected in zip(("positive", "denial", "recovery"), ("PASS", "EXPECTED_NEGATIVE", "PASS"))}
    attempt = {
        "ordinal": 1,
        "candidate_sha256": candidate["candidate_sha256"],
        "execution_ref_sha256": D,
        "boundary_sha256": D,
        "fresh_boundary": True,
        "trace": [
            {"seq": 1, "code": "terminal.complete", "actor": "plugin", "phase": "terminal", "correlation_sha256": None},
            {"seq": 2, "code": "terminal.failed", "actor": "plugin", "phase": "terminal", "correlation_sha256": None},
            {"seq": 3, "code": "terminal.complete", "actor": "plugin", "phase": "terminal", "correlation_sha256": None},
        ],
        "paths": paths,
        "path_status": {name: _path_status(status, paths[name]["expected_outcome"]) for name, status in zip(paths, statuses)},
        "resume": {"present": False, "runtime_id": "hermes-claude-agent-sdk", "runtime_schema_version": 1, "state_length": 0, "prior_state_sha256": None, "supplied_state_sha256": None, "produced_state_sha256": None, "accepted": False, "fixture_ref": "fixture:none"},
        "billing": {"mode": "subscription_included", "status": "included", "safe": True},
    }
    cell = {"capability_id": "CAP-one", "scenario_id": "SCN-one", "source_rows": [{"pack_id": "v2_non_soak", "row_id": "row1"}], "session_scope": "isolated_cell", "attempts": [attempt], "required": True}
    result = {
        "result_schema_version": "3.0.0", "freeze_sha256": D, "contract_sha256": D, "catalog_sha256": D,
        "source_map_sha256": D, "fixture_manifest_sha256": D, "candidate": candidate,
        "inventory": _inventory(candidate["candidate_sha256"]),
        "run": {"run_id_sha256": D, "scenario_sha256": D, "mode": "deterministic", "scope_partition_id": "PART-one", "session_scope": "isolated_cell", "candidate_unchanged": True, "cell_count": 1, "required_cell_count": 1, "terminal_event_count": 3, "resume_boundary": None},
        "cells": [cell], "grade": grade or _grade(), "redaction": {"profile": "v3-safe", "forbidden_field_count": 0, "omitted_categories": ["auth"], "secret_scan": "PASS"},
    }
    return result | {"result_sha256": canonical_sha256(result)}


def _freeze() -> dict[str, Any]:
    candidate = _candidate()
    value = {"freeze_schema_version": 1, "contract_id": "hermes-agent-sdk-feature-parity", "contract_sha256": D, "catalog_sha256": D, "source_map_sha256": D, "receipt_artifact_sha256": D, "replacement_receipt_sha256": D, "fixture_manifest_sha256": D, "candidate_sha256": candidate["candidate_sha256"], "declared_inventory_sha256": D, "observed_inventory_sha256": _inventory(candidate["candidate_sha256"])["observed_inventory_sha256"], "sdk_ledger_sha256": D, "scenario_sha256": D, "scope_partition_id": "PART-one", "session_scope": "isolated_cell", "capability_set_sha256": D, "frozen": True}
    return value | {"freeze_sha256": canonical_sha256(value)}


@pytest.mark.parametrize("status", sorted(OBSERVED))
def test_path_status_derives_closed_status_matrix(status: str) -> None:
    expected = "NOT_APPLICABLE" if status == "NOT_APPLICABLE" else "PASS"
    packet = _path_status(status, expected)
    assert validate_path_status(packet, expected)["qualification"] == derive_path_qualification(expected, status)


def test_blocked_pending_have_no_terminal_and_cannot_pass() -> None:
    for status in ("ENVIRONMENT_BLOCKED", "PENDING"):
        packet = _path_status(status, "PASS")
        assert packet["terminal"] == {"kind": "not_applicable", "count": 0}
        assert packet["qualification"] in {"BLOCKED", "PENDING"}
        with pytest.raises(PacketValidationError):
            validate_path_status(packet | {"qualification": "PASS"}, "PASS")


def test_verified_failure_cannot_be_relabelled_pass() -> None:
    packet = _path_status("VERIFIED_FAILURE", "PASS")
    assert packet["qualification"] == "FAIL"
    with pytest.raises(PacketValidationError):
        validate_path_status(packet | {"qualification": "PASS"}, "PASS")


def test_redaction_enum_is_literal_and_fail_closed() -> None:
    assert len(REDACTION_CATEGORIES) == 19
    valid = {"profile": "v3-safe", "forbidden_field_count": 0, "omitted_categories": sorted(REDACTION_CATEGORIES), "secret_scan": "PASS"}
    assert validate_redaction(valid) == valid
    for bad in (valid | {"omitted_categories": ["not-a-category"]}, valid | {"raw": "x"}, valid | {"omitted_categories": ["auth", "auth"]}):
        with pytest.raises(PacketValidationError):
            validate_redaction(bad)


def test_candidate_freeze_and_result_hashes_are_deterministic() -> None:
    freeze = _freeze()
    assert validate_freeze(freeze) == freeze
    result = _result()
    result["freeze_sha256"] = freeze["freeze_sha256"]
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    assert validate_result(result, freeze=freeze)["result_sha256"] == result["result_sha256"]
    with pytest.raises(PacketValidationError):
        validate_freeze(freeze | {"freeze_sha256": D})


def test_result_rejects_grade_status_tamper() -> None:
    result = _result(grade=_grade("FAIL"))
    with pytest.raises(PacketValidationError, match="grade.status"):
        validate_result(result)


def test_sdk_ledger_requires_unique_exact_source_set() -> None:
    rows = [{"pack_id": "sdk_boundary", "row_id": f"row{n}", "ordinal": n, "executable": True, "classification": "covered_current", "proof": {"ref": f"proof:row{n}", "sha256": D}} for n in range(1, 24)]
    ledger = {"schema_version": 1, "rows": rows}
    ledger |= {"rows_sha256": hash_projection("rows_sha256", ledger), "ledger_sha256": hash_projection("ledger_sha256", ledger)}
    source_keys = [("sdk_boundary", f"row{n}") for n in range(1, 24)]
    assert validate_sdk_ledger(ledger, source_keys) == ledger
    duplicate = deepcopy(ledger)
    duplicate["rows"][-1]["row_id"] = "row1"
    duplicate["rows_sha256"] = hash_projection("rows_sha256", duplicate)
    duplicate["ledger_sha256"] = hash_projection("ledger_sha256", duplicate)
    with pytest.raises(PacketValidationError):
        validate_sdk_ledger(duplicate, source_keys)


def test_aggregate_embeds_and_recomputes_packets() -> None:
    freeze = _freeze()
    result = _result()
    result["freeze_sha256"] = freeze["freeze_sha256"]
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    aggregate = {"aggregate_schema_version": 1, "catalog_sha256": D, "source_map_sha256": D, "contract_sha256": D, "candidate_sha256": _candidate()["candidate_sha256"], "declared_inventory_sha256": D, "sdk_ledger_sha256": D, "partition_packets": [{"partition_id": "PART-one", "freeze_packet": freeze, "result_packet": result}], "source_row_set_sha256": canonical_sha256(result["cells"][0]["source_rows"])}
    aggregate["full_result_sha256"] = hash_projection("full_result_sha256", aggregate)
    aggregate["aggregate_sha256"] = hash_projection("aggregate_sha256", aggregate)
    assert validate_aggregate(aggregate)["aggregate_sha256"] == aggregate["aggregate_sha256"]
    tampered = deepcopy(aggregate)
    tampered["partition_packets"][0]["result_packet"]["grade"]["status"] = "FAIL"
    with pytest.raises(PacketValidationError):
        validate_aggregate(tampered)
