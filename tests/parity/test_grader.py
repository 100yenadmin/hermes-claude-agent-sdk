from __future__ import annotations

import pytest

from hermes_claude_agent_sdk.parity.grader import grade_packets
from hermes_claude_agent_sdk.parity.results import (
    ExecutionClassification,
    ResultViolation,
)

from .conftest import make_packet


def _complete_rc_packets(catalog, candidate_fields):
    packets = []
    for capability in catalog.for_lane("rc"):
        repeats = int(capability.repeat_policy["consecutive_passes"])
        if set(capability.repeat_policy["triggers"]) & {"consequential", "unstable"}:
            repeats = max(repeats, 3)
        for path in ("positive", "denial", "recovery"):
            for trial in range(1, repeats + 1):
                packets.append(
                    make_packet(
                        catalog,
                        capability.capability_id,
                        path,
                        trial,
                        candidate_fields,
                    )
                )
    return packets


def test_full_exact_candidate_can_reach_complete_without_partial_paths(
    catalog, candidate_fields
) -> None:
    packets = _complete_rc_packets(catalog, candidate_fields)
    report = grade_packets(catalog, packets, lane="rc")
    assert report.status == "COMPLETE"
    assert report.exit_code == 0
    assert report.required_paths == 124 * 3
    assert report.passed_paths == report.required_paths
    assert report.pending_paths == 0
    assert report.failed_paths == 0
    assert report.pass_at_3_paths == report.required_paths
    assert report.source_coverage == {
        "agent_sdk_boundary": 23,
        "clawprobench_native": 36,
        "openclaw_active": 12,
        "v2_non_soak": 53,
    }


def test_pass_at_3_never_substitutes_for_strict_three_consecutive_passes(
    catalog, candidate_fields
) -> None:
    capability_id = "v2:parent-01"
    packets = [
        make_packet(
            catalog,
            capability_id,
            "positive",
            1,
            candidate_fields,
            classification=ExecutionClassification.VERIFIED_FAILURE,
        ),
        make_packet(catalog, capability_id, "positive", 2, candidate_fields),
        make_packet(
            catalog,
            capability_id,
            "positive",
            3,
            candidate_fields,
            classification=ExecutionClassification.VERIFIED_FAILURE,
        ),
    ]
    report = grade_packets(catalog, packets, lane="rc")
    grade = next(
        item
        for item in report.path_grades
        if item.capability_id == capability_id and item.path == "positive"
    )
    assert grade.pass_at_3 is True
    assert grade.pass_power_3 is False
    assert grade.status == "VERIFIED_FAILURE"
    assert report.exit_code == 1


def test_failure_recovers_only_after_three_consecutive_unchanged_candidate_passes(
    catalog, candidate_fields
) -> None:
    capability_id = "v2:parent-01"
    packets = [
        make_packet(
            catalog,
            capability_id,
            "positive",
            1,
            candidate_fields,
            classification=ExecutionClassification.VERIFIED_FAILURE,
        )
    ]
    packets.extend(
        make_packet(catalog, capability_id, "positive", trial, candidate_fields)
        for trial in (2, 3, 4)
    )
    report = grade_packets(catalog, packets, lane="rc")
    grade = next(
        item
        for item in report.path_grades
        if item.capability_id == capability_id and item.path == "positive"
    )
    assert grade.status == "COMPLETE"
    assert grade.pass_power_3 is True
    assert report.exit_code == 75  # The rest of the RC catalog is still pending.


def test_grade_rejects_mixed_candidate_identity(catalog, candidate_fields) -> None:
    first = make_packet(catalog, "v2:parent-01", "positive", 1, candidate_fields)
    changed = dict(candidate_fields)
    changed["host_sha"] = "9" * 40
    second = make_packet(catalog, "v2:parent-01", "positive", 2, changed)
    with pytest.raises(ResultViolation, match="multiple candidate"):
        grade_packets(catalog, [first, second], lane="rc")


def test_grade_rejects_duplicate_trial_receipt(catalog, candidate_fields) -> None:
    packet = make_packet(catalog, "v2:parent-01", "positive", 1, candidate_fields)
    with pytest.raises(ResultViolation, match="duplicate result"):
        grade_packets(catalog, [packet, packet], lane="rc")
