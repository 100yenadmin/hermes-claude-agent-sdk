from __future__ import annotations

import copy

import pytest

from hermes_claude_agent_sdk.parity.results import (
    ExecutionClassification,
    ResultPacket,
    ResultViolation,
)

from .conftest import make_packet


def test_result_packet_round_trip_binds_candidate_and_hashes(catalog, candidate_fields) -> None:
    packet = make_packet(catalog, "v2:auth-01", "positive", 1, candidate_fields)
    reloaded = ResultPacket.from_dict(packet.to_dict())
    assert reloaded == packet
    assert len(packet.candidate_hash) == 64
    assert len(packet.trace_hash) == 64
    assert len(packet.packet_hash) == 64


def test_expected_denial_is_positive_evidence(catalog, candidate_fields) -> None:
    packet = make_packet(catalog, "v2:auth-01", "denial", 1, candidate_fields)
    assert packet.classification is ExecutionClassification.EXPECTED_NEGATIVE
    assert packet.normalized_events[-1]["terminal_outcome"] == "denied"


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("billing_classification", "unknown", "billing"),
        ("silent_fallback", True, "fallback"),
        ("invariant_violations", ["approval_bypass"], "invariant"),
    ],
)
def test_result_packet_fails_closed_on_safety_signals(
    catalog, candidate_fields, field, value, match
) -> None:
    raw = make_packet(catalog, "v2:auth-01", "positive", 1, candidate_fields).to_dict()
    raw[field] = value
    raw.pop("packet_hash")
    from hermes_claude_agent_sdk.parity.hashing import sha256_value

    raw["packet_hash"] = sha256_value(raw)
    with pytest.raises(ResultViolation, match=match):
        ResultPacket.from_dict(raw)


def test_result_packet_rejects_raw_or_session_fields(catalog, candidate_fields) -> None:
    raw = make_packet(catalog, "v2:auth-01", "positive", 1, candidate_fields).to_dict()
    raw["normalized_events"][0]["session_id"] = "must-not-persist"
    with pytest.raises(ResultViolation, match="forbidden field session_id"):
        ResultPacket.from_dict(raw)


def test_result_packet_requires_exactly_one_terminal_for_completed_execution(
    catalog, candidate_fields
) -> None:
    raw = make_packet(catalog, "v2:auth-01", "positive", 1, candidate_fields).to_dict()
    raw["normalized_events"] = raw["normalized_events"][:-1]
    from hermes_claude_agent_sdk.parity.hashing import sha256_value

    raw["trace_hash"] = sha256_value(raw["normalized_events"])
    raw.pop("packet_hash")
    raw["packet_hash"] = sha256_value(raw)
    with pytest.raises(ResultViolation, match="exactly one terminal"):
        ResultPacket.from_dict(raw)


def test_packet_hash_detects_post_execution_mutation(catalog, candidate_fields) -> None:
    raw = make_packet(catalog, "v2:auth-01", "positive", 1, candidate_fields).to_dict()
    tampered = copy.deepcopy(raw)
    tampered["trial_index"] = 2
    with pytest.raises(ResultViolation, match="packet_hash"):
        ResultPacket.from_dict(tampered)
