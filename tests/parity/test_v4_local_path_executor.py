from __future__ import annotations

from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.hashing import sha256_file
from hermes_claude_agent_sdk.parity.v4_contract import load_v4_contract
from hermes_claude_agent_sdk.parity.v4_live_packets import _local
from hermes_claude_agent_sdk.parity.v4_local_path_executor import (
    V4LocalPathExecutorViolation,
    execute_v4_local_path,
)


def _run(tmp_path: Path, row: str, path: str = "positive") -> dict:
    return execute_v4_local_path(row_key=row, trial_index=1, path=path, task_root=tmp_path)


def _expected_trace(row_key: str) -> tuple[str, ...]:
    contract_path = Path(__file__).parents[2] / "qa/parity-contract-v4.yaml"
    assert sha256_file(contract_path) == "53864834496403388f3475291475fea70acfa3105609ad49f5edf75ad1c67d94"
    contract = load_v4_contract(contract_path)
    row = next(row for row in contract["source_rows"] if f"{row['source_pack']}/{row['source_item_id']}" == row_key)
    return tuple(row["expected_trace"])


def _assert_local(packet: dict, path: str, trace: tuple[str, ...]) -> None:
    assert set(packet) == {"schema_version", "status", "path", "host_local", "provider_calls", "terminal_status", "events", "observation", "proof_hashes"}
    _local(packet, path, trace)
    assert packet["host_local"] is True and packet["provider_calls"] == 0


def test_tool_path_observes_real_host_request_and_result(tmp_path: Path) -> None:
    packet = _run(tmp_path, "v2_non_soak/TOOL-02")
    _assert_local(packet, "positive", _expected_trace("v2_non_soak/TOOL-02"))
    assert packet["observation"]["tool"] == {"request_count": 1, "result_count": 1}
    assert "v4 local runtime fixture" not in repr(packet)
    assert "v4-local-record-1" not in repr(packet)


def test_approval_denial_is_negative_and_does_not_write(tmp_path: Path) -> None:
    packet = _run(tmp_path, "clawprobench_native/constraints_23_external_approval_boundary_live", "denial")
    _assert_local(packet, "denial", _expected_trace("clawprobench_native/constraints_23_external_approval_boundary_live"))
    assert packet["terminal_status"] == "denied"
    assert packet["observation"]["tool"] == {"request_count": 1, "result_count": 1}
    assert packet["observation"]["approval"]["decisions"] == ["deny"]
    assert not (tmp_path / ".v4_local_runtime_fixture_state.json").exists()


def test_approval_recovery_retains_denial_then_writes_once(tmp_path: Path) -> None:
    packet = _run(tmp_path, "clawprobench_native/constraints_23_external_approval_boundary_live", "recovery")
    _assert_local(packet, "recovery", _expected_trace("clawprobench_native/constraints_23_external_approval_boundary_live"))
    assert packet["observation"]["prior_denial"] == {"observed": True, "no_write": True}
    assert packet["observation"]["single_write_on_recovery"] is True
    assert packet["observation"]["approval"]["decisions"] == ["deny", "once"]
    assert packet["observation"]["tool"] == {"request_count": 2, "result_count": 2}


def test_host_binding_is_required_and_source_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_AGENT_HOST_ROOT", raising=False)
    with pytest.raises(V4LocalPathExecutorViolation):
        _run(tmp_path, "v2_non_soak/TOOL-02")
    monkeypatch.setenv("HERMES_AGENT_HOST_ROOT", str(tmp_path))
    with pytest.raises(V4LocalPathExecutorViolation):
        _run(tmp_path, "v2_non_soak/TOOL-02")


def test_inputs_and_unmapped_rows_are_rejected_before_host(tmp_path: Path) -> None:
    with pytest.raises(V4LocalPathExecutorViolation):
        _run(tmp_path, "v2_non_soak/AUTH-01")
    with pytest.raises(V4LocalPathExecutorViolation):
        _run(tmp_path, "v2_non_soak/PARENT-01")
    with pytest.raises(V4LocalPathExecutorViolation):
        _run(tmp_path, "openclaw_active/source-docs-discovery-report")
    with pytest.raises(V4LocalPathExecutorViolation):
        execute_v4_local_path(row_key="v2_non_soak/TOOL-02", trial_index=4, path="positive", task_root=tmp_path)
    with pytest.raises(V4LocalPathExecutorViolation):
        execute_v4_local_path(row_key="v2_non_soak/TOOL-02", trial_index=1, path="denial", task_root=tmp_path)
    with pytest.raises(TypeError):
        execute_v4_local_path(row_key="v2_non_soak/PARENT-01", trial_index=1, path="positive", task_root=tmp_path, expected_trace=())
