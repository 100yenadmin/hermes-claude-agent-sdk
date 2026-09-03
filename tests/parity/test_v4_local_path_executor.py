from __future__ import annotations

from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.v4_live_packets import _local
from hermes_claude_agent_sdk.parity.v4_local_path_executor import (
    V4LocalPathExecutorViolation,
    execute_v4_local_path,
)


def _run(tmp_path: Path, row: str, path: str = "positive") -> dict:
    return execute_v4_local_path(row_key=row, trial_index=1, path=path, task_root=tmp_path)


def _assert_local(packet: dict, path: str, trace: tuple[str, ...]) -> None:
    assert set(packet) == {"schema_version", "status", "path", "host_local", "provider_calls", "terminal_status", "events", "observation", "proof_hashes"}
    _local(packet, path, trace)
    assert packet["host_local"] is True and packet["provider_calls"] == 0


def test_basic_start_terminal_is_observed_and_closed(tmp_path: Path) -> None:
    packet = _run(tmp_path, "openclaw_active/source-docs-discovery-report")
    _assert_local(packet, "positive", ("start", "terminal"))
    assert packet["terminal_status"] == "completed"


def test_state_path_uses_before_after_fixture_snapshot(tmp_path: Path) -> None:
    packet = _run(tmp_path, "v2_non_soak/PARENT-01")
    _assert_local(packet, "positive", ("start", "state", "terminal"))
    assert packet["observation"]["state_before"]["present"] is False
    assert packet["observation"]["state_after"]["present"] is True


def test_tool_path_observes_real_host_request_and_result(tmp_path: Path) -> None:
    packet = _run(tmp_path, "v2_non_soak/TOOL-02")
    _assert_local(packet, "positive", ("start", "tool_requested", "tool_result", "state", "terminal"))
    assert packet["observation"]["tool"] == {"request_count": 1, "result_count": 1}
    assert "v4 local runtime fixture" not in repr(packet)
    assert "v4-local-record-1" not in repr(packet)


def test_approval_denial_is_negative_and_does_not_write(tmp_path: Path) -> None:
    packet = _run(tmp_path, "clawprobench_native/constraints_23_external_approval_boundary_live", "denial")
    _assert_local(packet, "denial", ("start", "approval_requested", "approval_decision", "terminal"))
    assert packet["terminal_status"] == "denied"
    assert packet["observation"]["approval"]["decisions"] == ["deny"]
    assert not (tmp_path / ".v4_local_runtime_fixture_state.json").exists()


def test_approval_recovery_retains_denial_then_writes_once(tmp_path: Path) -> None:
    packet = _run(tmp_path, "clawprobench_native/constraints_23_external_approval_boundary_live", "recovery")
    _assert_local(packet, "recovery", ("start", "approval_requested", "approval_decision", "terminal"))
    assert packet["observation"]["prior_denial"] == {"observed": True, "no_write": True}
    assert packet["observation"]["single_write_on_recovery"] is True
    assert packet["observation"]["approval"]["decisions"] == ["deny", "once"]


def test_inputs_and_unmapped_rows_are_rejected_before_host(tmp_path: Path) -> None:
    with pytest.raises(V4LocalPathExecutorViolation):
        _run(tmp_path, "v2_non_soak/AUTH-01")
    with pytest.raises(V4LocalPathExecutorViolation):
        execute_v4_local_path(row_key="v2_non_soak/PARENT-01", trial_index=2, path="positive", task_root=tmp_path)
    with pytest.raises(V4LocalPathExecutorViolation):
        execute_v4_local_path(row_key="v2_non_soak/PARENT-01", trial_index=1, path="denial", task_root=tmp_path)
    with pytest.raises(TypeError):
        execute_v4_local_path(row_key="v2_non_soak/PARENT-01", trial_index=1, path="positive", task_root=tmp_path, expected_trace=())
