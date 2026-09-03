"""Provider-free denial/recovery receipts over Hermes async delegation."""
from __future__ import annotations

import json
import os
import builtins
import sys
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.v4_background_delivery_receipt import (
    V4BackgroundDeliveryViolation,
    run_v4_background_delivery_receipt,
)
from hermes_claude_agent_sdk.parity.v4_live_packets import _local

if not os.environ.get("HERMES_AGENT_HOST_ROOT") or not Path(os.environ["HERMES_AGENT_HOST_ROOT"]).is_dir():
    pytest.skip("HERMES_AGENT_HOST_ROOT is not configured", allow_module_level=True)

_FIELDS = {"schema_version", "status", "path", "host_local", "provider_calls", "terminal_status", "events", "observation", "proof_hashes"}


def _receipt(tmp_path: Path, row: str, path: str) -> dict:
    value = run_v4_background_delivery_receipt(row, 1, path, tmp_path)
    assert set(value) == _FIELDS
    assert value["status"] == "PASS" and value["host_local"] is True
    assert value["provider_calls"] == 0
    assert [event["kind"] for event in value["events"]] == ["start", "background", "terminal"]
    assert all(len(event["sha256"]) == 64 for event in value["events"])
    assert all(len(value["proof_hashes"][key]) == 64 for key in ("primary", "secondary"))
    return value


def test_one_child_delivery_denial_is_pending_and_local_compatible(tmp_path: Path) -> None:
    value = _receipt(tmp_path, "openclaw_active/subagent-handoff", "denial")
    assert value["terminal_status"] == "denied"
    assert value["observation"] == {
        "batch": {"durable_rows": 1, "child_count": 1, "is_batch": True},
        "producer": {"state": "completed", "child_count": 1},
        "delivery": {"state": "pending", "attempts": 1, "parent_rows": 0, "transitions": [{"phase": "denial", "state": "pending", "attempts": 1}]},
    }
    events, _ = _local(value, "denial", ("start", "background", "terminal"))
    assert events[-1]["terminal_outcome"] == "denied"


def test_one_child_positive_delivery_is_completed(tmp_path: Path) -> None:
    value = _receipt(tmp_path, "openclaw_active/subagent-handoff", "positive")
    assert value["terminal_status"] == "completed"
    assert value["observation"]["delivery"] == {
        "state": "delivered", "attempts": 1, "parent_rows": 1,
        "transitions": [{"phase": "delivery", "state": "delivered", "attempts": 1, "parent_rows": 1}],
    }
    events, _ = _local(value, "positive", ("start", "background", "terminal"))
    assert events[-1]["terminal_outcome"] == "completed"


def test_manager_restores_none_after_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hermes_cli import plugins as plugins_mod
    monkeypatch.setattr(plugins_mod, "_plugin_manager", None)
    _receipt(tmp_path, "openclaw_active/subagent-handoff", "positive")
    assert plugins_mod._plugin_manager is None


def test_setup_failure_restores_process_global_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before_path = list(sys.path)
    before_env = {
        key: os.environ.get(key)
        for key in ("HERMES_HOME", "HERMES_BUNDLED_PLUGINS", "HERMES_INTERACTIVE")
    }
    real_import = builtins.__import__

    def fail_gateway_import(name, *args, **kwargs):
        if name == "gateway.wake":
            raise RuntimeError("synthetic setup failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_gateway_import)
    with pytest.raises(RuntimeError, match="synthetic setup failure"):
        run_v4_background_delivery_receipt(
            "openclaw_active/subagent-handoff", 1, "positive", tmp_path
        )
    assert sys.path == before_path
    assert {
        key: os.environ.get(key)
        for key in ("HERMES_HOME", "HERMES_BUNDLED_PLUGINS", "HERMES_INTERACTIVE")
    } == before_env


def test_fanout_recovery_is_one_batch_and_one_parent_delivery(tmp_path: Path) -> None:
    value = _receipt(tmp_path, "openclaw_active/subagent-fanout-synthesis", "recovery")
    assert value["terminal_status"] == "completed"
    batch = value["observation"]["batch"]
    delivery = value["observation"]["delivery"]
    assert batch == {"durable_rows": 1, "child_count": 2, "is_batch": True}
    assert delivery["state"] == "delivered" and delivery["attempts"] == 2 and delivery["parent_rows"] == 1
    assert [step["phase"] for step in delivery["transitions"]] == ["denial", "delivery"]
    events, _ = _local(value, "recovery", ("start", "background", "terminal"))
    assert events[-1]["terminal_outcome"] == "completed"


@pytest.mark.parametrize(
    "args",
    [
        ("unsupported/row", 1, "denial"),
        ("openclaw_active/subagent-handoff", 2, "denial"),
        ("openclaw_active/subagent-handoff", 1, "positive"),
    ],
)
def test_sealed_input_rejects_unsupported_identity_or_path(tmp_path: Path, args: tuple[object, ...]) -> None:
    if args[2] == "positive":
        # Positive is admitted; use a malformed trial to keep this table focused
        # on rejection without invoking the host.
        args = (args[0], 0, args[2])
    with pytest.raises(V4BackgroundDeliveryViolation):
        run_v4_background_delivery_receipt(*args, task_root=tmp_path)


def test_receipt_has_no_raw_or_caller_proof_fields(tmp_path: Path) -> None:
    value = _receipt(tmp_path, "openclaw_active/subagent-handoff", "recovery")
    rendered = json.dumps(value, sort_keys=True)
    assert all(name not in rendered for name in ("raw_prompt", "raw_content", "session_id", "delegation_id", "request_id"))
