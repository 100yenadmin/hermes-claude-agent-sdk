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


def test_packet_inventory_accepts_max_length_qualified_drift_names() -> None:
    from hermes_claude_agent_sdk.parity import packets as packet_impl

    inventory = _inventory(D)
    inventory["missing_names"] = [
        f"mcp_server:{'s' * 128}",
        f"tool:{'t' * 128}",
    ]

    assert packet_impl._inventory(inventory)["missing_names"] == inventory["missing_names"]


def test_packet_inventory_keeps_tool_and_mcp_server_names_in_separate_namespaces() -> None:
    from hermes_claude_agent_sdk.parity import packets as packet_impl

    inventory = _inventory(D)
    entry = {"name": "shared", "schema_sha256": D, "enabled": True}
    inventory["tools"] = [entry]
    inventory["mcp_servers"] = [entry]
    inventory["declared_inventory_sha256"] = canonical_sha256(
        {key: inventory[key] for key in ("tools", "mcp_servers")}
    )
    inventory["observed_inventory_sha256"] = canonical_sha256(
        {key: inventory[key] for key in ("candidate_sha256", "tools", "mcp_servers")}
    )

    assert packet_impl._inventory(inventory) == inventory


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
        "sdk_ledger": sdk_ledger or {"ledger_sha256": _ledger()["ledger_sha256"], "row_count": 23, "requires_0_3_239_rows": [], "upgrade_issue_ref": None, "status": "CLEAR"},
        "result_bijection": True,
    }


def _catalog() -> dict[str, Any]:
    paths = {name: _path(expected) for name, expected in zip(("positive", "denial", "recovery"), ("PASS", "EXPECTED_NEGATIVE", "PASS"))}
    cap = {"id": "CAP-one", "scenario_id": "SCN-one", "source_rows": [{"pack_id": "v2_non_soak", "row_id": "row1"}], "required": True, "positive_path": paths["positive"], "denial_path": paths["denial"], "recovery_path": paths["recovery"]}
    return {"catalog_sha256": D, "contract_sha256": D, "source_map_sha256": D, "capabilities": [cap], "scope_partitions": [{"id": "PART-one", "session_scope": "isolated_cell", "capability_ids": ["CAP-one"], "capability_set_sha256": canonical_sha256(["CAP-one"])}], "source_packs": [{"id": "sdk_boundary", "row_ids": [f"row{n}" for n in range(1, 24)]}], "sdk_ledger": _ledger()}


def _two_partition_catalog() -> dict[str, Any]:
    catalog = _catalog()
    second = deepcopy(catalog["capabilities"][0])
    second.update({"id": "CAP-two", "scenario_id": "SCN-two", "source_rows": [{"pack_id": "v2_non_soak", "row_id": "row2"}]})
    catalog["capabilities"].append(second)
    catalog["capabilities"].sort(key=lambda item: item["id"])
    catalog["scope_partitions"] = [{"id": "PART-one", "session_scope": "isolated_cell", "capability_ids": ["CAP-one"], "capability_set_sha256": canonical_sha256(["CAP-one"])}, {"id": "PART-two", "session_scope": "isolated_cell", "capability_ids": ["CAP-two"], "capability_set_sha256": canonical_sha256(["CAP-two"])}]
    return catalog


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


def test_sdk_ledger_rejects_executable_not_runtime_applicable_row() -> None:
    ledger = deepcopy(_ledger())
    ledger["rows"][0]["classification"] = "not_runtime_applicable"
    ledger["rows_sha256"] = hash_projection("rows_sha256", ledger)
    ledger["ledger_sha256"] = hash_projection("ledger_sha256", ledger)
    with pytest.raises(PacketValidationError, match="executable SDK row"):
        validate_sdk_ledger(ledger)


def test_sdk_proof_ref_accepts_repository_relative_source_path() -> None:
    ledger = _ledger()
    ledger["rows"][0]["proof"]["ref"] = "src:openclaw@" + "e" * 40 + ":extensions/anthropic/agent-sdk.runtime.test.ts"
    ledger["rows_sha256"] = hash_projection("rows_sha256", ledger)
    ledger["ledger_sha256"] = hash_projection("ledger_sha256", ledger)
    assert validate_sdk_ledger(ledger)["rows"][0]["proof"]["ref"].count("/") == 2


@pytest.mark.parametrize("ref", ("src:repo:../secret", "/absolute/path", "src:repo:/absolute/path", "src:repo:path//file", r"src:repo:path\file", "src:repo:path\nfile", "src:repo:prompt.txt"))
def test_sdk_proof_ref_rejects_unsafe_paths(ref: str) -> None:
    from hermes_claude_agent_sdk.parity import packets as packet_impl

    with pytest.raises(PacketValidationError):
        packet_impl._proof_ref(ref, "sdk proof ref")


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


def test_aggregate_binds_disjoint_catalog_partitions_and_exact_full_projection() -> None:
    catalog = _two_partition_catalog()
    freeze_one, result_one = _partition_result("PART-one", "CAP-one", "SCN-one", "row1", D)
    freeze_two, result_two = _partition_result("PART-two", "CAP-two", "SCN-two", "row2", "c" * 64)
    aggregate = {"aggregate_schema_version": 1, "catalog_sha256": D, "source_map_sha256": D, "contract_sha256": D, "candidate_sha256": _candidate()["candidate_sha256"], "declared_inventory_sha256": freeze_one["declared_inventory_sha256"], "sdk_ledger_sha256": _ledger()["ledger_sha256"], "partition_packets": [{"partition_id": "PART-one", "freeze_packet": freeze_one, "result_packet": result_one}, {"partition_id": "PART-two", "freeze_packet": freeze_two, "result_packet": result_two}], "source_row_set_sha256": canonical_sha256([{"pack_id": "v2_non_soak", "row_id": "row1"}, {"pack_id": "v2_non_soak", "row_id": "row2"}])}
    aggregate["full_result_sha256"] = hash_projection("full_result_sha256", aggregate)
    aggregate["aggregate_sha256"] = hash_projection("aggregate_sha256", aggregate)
    expected_full = {"source_row_set_sha256": aggregate["source_row_set_sha256"], "partitions": [{"partition_id": "PART-one", "freeze_sha256": freeze_one["freeze_sha256"], "result_sha256": result_one["result_sha256"], "capability_ids": ["CAP-one"]}, {"partition_id": "PART-two", "freeze_sha256": freeze_two["freeze_sha256"], "result_sha256": result_two["result_sha256"], "capability_ids": ["CAP-two"]}]}
    assert aggregate["full_result_sha256"] == canonical_sha256(expected_full)
    assert validate_aggregate(aggregate, catalog=catalog)["aggregate_sha256"] == aggregate["aggregate_sha256"]
    for key in ("contract_sha256", "source_map_sha256"):
        bad = deepcopy(aggregate)
        for packet in bad["partition_packets"]:
            freeze = packet["freeze_packet"]; result = packet["result_packet"]; freeze[key] = result[key] = "e" * 64
            freeze["freeze_sha256"] = canonical_sha256({name: value for name, value in freeze.items() if name != "freeze_sha256"})
            result["freeze_sha256"] = freeze["freeze_sha256"]
            result["result_sha256"] = canonical_sha256({name: value for name, value in result.items() if name != "result_sha256"})
        bad[key] = "e" * 64
        bad["full_result_sha256"] = hash_projection("full_result_sha256", bad)
        bad["aggregate_sha256"] = hash_projection("aggregate_sha256", bad)
        with pytest.raises(PacketValidationError, match="catalog"):
            validate_aggregate(bad, catalog=catalog)
    for bad_packets in (aggregate["partition_packets"][:1], aggregate["partition_packets"] + [aggregate["partition_packets"][0]]):
        bad = deepcopy(aggregate); bad["partition_packets"] = bad_packets
        with pytest.raises(PacketValidationError):
            validate_aggregate(bad, catalog=catalog)
    bad = deepcopy(aggregate); bad["partition_packets"][0]["partition_id"] = "PART-invented"
    with pytest.raises(PacketValidationError):
        validate_aggregate(bad, catalog=catalog)
    bad = deepcopy(aggregate); bad["partition_packets"][1]["result_packet"]["cells"][0]["capability_id"] = "CAP-one"
    with pytest.raises(PacketValidationError):
        validate_aggregate(bad, catalog=catalog)
    whole = deepcopy(aggregate); whole["partition_packets"][0]["result_packet"]["cells"].append(deepcopy(whole["partition_packets"][1]["result_packet"]["cells"][0]))
    with pytest.raises(PacketValidationError):
        validate_aggregate(whole, catalog=catalog)
    old = deepcopy(aggregate); old["full_result_sha256"] = canonical_sha256({"source_row_set_sha256": aggregate["source_row_set_sha256"], "partitions": [{"partition_id": packet["partition_id"], "freeze_sha256": packet["freeze_packet"]["freeze_sha256"], "result_sha256": packet["result_packet"]["result_sha256"], "capability_ids": sorted(cell["capability_id"] for cell in packet["result_packet"]["cells"]), "source_rows": sorted((row for cell in packet["result_packet"]["cells"] for row in cell["source_rows"]), key=lambda row: (row["pack_id"], row["row_id"]))} for packet in aggregate["partition_packets"]]})
    with pytest.raises(PacketValidationError):
        validate_aggregate(old, catalog=catalog)


def _bound_result(*, statuses: tuple[str, str, str] = ("PASS", "EXPECTED_NEGATIVE", "PASS"), grade: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    freeze = _freeze()
    result = _result(statuses=statuses, grade=grade)
    result["freeze_sha256"] = freeze["freeze_sha256"]
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    return freeze, result


def _partition_result(partition_id: str, capability_id: str, scenario_id: str, row_id: str, boundary: str) -> tuple[dict[str, Any], dict[str, Any]]:
    freeze, result = _bound_result()
    result["run"]["scope_partition_id"] = partition_id
    result["cells"][0].update({"capability_id": capability_id, "scenario_id": scenario_id, "source_rows": [{"pack_id": "v2_non_soak", "row_id": row_id}]})
    result["cells"][0]["attempts"][0]["boundary_sha256"] = boundary
    for key in ("cell_qualifications", "pass_caret_3", "pass_at_3"):
        result["grade"][key] = {capability_id: result["grade"][key]["CAP-one"]}
    freeze.update({"scope_partition_id": partition_id, "capability_set_sha256": canonical_sha256([capability_id])})
    freeze["freeze_sha256"] = canonical_sha256({key: value for key, value in freeze.items() if key != "freeze_sha256"})
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


def test_required_request_id_catalog_null_accepts_only_result_digest() -> None:
    catalog = _catalog()
    expected = catalog["capabilities"][0]["positive_path"]
    expected["tool_calls"] = [{"ordinal": 1, "name": "tool", "schema_sha256": D, "outcome": "requested", "request_id": {"mode": "required", "sha256": None}}]
    freeze, result = _bound_result()
    result["cells"][0]["attempts"][0]["paths"]["positive"] = deepcopy(expected)
    result["cells"][0]["attempts"][0]["paths"]["positive"]["tool_calls"][0]["request_id"]["sha256"] = D
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    assert validate_result(result, freeze=freeze, catalog=catalog)["result_sha256"] == result["result_sha256"]
    result["cells"][0]["attempts"][0]["paths"]["positive"]["tool_calls"][0]["request_id"]["sha256"] = None
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    with pytest.raises(PacketValidationError, match="differs from catalog"):
        validate_result(result, freeze=freeze, catalog=catalog)
    result["cells"][0]["attempts"][0]["paths"]["positive"]["tool_calls"][0]["request_id"]["sha256"] = D
    result["cells"][0]["attempts"][0]["paths"]["positive"]["tool_calls"][0]["name"] = "other"
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    with pytest.raises(PacketValidationError, match="differs from catalog"):
        validate_result(result, freeze=freeze, catalog=catalog)


def test_result_rejects_candidate_inventory_and_catalog_identity_drift() -> None:
    freeze, result = _bound_result()
    result["inventory"]["candidate_sha256"] = D
    result["inventory"]["observed_inventory_sha256"] = hash_projection("observed_inventory_sha256", result["inventory"])
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    with pytest.raises(PacketValidationError, match="candidate/inventory"):
        validate_result(result, freeze=freeze, catalog=_catalog())
    freeze, result = _bound_result()
    catalog = _catalog() | {"catalog_sha256": "e" * 64}
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    with pytest.raises(PacketValidationError, match="catalog"):
        validate_result(result, freeze=freeze, catalog=catalog)


def test_result_and_freeze_root_identities_must_match_catalog() -> None:
    for key in ("contract_sha256", "source_map_sha256"):
        freeze, result = _bound_result()
        freeze[key] = result[key] = "e" * 64
        freeze["freeze_sha256"] = canonical_sha256({name: value for name, value in freeze.items() if name != "freeze_sha256"})
        result["freeze_sha256"] = freeze["freeze_sha256"]
        result["result_sha256"] = canonical_sha256({name: value for name, value in result.items() if name != "result_sha256"})
        with pytest.raises(PacketValidationError, match="catalog"):
            validate_result(result, freeze=freeze, catalog=_catalog())


@pytest.mark.parametrize(
    "case",
    ("missing_capability_id", "non_mapping_capability", "unknown_partition_capability", "invalid_repeat_policy"),
)
def test_malformed_catalog_inputs_raise_typed_packet_errors(case: str) -> None:
    catalog = _catalog()
    if case == "missing_capability_id":
        catalog["capabilities"][0].pop("id")
    elif case == "non_mapping_capability":
        catalog["capabilities"][0] = "not-a-capability"
    elif case == "unknown_partition_capability":
        catalog["scope_partitions"][0]["capability_ids"] = ["CAP-missing"]
    else:
        catalog["capabilities"][0]["repeat_policy"] = None
    freeze, result = _bound_result()

    with pytest.raises(PacketValidationError, match="catalog binding"):
        validate_result(result, freeze=freeze, catalog=catalog)


def test_verified_failure_trace_may_deviate_but_qualifies_fail() -> None:
    from hermes_claude_agent_sdk.parity import packets as packet_impl

    catalog = _catalog()
    attempt = _result(statuses=("VERIFIED_FAILURE", "EXPECTED_NEGATIVE", "PASS"))["cells"][0]["attempts"][0]
    attempt["trace"][1]["code"] = "terminal.failed"
    expected_paths = {name: catalog["capabilities"][0][f"{name}_path"] for name in ("positive", "denial", "recovery")}
    expected_trace = ["path.positive.begin", "terminal.complete", "path.positive.end", "path.denial.begin", "terminal.failed", "path.denial.end", "path.recovery.begin", "terminal.complete", "path.recovery.end"]
    validated = packet_impl._attempt(attempt, expected_paths, "attempt", expected_trace)
    assert validated["path_status"]["positive"]["qualification"] == "FAIL"


def test_pass_trace_still_must_equal_catalog_trace() -> None:
    from hermes_claude_agent_sdk.parity import packets as packet_impl

    catalog = _catalog()
    attempt = _result()["cells"][0]["attempts"][0]
    attempt["trace"][1]["code"] = "terminal.failed"
    expected_paths = {name: catalog["capabilities"][0][f"{name}_path"] for name in ("positive", "denial", "recovery")}
    expected_trace = ["path.positive.begin", "terminal.complete", "path.positive.end", "path.denial.begin", "terminal.failed", "path.denial.end", "path.recovery.begin", "terminal.complete", "path.recovery.end"]
    with pytest.raises(PacketValidationError, match="differs from catalog"):
        packet_impl._attempt(attempt, expected_paths, "attempt", expected_trace)


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


def test_h4_source_fixture_scenario_projections_are_exact() -> None:
    packs = [
        {"id": "pack-b", "expected_count": 2, "row_ids": ["b1"], "source": {"kind": "git", "ref": "src:repo:two.py"}, "provenance": {"kind": "ledger", "ref": "evidence:two"}, "ignored": "excluded"},
        {"id": "pack-a", "expected_count": 1, "row_ids": ["a1"], "source": {"kind": "git", "ref": "src:repo:one.py"}, "provenance": {"kind": "ledger", "ref": "evidence:one"}, "ignored": "excluded"},
    ]
    source_projection = [{key: pack[key] for key in ("id", "expected_count", "row_ids", "source", "provenance")} for pack in sorted(packs, key=lambda item: item["id"])]
    assert hash_projection("source_map_sha256", {"source_packs": packs}) == canonical_sha256(source_projection)
    fixtures = [{"ref": "fixture:z", "kind": "resume", "content_sha256": D, "byte_length": 2}, {"ref": "fixture:a", "kind": "scenario", "content_sha256": D, "byte_length": 1}]
    assert hash_projection("fixture_manifest_sha256", {"fixtures": fixtures}) == canonical_sha256(sorted(fixtures, key=lambda item: item["ref"]))
    capabilities = [{"capability_id": "CAP-two", "scenario_id": "SCN-two", "fixture_ref": "fixture:z", "fixture_content_sha256": D, "mode": "integration", "session_scope": "isolated_cell"}, {"capability_id": "CAP-one", "scenario_id": "SCN-one", "fixture_ref": "fixture:a", "fixture_content_sha256": D, "mode": "deterministic", "session_scope": "isolated_cell"}]
    scenario = {"scenario_input_schema_version": 1, "catalog_sha256": D, "fixture_manifest_sha256": D, "scope_partition_id": "PART-one", "capabilities": capabilities}
    expected = scenario | {"capabilities": sorted(capabilities, key=lambda item: item["capability_id"])}
    assert hash_projection("scenario_sha256", scenario) == canonical_sha256(expected)


def test_any_evidence_triggered_sdk_row_forces_upgrade_stop() -> None:
    catalog = _catalog()
    ledger = _ledger()
    ledger["rows"][4]["classification"] = "requires_0_3_239"
    ledger["rows_sha256"] = hash_projection("rows_sha256", ledger)
    ledger["ledger_sha256"] = hash_projection("ledger_sha256", ledger)
    catalog["sdk_ledger"] = ledger
    freeze, result = _bound_result()
    freeze["sdk_ledger_sha256"] = ledger["ledger_sha256"]
    freeze["freeze_sha256"] = canonical_sha256({key: value for key, value in freeze.items() if key != "freeze_sha256"})
    result["freeze_sha256"] = freeze["freeze_sha256"]
    result["grade"]["sdk_ledger"] = {"ledger_sha256": ledger["ledger_sha256"], "row_count": 23, "requires_0_3_239_rows": ["row5"], "upgrade_issue_ref": "issue:16", "status": "STOP"}
    result["grade"]["status"] = "FAIL"
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    assert validate_result(result, freeze=freeze, catalog=catalog)["grade"]["sdk_ledger"]["status"] == "STOP"


def test_sdk_grade_cannot_clear_evidence_triggered_stop() -> None:
    freeze, result = _bound_result()
    catalog = _catalog()
    ledger = deepcopy(catalog["sdk_ledger"])
    ledger["rows"][4]["classification"] = "requires_0_3_239"
    ledger["rows_sha256"] = hash_projection("rows_sha256", ledger)
    ledger["ledger_sha256"] = hash_projection("ledger_sha256", ledger)
    catalog["sdk_ledger"] = ledger
    result["grade"]["sdk_ledger"] = {"ledger_sha256": ledger["ledger_sha256"], "row_count": 23, "requires_0_3_239_rows": [], "upgrade_issue_ref": None, "status": "CLEAR"}
    result["grade"]["status"] = "PASS"
    result["result_sha256"] = canonical_sha256({key: value for key, value in result.items() if key != "result_sha256"})
    with pytest.raises(PacketValidationError, match="grade.sdk_ledger"):
        validate_result(result, freeze=freeze, catalog=catalog)
