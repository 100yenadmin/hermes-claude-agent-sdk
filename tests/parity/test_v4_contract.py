from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.v4_contract import (
    V4ContractViolation,
    load_v4_boundary_ledger,
    load_v4_contract,
    load_v4_manifest,
    load_v4_predecessor_map,
    mandatory_path_count,
    validate_v4_contract,
)

ROOT = Path(__file__).parents[2]
V3_HASHES = {
    "parity-contract-v3.yaml": "e601f41313deb68b77a01402fe3b79c5da90afc7c46e40f87a6bac1850b69d8a",
    "agent-sdk-boundary-ledger-v3.yaml": "22e738bebca804514cfd8311d0ff1bf1bc9da6e6a8d21cce5fb9f6aa31f1463b",
    "result-packet-v3.schema.json": "dde70d2fbaa5e1cc669ff6167f89f043cc6854cf740ddff8e40c3dcb68ee1295",
}


def test_v4_artifacts_are_valid_and_preserve_the_frozen_budget() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    ledger = load_v4_boundary_ledger(ROOT / "qa/agent-sdk-boundary-ledger-v4.yaml")
    predecessor = load_v4_predecessor_map(ROOT / "qa/parity-v4-predecessor-map.yaml")
    validated = validate_v4_contract(contract, ledger=ledger, predecessor_map=predecessor)

    assert validated["counts"] == {
        "v2_non_soak": {"rows": 53, "mandatory_paths": 53},
        "openclaw_active": {"rows": 12, "mandatory_paths": 36},
        "agent_sdk_boundary": {"rows": 23, "mandatory_paths": 23},
        "clawprobench_native": {"rows": 36, "mandatory_paths": 108},
    }
    assert validated["total_rows"] == 124
    assert validated["mandatory_paths"] == 220
    assert validated["required_trial_packets"] == 390
    assert validated["provider_live_rows"] == 70
    assert sum("consequential" in row["repeat_policy"]["triggers"] for row in contract["source_rows"]) == 55
    assert all(row["expected_trace"] and row["repeat_policy"] for row in contract["source_rows"])
    assert all(row["primary_proof"] and row["secondary_proof"] for row in contract["source_rows"])
    assert sum(row["provider_live_required"] for row in contract["source_rows"]) == 70
    assert {
        row["source_item_id"]
        for row in contract["source_rows"]
        if row["source_pack"] == "openclaw_active" and row["provider_live_required"]
    } == {
        "source-docs-discovery-report",
        "image-understanding-attachment",
        "subagent-handoff",
        "subagent-fanout-synthesis",
        "memory-recall",
        "thread-memory-isolation",
        "config-restart-capability-flip",
        "instruction-followthrough-repo-contract",
    }
    assert validated["disposition_totals"] == {
        "carry": 8,
        "replace": 102,
        "retire-with-successor": 3,
        "split": 11,
    }
    assert contract["contract"]["target"] == {
        "sdk_distribution": "claude-agent-sdk",
        "sdk_version": "0.2.151",
        "cli_version": "2.1.258",
        "model": "claude-fable-5-1",
    }
    assert contract["contract"]["name"] == (
        "Hermes-owned Claude Subscription Runtime Parity"
    )
    assert contract["runtime_soak"]["source_item_id"] == "soak-100-turn"
    assert contract["runtime_soak"]["mandatory_paths"] == [
        "positive",
        "denial",
        "recovery",
    ]
    assert contract["runtime_soak"]["turns"] == 100
    assert len(ledger["rows"]) == 23


def test_v4_manifest_binds_every_immutable_artifact() -> None:
    manifest = load_v4_manifest(ROOT / "qa/parity-v4-manifest.json")

    assert manifest["counts"]["total_rows"] == 124
    assert manifest["counts"]["mandatory_paths"] == 220
    assert manifest["counts"]["provider_live_rows"] == 70
    assert manifest["target"]["sdk_version"] == "0.2.151"
    assert manifest["target"]["cli_version"] == "2.1.258"


def test_v3_provenance_is_byte_identical() -> None:
    for name, expected in V3_HASHES.items():
        digest = hashlib.sha256((ROOT / "qa" / name).read_bytes()).hexdigest()
        assert digest == expected


def test_v4_has_one_successor_for_every_source_row_and_path() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    rows = contract["source_rows"]
    assert len(rows) == len({(row["source_pack"], row["source_item_id"]) for row in rows}) == 124
    assert len({row["successor_id"] for row in rows}) == 124
    assert all(row["successor_id"] == f"hermes-v4/{row['source_pack']}/{row['source_item_id']}" for row in rows)
    assert mandatory_path_count(rows) == 220
    assert all(row["mandatory_paths"] for row in rows)


def test_ownership_rows_are_hermes_owned_and_cover_native_replacements() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    preflights = set(contract["contract"]["ownership_preflights"])
    assert {
        "zero_native_absence",
        "exact_prompt_settings_tools_mcp",
        "no_native_events_projector",
        "delegate_owner",
        "background_owner",
        "canonical_transcript_content",
        "streaming_owner",
        "redaction_fail_closed",
    } <= preflights
    delegated = [row for row in contract["source_rows"] if row["ownership_mode"] == "delegate_task"]
    background = [row for row in contract["source_rows"] if row["ownership_mode"] == "host_background"]
    assert delegated and background
    assert all(row["native_surface"] is False for row in delegated + background)
    assert all("delegate_task" in row["proof_atoms"] for row in delegated)
    assert all("host_background" in row["proof_atoms"] for row in background)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["source_rows"].pop(),
        lambda value: value["source_rows"][0].update({"successor_id": "hermes-v4/v2_non_soak/other"}),
        lambda value: value["source_rows"][0]["mandatory_paths"].append("positive"),
        lambda value: value["source_rows"][0].update({"predecessor_execution_id": None}),
    ],
)
def test_v4_contract_rejects_cardinality_or_predecessor_drift(mutation) -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    mutation(copy.deepcopy(contract))
    # Mutations are applied to a deep copy above; apply them to the object passed
    # to the validator explicitly so this test stays independent of YAML writing.
    mutated = copy.deepcopy(contract)
    mutation(mutated)
    with pytest.raises(V4ContractViolation):
        validate_v4_contract(mutated)


def test_contract_validation_does_not_accept_raw_content_or_unknown_fields() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    mutated = copy.deepcopy(contract)
    mutated["source_rows"][0]["raw_prompt"] = "forbidden"
    with pytest.raises(V4ContractViolation):
        validate_v4_contract(mutated)
