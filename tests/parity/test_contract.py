from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from hermes_claude_agent_sdk.parity.canonical import canonical_sha256, load_json
from hermes_claude_agent_sdk.parity.contract import (
    CatalogValidationError,
    EXPECTED_PACK_COUNTS,
    build_contract_envelope,
    hash_catalog,
    hash_candidate,
    hash_declared_inventory,
    hash_fixture_manifest,
    hash_receipt,
    hash_sdk_ledger,
    hash_source_map,
    load_catalog,
    validate_candidate,
    validate_fixture_manifest,
    validate_resume,
    validate_scenario,
)


H = "a" * 64
S = "b" * 40
EMPTY_STATE = {
    "lifecycle": "fresh",
    "approval": "not_required",
    "tool": "none",
    "resume": "absent",
    "billing": "not_applicable",
    "side_effect_count": 0,
    "boundary_sha256": None,
}
DONE_STATE = {**EMPTY_STATE, "lifecycle": "completed"}


def _path(role: str, required: bool) -> dict[str, Any]:
    if not required:
        return {
            "required": False,
            "expected_outcome": "NOT_APPLICABLE",
            "trace_codes": [],
            "terminal": {"kind": "not_applicable", "count": 0},
            "tool_calls": [],
            "side_effect_count": 0,
            "sdk_events": [],
            "state_before": copy.deepcopy(EMPTY_STATE),
            "state_after": copy.deepcopy(EMPTY_STATE),
        }
    return {
        "required": True,
        "expected_outcome": "EXPECTED_NEGATIVE" if role == "denial" else "PASS",
        "trace_codes": [f"path.{role}.begin", f"terminal.{'failed' if role == 'denial' else 'complete'}", f"path.{role}.end"],
        "terminal": {
            "kind": "failed" if role == "denial" else "complete",
            "count": 1,
        },
        "tool_calls": [],
        "side_effect_count": 0,
        "sdk_events": [],
        "state_before": copy.deepcopy(EMPTY_STATE),
        "state_after": copy.deepcopy(DONE_STATE),
    }


def _pack(pack_id: str, count: int) -> dict[str, Any]:
    rows = [f"{pack_id}-row-{index:03d}" for index in range(1, count + 1)]
    return {
        "id": pack_id,
        "expected_count": count,
        "row_ids": rows,
        "source": {
            "kind": "git_commit",
            "repo_id": "repo-hermes",
            "commit_sha": S,
            "source_ref": f"src:{pack_id}-source",
            "artifact_sha256": H,
        },
        "provenance": {
            "origin_id": f"origin-{pack_id}",
            "license_id": "MIT",
            "attribution_ref": f"src:{pack_id}-attribution",
        },
        "row_ids_sha256": canonical_sha256(rows),
    }


def _catalog() -> dict[str, Any]:
    packs = [_pack(pack_id, count) for pack_id, count in EXPECTED_PACK_COUNTS.items()]
    source_rows = [
        (pack["id"], row_id) for pack in packs for row_id in pack["row_ids"]
    ]
    caps = []
    for index, (pack_id, row_id) in enumerate(source_rows, 1):
        caps.append(
            {
                "id": f"CAP-{index:03d}",
                "source_rows": [{"pack_id": pack_id, "row_id": row_id}],
                "scenario_id": f"SCN-{index:03d}",
                "lane": {"v2_non_soak": "catalog", "openclaw_active": "openclaw", "sdk_boundary": "sdk_boundary", "clawprobench_native": "clawprobench_native"}[pack_id],
                "surface": "tool",
                "owner": "plugin",
                "consumers": ["inventory", "run", "grade"],
                "positive_path": _path("positive", True),
                "denial_path": _path("denial", False),
                "recovery_path": _path("recovery", False),
                "state_before": copy.deepcopy(EMPTY_STATE),
                "state_after": copy.deepcopy(DONE_STATE),
                "expected_trace": ["path.positive.begin", "path.positive.end"],
                "primary_proof": {
                    "kind": "source_map",
                    "ref": f"src:{pack_id}-{index:03d}",
                    "sha256": H,
                },
                "secondary_proof": [],
                "repeat_policy": {"mode": "once", "reason": "stable"},
                "session_scope": "isolated_cell",
                "sdk_classification": "not_runtime_applicable"
                if pack_id != "sdk_boundary"
                else ("requires_0_3_239" if row_id.endswith(("001", "009")) else "covered_current"),
                "required": True,
            }
        )
    partition = {
        "id": "PART-all",
        "session_scope": "isolated_cell",
        "capability_ids": [cap["id"] for cap in caps],
        "capability_set_sha256": canonical_sha256([cap["id"] for cap in caps]),
    }
    receipt = {
        "receipt_schema_version": 1,
        "ref": "evidence:replacement-receipt",
        "receipt_artifact_sha256": H,
        "replacement_receipt_sha256": None,
        "prior_mechanism_id": "parity-v2",
        "prior_gate_id": "passive-soak-48h-100-run",
        "successor_mechanism_id": "parity-v3",
        "successor_gate_id": "feature-parity-v3",
        "v2_evidence_immutable": True,
    }
    receipt["replacement_receipt_sha256"] = hash_receipt(receipt)
    predecessor = {
        "contract_version": "2.0.0",
        "contract_sha256": H,
        "baseline_sha": S,
        "evidence": {"ref": "evidence:baseline", "sha256": H},
        "replacement_receipt": receipt,
    }
    inventory = {
        "schema_version": 1,
        "tools": [],
        "mcp_servers": [],
        "declared_inventory_sha256": None,
    }
    inventory["declared_inventory_sha256"] = hash_declared_inventory(inventory)
    sdk_rows = []
    sdk_pack = next(pack for pack in packs if pack["id"] == "sdk_boundary")
    for ordinal, row_id in enumerate(sdk_pack["row_ids"], 1):
        classification = "requires_0_3_239" if ordinal in (1, 9) else "covered_current"
        sdk_rows.append(
            {
                "pack_id": "sdk_boundary",
                "row_id": row_id,
                "ordinal": ordinal,
                "executable": True,
                "classification": classification,
                "proof": {"ref": f"ledger:{row_id}", "sha256": H},
            }
        )
    ledger = {"schema_version": 1, "rows": sdk_rows, "rows_sha256": None, "ledger_sha256": None}
    ledger["rows_sha256"], ledger["ledger_sha256"] = hash_sdk_ledger(ledger)
    envelope = build_contract_envelope()
    return {
        "catalog_schema_version": "3.0.0",
        "contract_id": envelope["contract_id"],
        "contract_sha256": canonical_sha256(envelope),
        "source_map_sha256": hash_source_map(packs),
        "catalog_sha256": None,
        "predecessor": predecessor,
        "source_packs": packs,
        "scope_partitions": [partition],
        "capabilities": caps,
        "tool_inventory": inventory,
        "sdk_ledger": ledger,
    }


def test_valid_catalog_and_deterministic_hash_pass() -> None:
    catalog = _catalog()
    catalog["catalog_sha256"] = hash_catalog(catalog)
    result = load_catalog(catalog)
    assert result["catalog_sha256"] == hash_catalog(catalog)
    assert hash_catalog({key: catalog[key] for key in reversed(catalog)}) == result["catalog_sha256"]


def test_repository_relative_source_and_proof_refs_allow_single_slashes() -> None:
    catalog = _catalog()
    catalog["source_packs"][1]["source"]["source_ref"] = "src:openclaw@abc123:extensions/qa-lab/src/agentic-parity.ts#L6-11"
    catalog["source_packs"][3]["source"]["source_ref"] = "src:clawprobench@abc123:scenario:scenarios/constraints/19_cron_conflict_buffer_live.yaml"
    catalog["capabilities"][53]["primary_proof"]["ref"] = "src:openclaw@abc123:extensions/qa-lab/src/agentic-parity.ts#L6-11"
    catalog["capabilities"][88]["primary_proof"]["ref"] = "src:clawprobench@abc123:scenario:scenarios/constraints/19_cron_conflict_buffer_live.yaml"
    catalog["source_map_sha256"] = hash_source_map(catalog["source_packs"])
    catalog["catalog_sha256"] = hash_catalog(catalog)
    assert load_catalog(catalog)["catalog_sha256"] == catalog["catalog_sha256"]


@pytest.mark.parametrize("source_ref", [
    "src:openclaw@abc123:extensions/../secret",
    "src:openclaw@abc123:/absolute/path",
    "src:openclaw@abc123:extensions//qa-lab",
    r"src:openclaw@abc123:extensions\\qa-lab",
])
def test_repository_relative_refs_reject_unsafe_paths(source_ref: str) -> None:
    catalog = _catalog()
    catalog["catalog_sha256"] = hash_catalog(catalog)
    catalog["source_packs"][1]["source"]["source_ref"] = source_ref
    with pytest.raises(CatalogValidationError):
        load_catalog(catalog)


@pytest.mark.parametrize("mutation", [
    lambda value: value.update({"extra": True}),
    lambda value: value.pop("capabilities"),
    lambda value: value["source_packs"][0].update({"expected_count": 52}),
    lambda value: value["source_packs"][0]["row_ids"].reverse(),
    lambda value: value["capabilities"][0]["positive_path"].update({"outcome": "PASS"}),
    lambda value: value["capabilities"][0].update({"lane": "v2_non_soak"}),
    lambda value: value["capabilities"][0].update({"prompt": "forbidden"}),
])
def test_catalog_rejects_malformed_or_forbidden_shapes(mutation: Any) -> None:
    catalog = _catalog()
    catalog["catalog_sha256"] = hash_catalog(catalog)
    mutation(catalog)
    with pytest.raises(CatalogValidationError):
        load_catalog(catalog)


def test_catalog_rejects_source_mapping_duplicates_and_hash_tamper() -> None:
    catalog = _catalog()
    catalog["catalog_sha256"] = hash_catalog(catalog)
    catalog["capabilities"][1]["source_rows"] = copy.deepcopy(catalog["capabilities"][0]["source_rows"])
    with pytest.raises(CatalogValidationError):
        load_catalog(catalog)
    catalog = _catalog()
    catalog["catalog_sha256"] = hash_catalog(catalog)
    catalog["source_map_sha256"] = "c" * 64
    with pytest.raises(CatalogValidationError):
        load_catalog(catalog)


@pytest.mark.parametrize("trace_codes", [
    ["path.positive.begin", "path.positive.end"],
    ["path.positive.begin", "terminal.complete", "terminal.complete", "path.positive.end"],
    ["path.positive.begin", "terminal.failed", "path.positive.end"],
])
def test_required_path_requires_one_matching_terminal_trace(trace_codes: list[str]) -> None:
    catalog = _catalog()
    catalog["capabilities"][0]["positive_path"]["trace_codes"] = trace_codes
    catalog["catalog_sha256"] = hash_catalog(catalog)
    with pytest.raises(CatalogValidationError):
        load_catalog(catalog)


@pytest.mark.parametrize("ordinal", [2, 3, 6, 19])
def test_not_runtime_applicable_sdk_rows_allow_all_non_required_paths(ordinal: int) -> None:
    catalog = _catalog()
    capability = next(
        cap for cap in catalog["capabilities"]
        if cap["source_rows"] == [{"pack_id": "sdk_boundary", "row_id": f"sdk_boundary-row-{ordinal:03d}"}]
    )
    for role in ("positive", "denial", "recovery"):
        capability[f"{role}_path"] = _path(role, False)
    capability["sdk_classification"] = "not_runtime_applicable"
    ledger_row = catalog["sdk_ledger"]["rows"][ordinal - 1]
    ledger_row["classification"] = "not_runtime_applicable"
    ledger_row["executable"] = False
    catalog["sdk_ledger"]["rows_sha256"], catalog["sdk_ledger"]["ledger_sha256"] = hash_sdk_ledger(catalog["sdk_ledger"])
    catalog["catalog_sha256"] = hash_catalog(catalog)
    assert load_catalog(catalog)["catalog_sha256"] == catalog["catalog_sha256"]


def test_non_sdk_not_runtime_applicable_capability_requires_an_executable_path() -> None:
    catalog = _catalog()
    capability = catalog["capabilities"][0]
    for role in ("positive", "denial", "recovery"):
        capability[f"{role}_path"] = _path(role, False)
    catalog["catalog_sha256"] = hash_catalog(catalog)
    with pytest.raises(CatalogValidationError):
        load_catalog(catalog)


@pytest.mark.parametrize("classification, executable", [("covered_current", True), ("equivalent_host", False), ("requires_0_3_239", True)])
def test_non_na_sdk_classifications_require_an_executable_path(classification: str, executable: bool) -> None:
    catalog = _catalog()
    capability = next(
        cap for cap in catalog["capabilities"]
        if cap["source_rows"] == [{"pack_id": "sdk_boundary", "row_id": "sdk_boundary-row-002"}]
    )
    for role in ("positive", "denial", "recovery"):
        capability[f"{role}_path"] = _path(role, False)
    capability["sdk_classification"] = classification
    ledger_row = catalog["sdk_ledger"]["rows"][1]
    ledger_row["classification"] = classification
    ledger_row["executable"] = executable
    catalog["sdk_ledger"]["rows_sha256"], catalog["sdk_ledger"]["ledger_sha256"] = hash_sdk_ledger(catalog["sdk_ledger"])
    catalog["catalog_sha256"] = hash_catalog(catalog)
    with pytest.raises(CatalogValidationError):
        load_catalog(catalog)


def test_duplicate_scenario_ids_fail_closed() -> None:
    catalog = _catalog()
    catalog["capabilities"][1]["scenario_id"] = catalog["capabilities"][0]["scenario_id"]
    catalog["catalog_sha256"] = hash_catalog(catalog)
    with pytest.raises(CatalogValidationError):
        load_catalog(catalog)


def test_sdk_ledger_requires_exact_set_and_pinned_stop_rows() -> None:
    catalog = _catalog()
    catalog["catalog_sha256"] = hash_catalog(catalog)
    del catalog["sdk_ledger"]["rows"][1]
    with pytest.raises(CatalogValidationError):
        load_catalog(catalog)
    catalog = _catalog()
    catalog["sdk_ledger"]["rows"][8]["classification"] = "covered_current"
    catalog["sdk_ledger"]["rows_sha256"], catalog["sdk_ledger"]["ledger_sha256"] = hash_sdk_ledger(catalog["sdk_ledger"])
    catalog["catalog_sha256"] = hash_catalog(catalog)
    with pytest.raises(CatalogValidationError):
        load_catalog(catalog)


def test_json_loader_rejects_duplicate_keys_before_catalog_validation() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        load_json('{"catalog_schema_version":"3.0.0","catalog_schema_version":"3.0.0"}')


def test_candidate_fixture_scenario_and_resume_inputs_are_digest_bound() -> None:
    candidate = {
        "candidate_schema_version": 1,
        "plugin_sha": S,
        "host_sha": S,
        "wheel_sha256": H,
        "sdk_distribution": "claude-agent-sdk",
        "sdk_version": "0.2.144",
        "profile_sha256": H,
        "runner_id": "hermes-parity-v3",
        "runner_version": "1.0.0",
        "candidate_sha256": None,
    }
    candidate["candidate_sha256"] = hash_candidate(candidate)
    assert validate_candidate(candidate)["candidate_sha256"] == candidate["candidate_sha256"]
    fixture = {
        "fixture_manifest_schema_version": 1,
        "fixtures": [{"ref": "fixture:demo", "kind": "scenario", "content_sha256": H, "byte_length": 1}],
        "fixture_manifest_sha256": None,
    }
    fixture["fixture_manifest_sha256"] = hash_fixture_manifest(fixture)
    assert validate_fixture_manifest(fixture)["fixture_manifest_sha256"] == fixture["fixture_manifest_sha256"]
    scenario = {
        "scenario_input_schema_version": 1,
        "catalog_sha256": H,
        "fixture_manifest_sha256": fixture["fixture_manifest_sha256"],
        "scope_partition_id": "PART-demo",
        "capabilities": [{"capability_id": "CAP-demo", "scenario_id": "SCN-demo", "fixture_ref": "fixture:demo", "fixture_content_sha256": H, "mode": "deterministic", "session_scope": "isolated_cell"}],
        "scenario_sha256": None,
    }
    scenario["scenario_sha256"] = canonical_sha256({key: value for key, value in scenario.items() if key != "scenario_sha256"})
    assert validate_scenario(scenario)["scenario_sha256"] == scenario["scenario_sha256"]
    resume = {
        "resume_input_schema_version": 1,
        "runtime_id": "hermes-claude-agent-sdk",
        "runtime_schema_version": 1,
        "present": False,
        "state_sha256": None,
        "state_length": 0,
        "fixture_ref": "fixture:none",
        "fixture_content_sha256": None,
        "fixture_manifest_sha256": fixture["fixture_manifest_sha256"],
        "resume_sha256": None,
    }
    resume["resume_sha256"] = canonical_sha256({key: value for key, value in resume.items() if key != "resume_sha256"})
    assert validate_resume(resume)["resume_sha256"] == resume["resume_sha256"]
