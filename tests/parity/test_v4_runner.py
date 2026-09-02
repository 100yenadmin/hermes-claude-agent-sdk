from __future__ import annotations

import copy
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.v4_contract import load_v4_contract
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


def _packet(contract, row, path="positive", classification="COMPLETE"):
    return build_result_packet(
        contract,
        row,
        path=path,
        classification=classification,
        candidate=_candidate(),
        billing_classification="none",
        preflight_results={
            name: "PASS"
            for name in contract["contract"]["ownership_preflights"]
        },
        proof_hashes={name: H for name in ("primary", "secondary", "transcript", "stream")},
        events=[{"sequence": 1, "kind": "terminal", "terminal_outcome": "completed"}],
        trial_index=1,
    )


def test_packet_identity_and_grade_are_deterministic() -> None:
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    row = contract["source_rows"][0]
    packet = _packet(contract, row)
    assert validate_result_packet(packet, contract=contract) == packet
    assert packet["trial_index"] == 1
    report = grade_result_packets([packet], contract=contract)
    assert report["required_paths"] == 220
    assert report["complete_paths"] == 1
    assert report["pending_paths"] == 219
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
        build_result_packet(
            contract,
            runtime,
            path="positive",
            classification="COMPLETE",
            candidate=_candidate(),
            billing_classification="none",
            preflight_results={
                name: "PASS"
                for name in contract["contract"]["ownership_preflights"]
            },
            proof_hashes={
                name: H
                for name in ("primary", "secondary", "transcript", "stream")
            },
            events=[{
                "sequence": 1,
                "kind": "terminal",
                "terminal_outcome": "completed",
            }],
            trial_index=1,
            turn_count=99,
        )


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
