from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.v4_host_probe import V4HostProbeViolation, collect_v4_host_observation

SID, CALL_ID, CONTENT, CORRELATION = "synthetic-session-secret", "synthetic-call-secret", "synthetic private prompt", "synthetic-correlation-secret"


def _db(tmp_path: Path, *, provider: str = "anthropic", result_id: str = CALL_ID, duplicate_receipt: bool = False, turns: int = 1, correlation: str = CORRELATION) -> tuple[Path, str]:
    path = tmp_path / "state.db"; conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, api_call_count INTEGER NOT NULL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL NOT NULL, finish_reason TEXT, active INTEGER NOT NULL);
        CREATE TABLE runtime_session_state (session_id TEXT NOT NULL, runtime_id TEXT NOT NULL, schema_version INTEGER NOT NULL, state_json TEXT NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE runtime_usage_receipts (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, runtime_id TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, selected_model TEXT, effective_model TEXT, canonical_model TEXT, model_resolution TEXT NOT NULL, billing_mode TEXT NOT NULL, cost_status TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, cache_read_tokens INTEGER NOT NULL, cache_write_tokens INTEGER NOT NULL, reasoning_tokens INTEGER NOT NULL, replay_safe INTEGER NOT NULL, correlation_id TEXT, fallback_used INTEGER NOT NULL, failure_phase TEXT, recorded_at REAL NOT NULL);
    """)
    conn.execute("INSERT INTO sessions VALUES (?, ?)", (SID, 1))
    calls = json.dumps([{"id": CALL_ID, "type": "function", "function": {"name": "terminal", "arguments": "{}"}}])
    conn.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [(1, SID, "user", CONTENT, None, None, None, 1.0, None, 1), (2, SID, "assistant", "working", None, calls, None, 2.0, "tool_calls", 1), (3, SID, "tool", "private tool result", result_id, None, "terminal", 3.0, None, 1), (4, SID, "assistant", "done", None, None, None, 4.0, "stop", 1)])
    for turn in range(1, turns):
        base = turn * 4 + 1
        conn.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [(base, SID, "user", f"turn {turn}", None, None, None, float(base), None, 1), (base + 1, SID, "assistant", "done", None, None, None, float(base + 1), "stop", 1)])
    conn.execute("INSERT INTO runtime_session_state VALUES (?, ?, ?, ?, ?)", (SID, "hermes-claude-agent-sdk", 1, '{"lifecycle":"completed"}', 4.0))
    receipt = (1, SID, "hermes-claude-agent-sdk", provider, "claude-fable-5-1", "claude-fable-5-1", "claude-fable-5-1", None, "exact", "subscription_included", "included", 2, 3, 0, 0, 0, 0, correlation, 0, None, 4.0)
    for turn in range(turns):
        conn.execute("INSERT INTO runtime_usage_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (turn + 1, *receipt[1:17], f"{correlation}-{turn}" if turn else correlation, *receipt[18:]))
    if duplicate_receipt: conn.execute("INSERT INTO runtime_usage_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (turns + 1, *receipt[1:17], "other-correlation", *receipt[18:]))
    conn.commit(); conn.close(); return path, SID


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
