from __future__ import annotations
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from hermes_claude_agent_sdk.parity.hashing import sha256_value
from hermes_claude_agent_sdk.parity.v4_contract import (
    OWNERSHIP_PREFLIGHTS,
    V4_CLI_VERSION,
    V4_MODEL,
    V4_RUNNER_ID,
    V4_RUNNER_VERSION,
    V4_SDK_DISTRIBUTION,
    V4_SDK_VERSION,
)
from hermes_claude_agent_sdk.parity.v4_gateway import Gateway
from hermes_claude_agent_sdk.parity.v4_live_executor import (
    V4LiveExecutor,
    V4LiveExecutorViolation,
)
from hermes_claude_agent_sdk.parity.v4_live_map import load_v4_live_execution_map

ROOT = Path(__file__).parents[2]
MAP_PATH = ROOT / "qa" / "parity-v4-live-execution-map.yaml"
def _candidate() -> dict[str, str]:
    return {
        "plugin_sha": "1" * 40,
        "host_sha": "2" * 40,
        "wheel_sha256": "3" * 64,
        "profile_sha256": "4" * 64,
        "sdk_distribution": V4_SDK_DISTRIBUTION,
        "sdk_version": V4_SDK_VERSION,
        "cli_version": V4_CLI_VERSION,
        "model": V4_MODEL,
        "runner_id": V4_RUNNER_ID,
        "runner_version": V4_RUNNER_VERSION,
    }
def _event(kind: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": kind, "payload": payload or {}},
    }
def _preflights(candidate: dict[str, str]) -> dict[str, dict[str, object]]:
    digest = sha256_value(candidate)
    return {
        name: {
            "schema_version": 1,
            "name": name,
            "candidate_hash": digest,
            "status": "PASS",
            "source": {
                "executable": "pytest",
                "source_ref": "tests/parity/fixture.py",
                "test_id": "fixture:pass",
            },
            "observation": {"exit_status": 0, "passed_count": 1},
        }
        for name in OWNERSHIP_PREFLIGHTS
    }
class _FakeTransport:
    """In-memory transport; only safe call metadata is recorded."""

    def __init__(self, events: list[dict[str, object]]) -> None:
        self._events, self._ready = deque(events), True
        self._session_id = sha256_value({"fixture": "session"})
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False
        self.provider_calls = 0

    def start(self) -> None:
        return None

    def send(self, frame: dict[str, object]) -> dict[str, object]:
        method, params = frame["method"], frame.get("params", {})
        assert isinstance(method, str) and isinstance(params, dict)
        safe = {"keys": sorted(params)}
        if isinstance(params.get("text"), str):
            safe.update({"text_bytes": len(params["text"].encode()), "text_sha256": sha256_value(params["text"])})
        self.calls.append((method, safe))
        result = {"session_id": self._session_id} if method == "session.create" else {"status": "streaming"}
        return {"jsonrpc": "2.0", "id": frame["id"], "result": result}

    def recv(self, _: float) -> dict[str, object]:
        if self._ready:
            self._ready = False
            return _event("gateway.ready")
        if not self._events:
            raise TimeoutError
        return self._events.popleft()

    def close(self) -> None:
        self.closed = True

def _executor(fake: _FakeTransport, *, path: str = "positive", trial_index: int = 1, **kwargs: Any) -> V4LiveExecutor:
    candidate = _candidate()
    document = load_v4_live_execution_map(MAP_PATH)
    row = next(item for item in document["rows"] if item["source_pack"] == "v2_non_soak" and item["source_item_id"] == "AUTH-01")
    return V4LiveExecutor(
        gateway=Gateway(python="fake-python", cwd=ROOT, env={}, transport=fake),
        candidate=candidate,
        preflight_projections=_preflights(candidate),
        live_map=document,
        map_path=MAP_PATH,
        source_pack=row["source_pack"],
        source_item_id=row["source_item_id"],
        path=path,
        trial_index=trial_index,
        **kwargs,
    )


def test_executor_drives_normal_gateway_sequence_and_safe_receipt() -> None:
    approval_token = sha256_value({"fixture": "approval"})
    fake = _FakeTransport(
        [
            _event("message.start"),
            _event("approval.requested", {"approval_id": approval_token}),
            _event("message.delta", {"content": "never-return"}),
            _event("message.complete", {"status": "completed"}),
        ]
    )
    prompt = "fixture prompt must remain ephemeral"
    receipt = _executor(fake, planned_calls=1).run(prompt, approval_choice="deny")

    assert [method for method, _ in fake.calls if method != "start"] == [
        "session.create",
        "prompt.submit",
        "approval.respond",
    ]
    assert receipt["classification"] == "COMPLETE"
    assert receipt["terminal_status"] == "completed"
    assert receipt["provider_calls"] == 1
    assert receipt["control_calls_used"] == 3 and fake.provider_calls == 0
    assert receipt["approval"] == {"decision_class": "deny", "decision_count": 1}
    assert receipt["event_count"] == 4
    assert receipt["event_kinds"]["approval.requested"] == 1
    assert prompt not in repr(receipt)
    assert approval_token not in repr(receipt)
    assert fake.closed

def test_admission_rejects_budget_and_identity_before_gateway_start() -> None:
    fake = _FakeTransport([_event("message.complete", {"status": "completed"})])
    with pytest.raises(V4LiveExecutorViolation):
        _executor(fake, planned_calls=0)
    with pytest.raises(V4LiveExecutorViolation):
        _executor(fake, planned_calls=137)
    assert fake.calls == []

    candidate = _candidate()
    bad_preflights = _preflights(candidate)
    bad_preflights[OWNERSHIP_PREFLIGHTS[0]]["candidate_hash"] = "f" * 64
    with pytest.raises(V4LiveExecutorViolation):
        V4LiveExecutor(
            gateway=Gateway(python="fake-python", cwd=ROOT, env={}, transport=fake),
            candidate=candidate,
            preflight_projections=bad_preflights,
            live_map=load_v4_live_execution_map(MAP_PATH),
            map_path=MAP_PATH,
            source_pack="v2_non_soak",
            source_item_id="AUTH-01",
            path="positive",
            trial_index=1,
        )
    assert fake.calls == []


def test_executor_rejects_terminal_duplicates_and_post_terminal_events() -> None:
    duplicate = _FakeTransport(
        [_event("message.complete", {"status": "completed"}), _event("terminal", {"status": "completed"})]
    )
    with pytest.raises(V4LiveExecutorViolation):
        _executor(duplicate).run("fixture")
    assert duplicate.closed

    trailing = _FakeTransport(
        [_event("message.complete", {"status": "completed"}), _event("message.delta")]
    )
    with pytest.raises(V4LiveExecutorViolation):
        _executor(trailing).run("fixture")
    missing = _FakeTransport([_event("approval.requested"), _event("message.complete", {"status": "completed"})])
    with pytest.raises(V4LiveExecutorViolation): _executor(missing).run("fixture")
    assert [method for method, _ in missing.calls] == ["session.create", "prompt.submit"]


def test_executor_is_single_use_and_rejects_unsafe_receipt_inputs() -> None:
    fake = _FakeTransport([_event("message.complete", {"status": "denied"})])
    executor = _executor(fake)
    receipt = executor.run("fixture")
    assert receipt["classification"] == "EXPECTED_NEGATIVE"
    with pytest.raises(V4LiveExecutorViolation):
        executor.run("second")

    candidate = _candidate()
    projections = _preflights(candidate)
    projections[OWNERSHIP_PREFLIGHTS[0]]["observation"] = {"raw_prompt": "forbidden"}
    with pytest.raises(V4LiveExecutorViolation):
        V4LiveExecutor(
            gateway=Gateway(python="fake-python", cwd=ROOT, env={}, transport=_FakeTransport([])),
            candidate=candidate,
            preflight_projections=projections,
            live_map=load_v4_live_execution_map(MAP_PATH),
            map_path=MAP_PATH,
            source_pack="v2_non_soak",
            source_item_id="AUTH-01",
            path="positive",
            trial_index=1,
        )
