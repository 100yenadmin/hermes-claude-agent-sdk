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
        "trace_codes": [] if not required else [f"terminal.{terminal['kind']}"],
        "terminal": terminal,
        "tool_calls": [],
        "side_effect_count": 0,
        "sdk_events": [],
        "state_before": _state(),
        "state_after": _state("completed" if required else "fresh"),
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
    value["declared_inventory_sha256"] = canonical_sha256({key: value[key] for key in ("tools", "mcp_servers")})
    return value | {"observed_inventory_sha256": canonical_sha256({key: value[key] for key in ("candidate_sha256", "tools", "mcp_servers")})}


def _ledger() -> dict[str, Any]:
    rows = [{"pack_id": "sdk_boundary", "row_id": f"row{n}", "ordinal": n, "executable": True, "classification": "covered_current", "proof": {"ref": f"proof:row{n}", "sha256": D}} for n in range(1, 24)]
    value = {"schema_version": 1, "rows": rows}
    value |= {"rows_sha256": hash_projection("rows_sha256", value), "ledger_sha256": hash_projection("ledger_sha256", value)}
    return value


def _grade(status: str = "PASS", *, verified_failure_count: int = 0, blocked: int = 0, pending: int = 0, expected_negative_count: int = 1, sdk_ledger: dict[str, Any] | None = None, qualifications: dict[str, Any] | None = None, caret: dict[str, bool] | None = None, at: dict[str, bool] | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "cell_qualifications": qualifications or {"CAP-one": {"positive": "PASS", "denial": "EXPECTED_NEGATIVE", "recovery": "PASS", "qualified": True, "not_required_paths": [], "attempts": 1}},
        "required_cell_count": 1,
        "observed_cell_count": 1,
        "required_pass_count": 2,
        "observed_pass_count": 2,
        "expected_negative_count": expected_negative_count,
        "verified_failure_count": verified_failure_count,
        "environment_blocked_count": blocked,
        "pending_count": pending,
        "pass_caret_3": caret or {"CAP-one": False},
        "pass_at_3": at or {"CAP-one": True},
        "candidate_consistent": True,
        "terminal_events_exact": True,
        "inventory_exact": True,
        "billing_safe": True,
        "resume_safe": True,
        "isolation_safe": True,
        "sdk_ledger": sdk_ledger or {"ledger_sha256": _ledger()["ledger_sha256"], "row_count": 23, "requires_0_3_239_rows": [], "upgrade_issue_ref": "issue:16", "status": "CLEAR"},
        "result_bijection": True,
    }


def _catalog() -> dict[str, Any]:
    paths = {name: _path(expected) for name, expected in zip(("positive", "denial", "recovery"), ("PASS", "EXPECTED_NEGATIVE", "PASS"))}
    cap = {"id": "CAP-one", "scenario_id": "SCN-one", "source_rows": [{"pack_id": "v2_non_soak", "row_id": "row1"}], "required": True, "positive_path": paths["positive"], "denial_path": paths["denial"], "recovery_path": paths["recovery"]}
    return {"catalog_sha256": D, "capabilities": [cap], "source_packs": [{"id": "sdk_boundary", "row_ids": [f"row{n}" for n in range(1, 24)]}], "sdk_ledger": _ledger()}


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
            {"seq": 1, "code": "path.positive.begin", "actor": "runner", "phase": "lifecycle", "correlation_sha256": None},
            {"seq": 2, "code": "terminal.complete", "actor": "plugin", "phase": "terminal", "correlation_sha256": None},
            {"seq": 3, "code": "path.positive.end", "actor": "runner", "phase": "lifecycle", "correlation_sha256": None},
            {"seq": 4, "code": "path.denial.begin", "actor": "runner", "phase": "lifecycle", "correlation_sha256": None},
            {"seq": 5, "code": "terminal.failed", "actor": "plugin", "phase": "terminal", "correlation_sha256": None},
            {"seq": 6, "code": "path.denial.end", "actor": "runner", "phase": "lifecycle", "correlation_sha256": None},
            {"seq": 7, "code": "path.recovery.begin", "actor": "runner", "phase": "lifecycle", "correlation_sha256": None},
            {"seq": 8, "code": "terminal.complete", "actor": "plugin", "phase": "terminal", "correlation_sha256": None},
            {"seq": 9, "code": "path.recovery.end", "actor": "runner", "phase": "lifecycle", "correlation_sha256": None},
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
    inventory = _inventory(candidate["candidate_sha256"])
    value = {"freeze_schema_version": 1, "contract_id": "hermes-agent-sdk-feature-parity", "contract_sha256": D, "catalog_sha256": D, "source_map_sha256": D, "receipt_artifact_sha256": D, "replacement_receipt_sha256": D, "fixture_manifest_sha256": D, "candidate_sha256": candidate["candidate_sha256"], "declared_inventory_sha256": inventory["declared_inventory_sha256"], "observed_inventory_sha256": inventory["observed_inventory_sha256"], "sdk_ledger_sha256": _ledger()["ledger_sha256"], "scenario_sha256": D, "scope_partition_id": "PART-one", "session_scope": "isolated_cell", "capability_set_sha256": canonical_sha256(["CAP-one"]), "frozen": True}
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
    assert validate_result(result, freeze=freeze, catalog=_catalog())["result_sha256"] == result["result_sha256"]
    with pytest.raises(PacketValidationError):
        validate_freeze(freeze | {"freeze_sha256": D})


def test_result_rejects_grade_status_tamper() -> None:
    freeze = _freeze()
    result = _result(grade=_grade("FAIL"))
    result["freeze_sha256"] = freeze["freeze_sha256"]
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    with pytest.raises(PacketValidationError, match="grade.status"):
        validate_result(result, freeze=freeze, catalog=_catalog())


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
    aggregate = {"aggregate_schema_version": 1, "catalog_sha256": D, "source_map_sha256": D, "contract_sha256": D, "candidate_sha256": _candidate()["candidate_sha256"], "declared_inventory_sha256": freeze["declared_inventory_sha256"], "sdk_ledger_sha256": _ledger()["ledger_sha256"], "partition_packets": [{"partition_id": "PART-one", "freeze_packet": freeze, "result_packet": result}], "source_row_set_sha256": canonical_sha256(result["cells"][0]["source_rows"])}
    aggregate["full_result_sha256"] = hash_projection("full_result_sha256", aggregate)
    aggregate["aggregate_sha256"] = hash_projection("aggregate_sha256", aggregate)
    assert validate_aggregate(aggregate, catalog=_catalog())["aggregate_sha256"] == aggregate["aggregate_sha256"]
    tampered = deepcopy(aggregate)
    tampered["partition_packets"][0]["result_packet"]["grade"]["status"] = "FAIL"
    with pytest.raises(PacketValidationError):
        validate_aggregate(tampered, catalog=_catalog())


def _bound_result(*, statuses: tuple[str, str, str] = ("PASS", "EXPECTED_NEGATIVE", "PASS"), grade: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    freeze = _freeze()
    result = _result(statuses=statuses, grade=grade)
    result["freeze_sha256"] = freeze["freeze_sha256"]
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    return freeze, result


def test_role_mismatch_required_not_applicable_fails_closed() -> None:
    freeze, result = _bound_result()
    status = result["cells"][0]["attempts"][0]["path_status"]["positive"]
    status.update({"status": "NOT_APPLICABLE", "observed_trace_codes": [], "terminal": {"kind": "not_applicable", "count": 0}, "qualification": "FAIL"})
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    with pytest.raises(PacketValidationError):
        validate_result(result, freeze=freeze, catalog=_catalog())


def test_executed_path_trace_must_be_contiguous_slice() -> None:
    freeze, result = _bound_result()
    trace = result["cells"][0]["attempts"][0]["trace"]
    trace[1], trace[2] = trace[2], trace[1]
    trace[1]["seq"], trace[2]["seq"] = 2, 3
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    with pytest.raises(PacketValidationError, match="contiguous"):
        validate_result(result, freeze=freeze, catalog=_catalog())


def test_all_grade_projections_are_recomputed() -> None:
    freeze, result = _bound_result()
    result["grade"]["pass_at_3"]["CAP-one"] = False
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    with pytest.raises(PacketValidationError, match="qualification projections"):
        validate_result(result, freeze=freeze, catalog=_catalog())


def test_result_requires_catalog_bound_sdk_ledger() -> None:
    freeze, result = _bound_result()
    with pytest.raises(PacketValidationError, match="catalog"):
        validate_result(result, freeze=freeze)


def test_capability_set_digest_is_freeze_bound() -> None:
    freeze, result = _bound_result()
    freeze["capability_set_sha256"] = D
    freeze["freeze_sha256"] = canonical_sha256({key: value for key, value in freeze.items() if key != "freeze_sha256"})
    result["freeze_sha256"] = freeze["freeze_sha256"]
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    with pytest.raises(PacketValidationError, match="capability_set_sha256"):
        validate_result(result, freeze=freeze, catalog=_catalog())


def test_nested_derived_forbidden_key_fails_closed() -> None:
    freeze, result = _bound_result()
    result["grade"]["cell_qualifications"]["CAP-one"]["prompt"] = "PASS"
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    with pytest.raises(PacketValidationError, match="forbidden"):
        validate_result(result, freeze=freeze, catalog=_catalog())


def test_non_required_path_requires_exact_empty_state() -> None:
    from hermes_claude_agent_sdk.parity import packets as packet_impl
    path = _path("NOT_APPLICABLE", required=False)
    path["state_after"] = _state("closed")
    with pytest.raises(PacketValidationError, match="non-required"):
        packet_impl._path(path, "path")


def test_isolated_attempt_requires_fresh_boundary() -> None:
    freeze, result = _bound_result()
    attempt = result["cells"][0]["attempts"][0]
    attempt["fresh_boundary"] = False
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    with pytest.raises(PacketValidationError, match="fresh"):
        validate_result(result, freeze=freeze, catalog=_catalog())


def test_aggregate_rejects_reused_isolated_boundary() -> None:
    catalog = _catalog()
    catalog["capabilities"][0]["repeat_policy"] = {"mode": "consecutive_3", "reason": "stable"}
    freeze, result = _bound_result()
    repeated = deepcopy(result["cells"][0]["attempts"][0])
    repeated["ordinal"] = 2
    result["cells"][0]["attempts"].append(repeated)
    result["run"]["terminal_event_count"] = 6
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    with pytest.raises(PacketValidationError, match="fresh/unique"):
        validate_result(result, freeze=freeze, catalog=catalog)


def test_h4_named_inventory_projection_excludes_only_declared_exceptions() -> None:
    inventory = _inventory(_candidate()["candidate_sha256"])
    assert hash_projection("declared_inventory_sha256", inventory) == canonical_sha256({"tools": [], "mcp_servers": []})
    assert hash_projection("observed_inventory_sha256", inventory) == canonical_sha256({"candidate_sha256": inventory["candidate_sha256"], "tools": [], "mcp_servers": []})


def test_sdk_rows_one_and_nine_force_issue_16_stop() -> None:
    catalog = _catalog()
    ledger = _ledger()
    ledger["rows"][0]["classification"] = "requires_0_3_239"
    ledger["rows"][8]["classification"] = "requires_0_3_239"
    ledger["rows_sha256"] = hash_projection("rows_sha256", ledger)
    ledger["ledger_sha256"] = hash_projection("ledger_sha256", ledger)
    catalog["sdk_ledger"] = ledger
    freeze, result = _bound_result()
    freeze["sdk_ledger_sha256"] = ledger["ledger_sha256"]
    freeze["freeze_sha256"] = canonical_sha256({key: value for key, value in freeze.items() if key != "freeze_sha256"})
    result["freeze_sha256"] = freeze["freeze_sha256"]
    result["grade"]["sdk_ledger"] = {"ledger_sha256": ledger["ledger_sha256"], "row_count": 23, "requires_0_3_239_rows": ["row1", "row9"], "upgrade_issue_ref": "issue:16", "status": "STOP"}
    result["grade"]["status"] = "FAIL"
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    assert validate_result(result, freeze=freeze, catalog=catalog)["grade"]["sdk_ledger"]["status"] == "STOP"
