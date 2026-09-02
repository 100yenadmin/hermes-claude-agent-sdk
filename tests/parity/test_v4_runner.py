from __future__ import annotations

import copy
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.v4_contract import (
    V3_RESULT_CATALOG_HASH,
    load_v4_contract,
)
from hermes_claude_agent_sdk.parity.v4_runner import (
    V4ResultViolation,
    build_result_packet,
    grade_result_packets,
    validate_result_packet,
)

ROOT = Path(__file__).parents[2]
H = "a" * 64
S = "b" * 40


def _candidate() -> dict[str, str]:
    return {
        "plugin_sha": S,
        "host_sha": S,
        "wheel_sha256": H,
        "profile_sha256": H,
    }


def _packet(contract, row, path="positive", classification="COMPLETE", trial_index=1, turn_count=0, billing_classification=None):
    if classification in {"PENDING", "ENVIRONMENT_BLOCKED"}:
        events = [{"sequence": 1, "kind": "preflight"}]
    else:
        events = [{"sequence": index, "kind": kind} for index, kind in enumerate(row["expected_trace"], 1)]
        events[-1]["terminal_outcome"] = "denied" if classification == "EXPECTED_NEGATIVE" else "completed"
    return build_result_packet(
        contract,
        row,
        path=path,
        classification=classification,
        candidate=_candidate(),
        billing_classification=billing_classification or ("subscription_included" if row["provider_live_required"] else "none"),
        preflight_results={
            name: "PASS"
            for name in contract["contract"]["ownership_preflights"]
        },
        proof_hashes={name: H for name in ("primary", "secondary", "transcript", "stream")},
        events=events,
        predecessor_catalog_sha256=V3_RESULT_CATALOG_HASH,
        predecessor_packet_sha256=H,
        trial_index=trial_index,
        turn_count=turn_count,
    )


def test_packet_identity_and_grade_are_deterministic() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    row = next(row for row in contract["source_rows"] if row["source_item_id"] == "PARENT-01")
    packet = _packet(contract, row)
    assert validate_result_packet(packet, contract=contract) == packet
    assert packet["trial_index"] == 1
    report = grade_result_packets([packet], contract=contract)
    assert report["required_paths"] == 220
    assert report["complete_paths"] == 1
    assert report["not_run_paths"] == 219
    assert report["status"] == "PENDING"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda packet: packet.update({"source_item_id": "other"}),
        lambda packet: packet.update({"classification": "PARTIAL"}),
        lambda packet: packet.update({"billing_classification": "unsafe"}),
        lambda packet: packet.update({"silent_fallback": True}),
        lambda packet: packet["preflight_results"].update({"delegate_owner": "FAIL"}),
        lambda packet: packet.update({"path": "not-a-path"}),
    ],
)
def test_runner_rejects_unsafe_or_mismatched_packets(mutation) -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    packet = _packet(contract, contract["source_rows"][0])
    mutation(packet)
    with pytest.raises(V4ResultViolation):
        validate_result_packet(packet, contract=contract)


def test_runner_rejects_duplicate_paths_and_wrong_runtime_turn_budget() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    row = contract["source_rows"][0]
    packet = _packet(contract, row)
    with pytest.raises(V4ResultViolation):
        grade_result_packets([packet, copy.deepcopy(packet)], contract=contract)

    runtime = contract["runtime_soak"]
    with pytest.raises(V4ResultViolation):
        grade_result_packets([_packet(contract, runtime, path=path, classification=classification, turn_count=33) for path, classification in (("positive", "COMPLETE"), ("denial", "EXPECTED_NEGATIVE"), ("recovery", "COMPLETE"))], contract=contract, lane="runtime")


def test_runner_requires_the_exact_consequential_trial_set() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    row = next(row for row in contract["source_rows"] if row["source_item_id"] == "AUTH-01")
    partial = grade_result_packets([_packet(contract, row)], contract=contract)
    assert partial["status"] == "PARTIAL"
    assert partial["partial_paths"] == 1
    assert partial["not_run_paths"] == 219
    assert next(item for item in partial["path_results"] if item["source_item_id"] == "AUTH-01")["status"] == "PARTIAL"
    report = grade_result_packets([_packet(contract, row, trial_index=index) for index in (1, 2, 3)], contract=contract)
    assert report["complete_paths"] == 1


@pytest.mark.parametrize("item_id,trial_index", [("AUTH-01", 4), ("PARENT-01", 2)])
def test_runner_rejects_extra_trial_indexes(item_id, trial_index) -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    row = next(row for row in contract["source_rows"] if row["source_item_id"] == item_id)
    with pytest.raises(V4ResultViolation):
        grade_result_packets([_packet(contract, row, trial_index=trial_index)], contract=contract)


def test_runtime_uses_one_hundred_parent_turns_across_three_path_packets() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    row = contract["runtime_soak"]
    packets = [
        _packet(contract, row, path="positive", turn_count=34),
        _packet(contract, row, path="denial", classification="EXPECTED_NEGATIVE", turn_count=33),
        _packet(contract, row, path="recovery", turn_count=33),
    ]
    report = grade_result_packets(packets, contract=contract, lane="runtime")
    assert report["required_trial_packets"] == 3
    assert report["complete_paths"] == 3


@pytest.mark.parametrize("value", [None, 0, True, "1"])
def test_runner_rejects_malformed_trial_index(value) -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    packet = _packet(contract, contract["source_rows"][0])
    if value is None:
        packet.pop("trial_index")
    else:
        packet["trial_index"] = value
    with pytest.raises(V4ResultViolation):
        validate_result_packet(packet, contract=contract)


@pytest.mark.parametrize("field", ["catalog_sha256", "packet_sha256"])
def test_runner_rejects_predecessor_identity_drift(field) -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    packet = _packet(contract, contract["source_rows"][0])
    packet["predecessor"][field] = "0" * 64
    with pytest.raises(V4ResultViolation):
        validate_result_packet(packet, contract=contract)


def test_runner_requires_subscription_billing_only_for_passing_provider_live_trials() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    live = next(row for row in contract["source_rows"] if row["source_item_id"] == "instruction-followthrough-repo-contract")
    for unsafe_billing in ("none", "explicitly_free"):
        with pytest.raises(V4ResultViolation):
            _packet(contract, live, billing_classification=unsafe_billing)
    blocked = _packet(
        contract,
        live,
        classification="ENVIRONMENT_BLOCKED",
        billing_classification="none",
    )
    assert grade_result_packets([blocked], contract=contract)["pending_paths"] == 1
    deterministic = next(row for row in contract["source_rows"] if row["source_item_id"] == "approval-turn-tool-followthrough")
    packet = _packet(contract, deterministic, billing_classification="none")
    assert validate_result_packet(packet, contract=contract) == packet


def test_runner_marks_wrong_terminal_classification_as_failure() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    row = next(row for row in contract["source_rows"] if "denial" in row["mandatory_paths"])
    report = grade_result_packets([_packet(contract, row, path="denial", classification="COMPLETE")], contract=contract)
    assert report["status"] == "VERIFIED_FAILURE"
    assert report["failed_paths"] == 1
