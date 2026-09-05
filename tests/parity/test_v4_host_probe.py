from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.v4_host_probe import (
    V4HostProbeViolation,
    collect_v4_delegation_observation,
    collect_v4_host_observation,
)
from hermes_claude_agent_sdk.parity.v4_live_packets import build_v4_live_packets

from .test_v4_live_packets import MAP, _inputs

SID, CALL_ID, CONTENT, CORRELATION = "synthetic-session-secret", "synthetic-call-secret", "synthetic private prompt", "synthetic-correlation-secret"


def _db(tmp_path: Path, *, provider: str = "anthropic", result_id: str = CALL_ID, duplicate_receipt: bool = False, turns: int = 1, correlation: str = CORRELATION, canonical_model: str | None = "claude-fable-5-1", api_call_count: int | None = None) -> tuple[Path, str]:
    path = tmp_path / "state.db"; conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, api_call_count INTEGER NOT NULL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL NOT NULL, finish_reason TEXT, active INTEGER NOT NULL, display_kind TEXT, display_metadata TEXT);
        CREATE TABLE runtime_session_state (session_id TEXT NOT NULL, runtime_id TEXT NOT NULL, schema_version INTEGER NOT NULL, state_json TEXT NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE runtime_usage_receipts (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, runtime_id TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, selected_model TEXT, effective_model TEXT, canonical_model TEXT, model_resolution TEXT NOT NULL, billing_mode TEXT NOT NULL, cost_status TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, cache_read_tokens INTEGER NOT NULL, cache_write_tokens INTEGER NOT NULL, reasoning_tokens INTEGER NOT NULL, replay_safe INTEGER NOT NULL, correlation_id TEXT, fallback_used INTEGER NOT NULL, failure_phase TEXT, recorded_at REAL NOT NULL);
        CREATE TABLE async_delegations (delegation_id TEXT, parent_session_id TEXT, state TEXT, delivery_state TEXT, delivery_attempts INTEGER);
    """)
    conn.execute("INSERT INTO sessions VALUES (?, ?)", (SID, turns if api_call_count is None else api_call_count))
    calls = json.dumps([{"id": CALL_ID, "type": "function", "function": {"name": "terminal", "arguments": "{}"}}])
    conn.executemany("INSERT INTO messages (id, session_id, role, content, tool_call_id, tool_calls, tool_name, timestamp, finish_reason, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [(1, SID, "user", CONTENT, None, None, None, 1.0, None, 1), (2, SID, "assistant", "working", None, calls, None, 2.0, "tool_calls", 1), (3, SID, "tool", "private tool result", result_id, None, "terminal", 3.0, None, 1), (4, SID, "assistant", "done", None, None, None, 4.0, "stop", 1)])
    for turn in range(1, turns):
        base = turn * 4 + 1
        conn.executemany("INSERT INTO messages (id, session_id, role, content, tool_call_id, tool_calls, tool_name, timestamp, finish_reason, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [(base, SID, "user", f"turn {turn}", None, None, None, float(base), None, 1), (base + 1, SID, "assistant", "done", None, None, None, float(base + 1), "stop", 1)])
    conn.execute("INSERT INTO runtime_session_state VALUES (?, ?, ?, ?, ?)", (SID, "hermes-claude-agent-sdk", 1, '{"lifecycle":"completed"}', 4.0))
    receipt = (1, SID, "hermes-claude-agent-sdk", provider, "claude-fable-5-1", "claude-fable-5-1", "claude-fable-5-1", canonical_model, "exact", "subscription_included", "included", 2, 3, 0, 0, 0, 0, correlation, 0, None, 4.0)
    for turn in range(turns):
        conn.execute("INSERT INTO runtime_usage_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (turn + 1, *receipt[1:17], f"{correlation}-{turn}" if turn else correlation, *receipt[18:]))
    if duplicate_receipt: conn.execute("INSERT INTO runtime_usage_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (turns + 1, *receipt[1:17], "other-correlation", *receipt[18:]))
    conn.commit(); conn.close(); return path, SID


def _delegation_db(
    tmp_path: Path,
    *,
    state: str = "completed",
    delivery_state: str = "delivered",
    delivery_attempts: int = 1,
    parent_id: str = SID,
    metadata: str | None = None,
    delivery_rows: int = 1,
) -> tuple[Path, str, str]:
    path, sid = _db(tmp_path)
    delegation_id = "synthetic-delegation-secret"
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO async_delegations VALUES (?, ?, ?, ?, ?)",
        (delegation_id, parent_id, state, delivery_state, delivery_attempts),
    )
    payload = metadata or json.dumps({
        "delegation_id": delegation_id,
        "task_count": 1,
        "completed_count": 1,
        "failed_count": 0,
    })
    for offset in range(delivery_rows):
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, timestamp, active, display_kind, display_metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (10 + offset, sid, "user", "private delivery summary", 10.0 + offset, 1, "async_delegation_complete", payload),
        )
    conn.commit(); conn.close()
    return path, sid, delegation_id


def test_collects_closed_sanitized_observation_without_writing(tmp_path: Path) -> None:
    path, sid = _db(tmp_path); before = os.stat(path).st_mtime_ns
    observation = collect_v4_host_observation(path, sid, allowed_root=tmp_path)
    assert os.stat(path).st_mtime_ns == before
    assert observation["status"] == "PASS"
    assert observation["transcript"]["row_count"] == 4
    assert {key: value["count"] for key, value in observation["transcript"]["canonical_rows"].items()} == {"user": 1, "assistant": 2, "tool_call": 1, "tool_result": 1}
    assert observation["transcript"]["ordering"]["strict"] is True
    assert observation["transcript"]["terminal"] == {"count": 1, "persisted": True, "sha256": observation["transcript"]["terminal"]["sha256"]}
    assert observation["runtime_state"] == {"present": True, "schema_version": 1, "sha256": observation["runtime_state"]["sha256"]}
    latest = observation["runtime_usage"]["latest"]
    assert latest["provider"] == "anthropic" and latest["model_resolution"] == "exact" and latest["fallback_used"] is False
    assert latest["tokens"] == {"input_tokens": 2, "output_tokens": 3, "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0}
    rendered = json.dumps(observation)
    assert all(secret not in rendered for secret in (sid, CALL_ID, CONTENT, CORRELATION))


@pytest.mark.parametrize("kwargs", [{"requested_fields": ("content",)}, {"include_raw": True}])
def test_rejects_raw_field_requests_without_echoing_identity(tmp_path: Path, kwargs: dict[str, object]) -> None:
    path, sid = _db(tmp_path)
    with pytest.raises(V4HostProbeViolation) as exc: collect_v4_host_observation(path, sid, allowed_root=tmp_path, **kwargs)
    assert all(secret not in str(exc.value) for secret in (sid, CALL_ID, CONTENT, CORRELATION))


@pytest.mark.parametrize("fixture", ["pairing", "receipt", "duplicate", "correlation"])
def test_rejects_pairing_or_receipt_drift_without_raw_error(tmp_path: Path, fixture: str) -> None:
    if fixture == "pairing": path, sid = _db(tmp_path, result_id="unmatched-result-secret")
    elif fixture == "receipt": path, sid = _db(tmp_path, provider="unknown-provider-secret")
    elif fixture == "correlation": path, sid = _db(tmp_path, correlation="")
    else: path, sid = _db(tmp_path, duplicate_receipt=True)
    with pytest.raises(V4HostProbeViolation) as exc: collect_v4_host_observation(path, sid, allowed_root=tmp_path)
    assert all(secret not in str(exc.value) for secret in (sid, CALL_ID, CONTENT, CORRELATION, "unknown-provider-secret"))


def test_rejects_symlink_and_outside_root(tmp_path: Path) -> None:
    path, sid = _db(tmp_path); link = tmp_path / "alias.db"; link.symlink_to(path)
    with pytest.raises(V4HostProbeViolation): collect_v4_host_observation(link, sid, allowed_root=tmp_path)
    outside = tmp_path.parent / "outside-state.db"; outside.write_bytes(path.read_bytes())
    try:
        with pytest.raises(V4HostProbeViolation): collect_v4_host_observation(outside, sid, allowed_root=tmp_path)
    finally:
        outside.unlink()


def test_two_turns_are_ordered_and_count_is_strict(tmp_path: Path) -> None:
    path, sid = _db(tmp_path, turns=2)
    observation = collect_v4_host_observation(path, sid, allowed_root=tmp_path, expected_turn_count=2)
    assert observation["expected_turn_count"] == 2 and observation["transcript"]["terminal"]["count"] == 2
    receipts = observation["runtime_usage"]["ordered"]
    assert len(receipts) == 2 and [item["ordinal"] for item in receipts] == [1, 2]
    assert all(set(item["correlation"]) == {"sha256", "byte_length"} for item in receipts)
    assert all(secret not in json.dumps(observation) for secret in (sid, CALL_ID, CONTENT, CORRELATION))
    with pytest.raises(V4HostProbeViolation): collect_v4_host_observation(path, sid, allowed_root=tmp_path)


def test_hermes_owned_async_delivery_is_counted_as_visible_parent_turn(tmp_path: Path) -> None:
    path, sid = _db(tmp_path, turns=2)
    conn = sqlite3.connect(path)
    conn.execute(
        "UPDATE messages SET display_kind=? WHERE session_id=? AND id=?",
        ("async_delegation_complete", sid, 5),
    )
    conn.commit()
    conn.close()
    observation = collect_v4_host_observation(
        path,
        sid,
        allowed_root=tmp_path,
        expected_turn_count=2,
    )
    assert observation["transcript"]["canonical_rows"]["user"]["count"] == 2
    assert observation["transcript"]["terminal"]["count"] == 2
    assert observation["runtime_usage"]["receipt_count"] == 2


@pytest.mark.parametrize(
    "kwargs",
    ({"canonical_model": None}, {"turns": 2, "api_call_count": 1}),
)
def test_rejects_missing_canonical_model_or_inexact_session_call_count(tmp_path: Path, kwargs: dict[str, object]) -> None:
    path, sid = _db(tmp_path, **kwargs)
    with pytest.raises(V4HostProbeViolation):
        collect_v4_host_observation(
            path,
            sid,
            allowed_root=tmp_path,
            expected_turn_count=int(kwargs.get("turns", 1)),
        )


def test_host_projection_binds_through_live_packet_validator(tmp_path: Path) -> None:
    contract, live_map, _, scenario, receipt = _inputs()
    path, sid = _db(tmp_path, turns=scenario.turn_count)
    receipt["host_observation"] = collect_v4_host_observation(
        path,
        sid,
        allowed_root=tmp_path,
        expected_turn_count=scenario.turn_count,
    )
    bundle = build_v4_live_packets(
        contract,
        scenario,
        receipt,
        live_map=live_map,
        map_path=MAP,
    )
    assert bundle["scenario_receipt"]["provider_accounting"]["positive_calls"] == scenario.turn_count


def test_collects_closed_sanitized_durable_delegation_observation(tmp_path: Path) -> None:
    path, sid, delegation_id = _delegation_db(tmp_path)
    before = os.stat(path).st_mtime_ns
    observation = collect_v4_delegation_observation(
        path, sid, allowed_root=tmp_path, expected_count=1
    )
    assert os.stat(path).st_mtime_ns == before
    assert set(observation) == {
        "schema_version", "status", "count", "background_count", "lifecycle",
        "delivery_state", "delivery_attempts", "parent_link_sha256",
        "parent_delivery_count", "parent_delivery_sha256", "invariant_violations",
    }
    assert observation["status"] == "PASS"
    assert observation["count"] == observation["background_count"] == 1
    assert observation["lifecycle"] == "completed"
    assert observation["delivery_state"] == "delivered"
    assert observation["delivery_attempts"] == [1]
    assert observation["parent_delivery_count"] == 1
    assert len(observation["parent_link_sha256"]) == 64
    assert len(observation["parent_delivery_sha256"]) == 64
    assert observation["invariant_violations"] == []
    rendered = json.dumps(observation)
    assert all(secret not in rendered for secret in (sid, delegation_id))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"state": "unknown"},
        {"metadata": "not-json"},
        {"parent_id": "foreign-parent-secret"},
        {"delivery_rows": 2},
    ],
)
def test_delegation_observation_rejects_ambiguous_or_foreign_rows(
    tmp_path: Path, kwargs: dict[str, object]
) -> None:
    path, sid, delegation_id = _delegation_db(tmp_path, **kwargs)
    with pytest.raises(V4HostProbeViolation) as exc:
        collect_v4_delegation_observation(path, sid, allowed_root=tmp_path)
    assert all(secret not in str(exc.value) for secret in (sid, delegation_id, "foreign-parent-secret"))


def test_delegation_observation_rejects_expected_count_drift(tmp_path: Path) -> None:
    path, sid, delegation_id = _delegation_db(tmp_path)
    with pytest.raises(V4HostProbeViolation) as exc:
        collect_v4_delegation_observation(path, sid, allowed_root=tmp_path, expected_count=2)
    assert delegation_id not in str(exc.value)
