from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity import v4_local_restart as restart
from hermes_claude_agent_sdk.parity.v4_live_packets import _local

ROW = "openclaw_active/config-restart-capability-flip"
TRACE = ("start", "restart", "terminal")

@pytest.mark.parametrize(
    ("path", "terminal"),
    (("denial", "denied"), ("recovery", "completed")),
)
def test_actual_task_local_gateway_restart_packet(tmp_path: Path, path: str, terminal: str) -> None:
    packet = restart.run_v4_local_restart(ROW, 1, path, tmp_path)

    events, proofs = _local(packet, path, TRACE)
    assert packet["status"] == "PASS"
    assert packet["host_local"] is True
    assert packet["provider_calls"] == 0
    assert packet["terminal_status"] == terminal
    assert tuple(event["kind"] for event in events) == TRACE
    assert proofs == packet["proof_hashes"]
    expected_methods = [
        "session.create",
        "session.title",
        "session.close",
        "session.resume",
    ]
    if path == "recovery":
        expected_methods.append("session.resume")
    assert packet["observation"]["rpc_methods"] == expected_methods
    terminal_observation = packet["observation"]["operations"]["terminal"]
    assert terminal_observation["stale_resume"]["ok"] is False
    if path == "recovery":
        assert terminal_observation["exact_resume"]["ok"] is True
    else:
        assert terminal_observation["exact_resume"] is None
    assert "session_id" not in repr(packet)
    assert "stored_session_id" not in repr(packet)
    assert "prompt.submit" not in repr(packet)


def test_restart_call_is_sealed_and_admits_only_mapped_trials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class NeverGateway:
        def __init__(self, **_: object) -> None:
            raise AssertionError("malformed admission started a gateway")

    monkeypatch.setattr(restart, "Gateway", NeverGateway)
    assert tuple(inspect.signature(restart.run_v4_local_restart).parameters) == (
        "row_key",
        "trial_index",
        "path",
        "task_root",
    )
    for trial in (1, 2, 3):
        with pytest.raises(restart.V4LocalRestartViolation):
            restart.run_v4_local_restart(ROW, trial, "positive", tmp_path)
    for value in (0, 4, True, "1"):
        with pytest.raises(restart.V4LocalRestartViolation):
            restart.run_v4_local_restart(ROW, value, "denial", tmp_path)  # type: ignore[arg-type]
    with pytest.raises(restart.V4LocalRestartViolation):
        restart.run_v4_local_restart("other/row", 1, "denial", tmp_path)
    with pytest.raises(restart.V4LocalRestartViolation):
        restart.run_v4_local_restart(ROW, 1, "denial", "relative-task-root")
    with pytest.raises(restart.V4LocalRestartViolation):
        restart.run_v4_local_restart(ROW, 1, "denial", Path.home())
    for value in (None, "relative-host", tmp_path / "missing-host", Path(__file__)):
        if value is None:
            monkeypatch.delenv("HERMES_AGENT_HOST_ROOT", raising=False)
        else:
            monkeypatch.setenv("HERMES_AGENT_HOST_ROOT", str(value))
        with pytest.raises(restart.V4LocalRestartViolation):
            restart.run_v4_local_restart(ROW, 1, "denial", tmp_path)
    with pytest.raises(TypeError):
        restart.run_v4_local_restart(ROW, 1, "denial", tmp_path, expected_trace=TRACE)  # type: ignore[call-arg]
