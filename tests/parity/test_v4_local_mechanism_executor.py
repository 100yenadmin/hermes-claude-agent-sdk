from __future__ import annotations

import copy
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.v4_contract import load_v4_contract
from hermes_claude_agent_sdk.parity.v4_live_map import load_v4_live_execution_map
from hermes_claude_agent_sdk.parity.v4_live_packets import _local
from hermes_claude_agent_sdk.parity.v4_live_scenarios import build_v4_live_scenario_catalog
from hermes_claude_agent_sdk.parity.v4_local_mechanism_executor import (
    V4LocalMechanismExecutorViolation,
    execute_v4_local_mechanism,
    generic_v4_local_rows,
)

ROOT = Path(__file__).parents[2]
MAP = ROOT / "qa/parity-v4-live-execution-map.yaml"
CONTRACT = ROOT / "qa/parity-contract-v4.yaml"


def _traces() -> dict[str, tuple[str, ...]]:
    contract = load_v4_contract(CONTRACT)
    return {
        f"{row['source_pack']}/{row['source_item_id']}": tuple(row["expected_trace"])
        for row in contract["source_rows"]
    }


def test_generic_dispatcher_partition_and_all_120_packets_are_terminal(tmp_path: Path) -> None:
    live_map = load_v4_live_execution_map(MAP)
    catalog = build_v4_live_scenario_catalog(live_map, map_path=MAP)
    non_positive = tuple(
        scenario
        for scenario in catalog.scenarios
        if {"positive", "denial", "recovery"}.issubset(scenario.mandatory_paths)
    )
    generic = generic_v4_local_rows()
    assert len(non_positive) == 44
    assert len(generic) == 40
    assert sum(len(scenario.trial_indexes) * 2 for scenario in non_positive) == 136
    assert sum(
        len(scenario.trial_indexes) * 2
        for scenario in non_positive
        if scenario.row_key in generic
    ) == 120

    traces = _traces()
    observed = []
    for scenario in non_positive:
        if scenario.row_key not in generic:
            continue
        for trial_index in scenario.trial_indexes:
            for path in ("denial", "recovery"):
                packet = execute_v4_local_mechanism(
                    row_key=scenario.row_key,
                    trial_index=trial_index,
                    path=path,
                    task_root=tmp_path,
                )
                events, proofs = _local(
                    packet,
                    path,
                    traces[scenario.row_key],
                    expected_row_key=scenario.row_key,
                    expected_trial_index=trial_index,
                )
                assert packet["status"] == "PASS"
                assert packet["host_local"] is True
                assert packet["provider_calls"] == 0
                assert packet["terminal_status"] == ("denied" if path == "denial" else "completed")
                assert proofs == packet["proof_hashes"]
                host = packet["observation"]["operation"]["host"]
                assert host["denial_observed"] is True
                assert host["recovery_observed"] is (path == "recovery")
                assert host["successful_calls"] == (1 if path == "recovery" else 0)
                observed.append((scenario.row_key, trial_index, path, tuple(event["kind"] for event in events)))
    assert len(observed) == 120


def test_generic_dispatcher_is_deterministic_and_identity_bound(tmp_path: Path) -> None:
    row = "clawprobench_native/intel_m05_injection_resist"
    first = execute_v4_local_mechanism(row_key=row, trial_index=2, path="recovery", task_root=tmp_path)
    second = execute_v4_local_mechanism(row_key=row, trial_index=2, path="recovery", task_root=tmp_path)
    assert first == second
    tampered = copy.deepcopy(first)
    tampered["observation"]["identity"]["trial_index"] = 1
    with pytest.raises(Exception, match="identity|proof"):
        _local(
            tampered,
            "recovery",
            ("start", "terminal"),
            expected_row_key=row,
            expected_trial_index=2,
        )


@pytest.mark.parametrize(
    ("row_key", "trial_index", "path"),
    [
        ("clawprobench_native/constraints_23_external_approval_boundary_live", 1, "denial"),
        ("openclaw_active/config-restart-capability-flip", 1, "recovery"),
        ("openclaw_active/subagent-handoff", 1, "denial"),
        ("v2_non_soak/AUTH-01", 1, "denial"),
        ("clawprobench_native/intel_m05_injection_resist", 4, "recovery"),
        ("clawprobench_native/intel_m05_injection_resist", 1, "positive"),
    ],
)
def test_generic_dispatcher_rejects_special_unlisted_or_invalid_paths(
    tmp_path: Path, row_key: str, trial_index: int, path: str
) -> None:
    with pytest.raises(V4LocalMechanismExecutorViolation):
        execute_v4_local_mechanism(
            row_key=row_key,
            trial_index=trial_index,
            path=path,
            task_root=tmp_path,
        )
