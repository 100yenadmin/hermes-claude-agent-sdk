from __future__ import annotations

from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.hashing import sha256_value
from hermes_claude_agent_sdk.parity.results import ResultPacket, candidate_hash
from hermes_claude_agent_sdk.parity.v4_contract import OWNERSHIP_PREFLIGHTS, load_v4_contract
from hermes_claude_agent_sdk.parity.v4_live_map import load_v4_live_execution_map
from hermes_claude_agent_sdk.parity.v4_live_packets import V4LivePacketViolation, build_v4_live_packets

ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "qa" / "parity-contract-v4.yaml"
MAP_PATH = ROOT / "qa" / "parity-v4-live-execution-map.yaml"


def _candidate() -> dict[str, str]:
    return {"plugin_sha": "1" * 40, "host_sha": "2" * 40, "wheel_sha256": "3" * 64, "profile_sha256": "4" * 64, "sdk_distribution": "claude-agent-sdk", "sdk_version": "0.2.151", "cli_version": "2.1.258", "model": "claude-fable-5-1", "runner_id": "hermes-parity-v4", "runner_version": "4.0.0"}


def _inputs(*, row_id: str = "AUTH-01", path: str = "positive", classification: str = "COMPLETE") -> tuple[dict, dict, dict, dict]:
    contract, live_map, candidate = load_v4_contract(CONTRACT_PATH), load_v4_live_execution_map(MAP_PATH), _candidate()
    row = next(item for item in live_map["rows"] if item["source_item_id"] == row_id)
    v4_hash = sha256_value(candidate)
    identity = {"candidate_hash": v4_hash, "preflight_hash": "0" * 64, "live_map_sha256": "16a9e8e3bb2a540b74c2b070b2b84f8d0d588778b615c4b5f91d3597a407140b", "row_key": f"{row['source_pack']}/{row_id}", "predecessor_execution_id": row["predecessor_execution_id"], "path": path, "trial_index": 1}
    types = {"start": "message.start", "state": "message.state", "usage": "message.usage", "approval_requested": "approval.requested", "approval_decision": "approval.responded", "tool_requested": "tool.start", "tool_result": "tool.complete", "compaction": "compaction", "background": "background", "restart": "restart", "terminal": "message.complete"}
    events = [{"kind": types[kind], "byte_length": 20 + index, "sha256": f"{index + 1:064x}", "terminal_status": ("denied" if path == "denial" else "completed") if kind == "terminal" else None} for index, kind in enumerate(row["mandatory_paths"] and next(item["expected_trace"] for item in contract["source_rows"] if item["source_item_id"] == row_id), 1)]
    events[-1]["sha256"] = "f" * 64
    attempt = {"identity": identity, "candidate": candidate, "classification": classification, "terminal_status": events[-1]["terminal_status"], "event_count": len(events), "event_kinds": {event["kind"]: sum(other["kind"] == event["kind"] for other in events) for event in events}, "events": events, "control_calls_used": 3, "provider_calls": 1, "turns_used": 1, "approval": {"decision_class": "deny", "decision_count": 0}}
    preflights = {name: {"schema_version": 1, "name": name, "candidate_hash": v4_hash, "status": "PASS", "source": {"executable": "pytest", "source_ref": "tests/parity/fixture.py", "test_id": f"fixture:{name}"}, "observation": {"exit_status": 0, "passed_count": 1}} for name in OWNERSHIP_PREFLIGHTS}
    identities = {name: {"candidate_hash": v4_hash, "status": "PASS", "source_hash": sha256_value(item["source"]), "observation_hash": sha256_value(item["observation"])} for name, item in preflights.items()}
    identity["preflight_hash"] = sha256_value(identities); attempt["identity"] = identity
    trial_hash = candidate_hash(catalog_hash="768c2d8f99077f8557a192d1053fc80401e83dee80d77475d12119df75b63abb", plugin_sha=candidate["plugin_sha"], host_sha=candidate["host_sha"], sdk_version=candidate["sdk_version"], profile_hash=candidate["profile_sha256"], runner_version=candidate["runner_version"], inventory_hash="5" * 64)
    host = {"identity": dict(identity), "runtime": "claude-agent-sdk", "provider": "anthropic", "effective_model": "claude-fable-5-1", "canonical_model": "claude-fable-5-1", "billing_mode": "subscription_included", "cost_status": "included", "fallback_used": False, "api_calls": 1, "api_call_budget": 1, "tool_request_count": sum(event["kind"] in {"tool.start", "tool.request", "tool.requested"} for event in events), "tool_result_count": sum(event["kind"] in {"tool.complete", "tool.result", "tool.completed"} for event in events), "transcript_count": 1, "terminal_count": 1, "invariant_violations": [], "state_hash": "6" * 64, "profile_id": "isolated", "inventory_hash": "5" * 64, "proof_hashes": {"primary": "7" * 64, "secondary": "8" * 64}, "preflight_projections": preflights, "stream_projection": {"schema_version": 1, "name": "stream", "candidate_hash": v4_hash, "trial_candidate_hash": trial_hash, "trial_index": 1, "status": "PASS", "source": {"executable": "pytest", "source_ref": "tests/parity/stream.py", "test_id": "stream:trial"}, "observation": {"exit_status": 0, "chunk_count": 1, "event_count": len(events), "content_hash": "9" * 64}}}
    return contract, live_map, attempt, host


def test_builds_v3_trial_receipt_and_bound_v4_packet() -> None:
    contract, live_map, attempt, host = _inputs()
    bundle = build_v4_live_packets(contract, live_map, attempt, host, map_path=MAP_PATH)
    assert isinstance(bundle["trial"], ResultPacket)
    assert bundle["trial"].catalog_hash == "768c2d8f99077f8557a192d1053fc80401e83dee80d77475d12119df75b63abb"
    assert bundle["trial"].primary_proof_hash == host["proof_hashes"]["primary"]
    assert bundle["ownership_receipt"]["trial_index"] == 1
    assert bundle["packet"]["source_item_id"] == "AUTH-01" and bundle["packet"]["path"] == "positive"
    assert bundle["packet"]["candidate_hash"] == bundle["ownership_receipt"]["candidate_hash"]


def test_builds_expected_negative_denial() -> None:
    contract, live_map, attempt, host = _inputs(row_id="source-docs-discovery-report", path="denial", classification="EXPECTED_NEGATIVE")
    bundle = build_v4_live_packets(contract, live_map, attempt, host, map_path=MAP_PATH)
    assert bundle["trial"].classification.value == "EXPECTED_NEGATIVE"
    assert bundle["packet"]["classification"] == "EXPECTED_NEGATIVE"


@pytest.mark.parametrize("change", [lambda host: host["identity"].update(path="recovery"), lambda host: host.update(billing_mode="unknown"), lambda host: host.update(cost_status="unknown"), lambda host: host.update(transcript_count=0), lambda host: host.update(terminal_count=0), lambda host: host.update(api_calls=2), lambda host: host.update(invariant_violations=["violation"])])
def test_rejects_mismatched_or_unsafe_host_observation(change) -> None:
    contract, live_map, attempt, host = _inputs()
    change(host)
    with pytest.raises(V4LivePacketViolation): build_v4_live_packets(contract, live_map, attempt, host, map_path=MAP_PATH)


def test_rejects_reused_event_projection_and_missing_tool_pair() -> None:
    contract, live_map, attempt, host = _inputs(row_id="TOOL-02")
    attempt["events"][1]["sha256"] = attempt["events"][0]["sha256"]
    with pytest.raises(V4LivePacketViolation): build_v4_live_packets(contract, live_map, attempt, host, map_path=MAP_PATH)
    contract, live_map, attempt, host = _inputs(row_id="TOOL-02")
    attempt["events"].pop(2); attempt["event_count"] -= 1
    with pytest.raises(V4LivePacketViolation): build_v4_live_packets(contract, live_map, attempt, host, map_path=MAP_PATH)


def test_rejects_raw_fields_and_identity_reuse() -> None:
    contract, live_map, attempt, host = _inputs()
    host["stream_projection"]["observation"]["raw_content"] = "forbidden"
    with pytest.raises(V4LivePacketViolation): build_v4_live_packets(contract, live_map, attempt, host, map_path=MAP_PATH)
    contract, live_map, attempt, host = _inputs()
    attempt["identity"]["row_key"] = "v2_non_soak/TOOL-02"
    with pytest.raises(V4LivePacketViolation): build_v4_live_packets(contract, live_map, attempt, host, map_path=MAP_PATH)
