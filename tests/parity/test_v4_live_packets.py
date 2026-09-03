from __future__ import annotations
from pathlib import Path
import pytest
from hermes_claude_agent_sdk.parity.hashing import sha256_value
from hermes_claude_agent_sdk.parity.v4_contract import OWNERSHIP_PREFLIGHTS, load_v4_contract
from hermes_claude_agent_sdk.parity.v4_live_map import load_v4_live_execution_map
from hermes_claude_agent_sdk.parity.v4_live_packets import V4LivePacketViolation, build_v4_live_packets
from hermes_claude_agent_sdk.parity.v4_live_scenarios import LIVE_MAP_SHA256, load_v4_live_scenario_catalog
ROOT = Path(__file__).parents[2]
MAP = ROOT / "qa" / "parity-v4-live-execution-map.yaml"
def _candidate() -> dict[str, str]:
    return {"plugin_sha": "1" * 40, "host_sha": "2" * 40, "wheel_sha256": "3" * 64, "profile_sha256": "4" * 64, "sdk_distribution": "claude-agent-sdk", "sdk_version": "0.2.151", "cli_version": "2.1.258", "model": "claude-fable-5-1", "runner_id": "hermes-parity-v4", "runner_version": "4.0.0"}
def _preflights(candidate: dict[str, str]) -> dict[str, dict[str, object]]:
    digest = sha256_value(candidate)
    return {name: {"schema_version": 1, "name": name, "candidate_hash": digest, "status": "PASS", "source": {"executable": "pytest", "source_ref": f"tests/{name}.py", "test_id": f"fixture:{name}"}, "observation": {"exit_status": 0, "check_count": 1}} for name in OWNERSHIP_PREFLIGHTS}
def _preflight_hash(preflights, candidate_hash):
    return sha256_value({name: {"candidate_hash": candidate_hash, "status": "PASS", "source_hash": sha256_value(item["source"]), "observation_hash": sha256_value(item["observation"])} for name, item in preflights.items()})
def _attempt(scenario, candidate, preflight_hash, turn_index, *, outcome="completed"):
    expected = next(row["expected_trace"] for row in load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")["source_rows"] if f"{row['source_pack']}/{row['source_item_id']}" == scenario.row_key)
    kinds = {"start": "message.start", "state": "message.state", "usage": "message.usage", "tool_requested": "tool.request", "tool_result": "tool.complete", "approval_requested": "approval.requested", "approval_decision": "approval.responded", "compaction": "compaction", "background": "background", "restart": "restart", "terminal": "message.complete"}
    events = [{"kind": kinds[name], "byte_length": 10 + index, "sha256": f"{turn_index}{index}".ljust(64, "0"), "terminal_status": outcome if name == "terminal" else None} for index, name in enumerate(expected, 1)]
    events.insert(1, {"kind": "message.delta", "byte_length": 19, "sha256": f"{turn_index}f".ljust(64, "0"), "terminal_status": None})
    candidate_hash = sha256_value(candidate)
    return {"identity": {"candidate_hash": candidate_hash, "preflight_hash": preflight_hash, "live_map_sha256": LIVE_MAP_SHA256, "row_key": scenario.row_key, "predecessor_execution_id": scenario.predecessor_execution_id, "path": "positive", "trial_index": scenario.trial_indexes[0]}, "candidate": candidate, "classification": "COMPLETE", "terminal_status": outcome, "event_count": len(events), "event_kinds": {event["kind"]: sum(item["kind"] == event["kind"] for item in events) for event in events}, "events": events, "control_calls_used": 1, "provider_calls": 1, "turns_used": 1, "approval": {"decision_class": "deny", "decision_count": 0}, "turn_index": turn_index}
def _host(turn_count):
    usage = [{"ordinal": index, "sha256": f"{index}".ljust(64, "0"), "provider": "anthropic", "model": "claude-fable-5-1", "selected_model": "claude-fable-5-1", "effective_model": "claude-fable-5-1", "canonical_model": "claude-fable-5-1", "model_resolution": "exact", "billing_mode": "subscription_included", "cost_status": "included", "fallback_used": False, "api_call_count": turn_count, "tokens": {"input_tokens": 1, "output_tokens": 1}} for index in range(1, turn_count + 1)]
    return {"schema_version": 1, "status": "PASS", "runtime": "claude-agent-sdk", "invariant_violations": [], "expected_turn_count": turn_count, "transcript": {"row_count": turn_count * 2, "canonical_rows": {"user": {"count": turn_count}, "assistant": {"count": turn_count}}, "terminal": {"count": turn_count, "persisted": True, "sha256": "a" * 64}}, "runtime_state": {"present": False, "schema_version": None, "sha256": None}, "runtime_usage": {"receipt_count": turn_count, "ordered": usage, "latest": usage[-1]}}
def _local(path, expected, terminal, prefix):
    events = [{"kind": {"start": "message.start", "state": "message.state", "terminal": "message.complete"}[name], "byte_length": 12 + index, "sha256": f"{prefix}{index}".ljust(64, "0"), "terminal_status": terminal if name == "terminal" else None} for index, name in enumerate(expected, 1)]
    return {"schema_version": 1, "status": "PASS", "path": path, "host_local": True, "provider_calls": 0, "terminal_status": terminal, "events": events, "observation": {"surface": "host_local", "observation_count": 1}, "proof_hashes": {"primary": f"{prefix}".ljust(64, "0"), "secondary": f"{prefix}f".ljust(64, "0")}}
def _inputs(row_key="openclaw_active/source-docs-discovery-report"):
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    live_map = load_v4_live_execution_map(MAP)
    catalog = load_v4_live_scenario_catalog(MAP)
    scenario = next(row for row in catalog.scenarios if row.row_key == row_key)
    candidate = _candidate(); preflights = _preflights(candidate); v4_hash = sha256_value(candidate)
    pf_hash = _preflight_hash(preflights, v4_hash)
    attempts = [_attempt(scenario, candidate, pf_hash, index) for index in range(1, scenario.turn_count + 1)]
    stream = {"schema_version": 1, "name": "stream", "candidate_hash": v4_hash, "trial_candidate_hash": "f" * 64, "trial_index": scenario.trial_indexes[0], "status": "PASS", "source": {"executable": "pytest", "source_ref": "tests/stream.py", "test_id": "stream:scenario"}, "observation": {"stream_count": 1, "content_hash": "e" * 64}}
    receipt = {"schema_version": 1, "candidate": candidate, "preflight_projections": preflights, "attempts": attempts, "host_observation": _host(scenario.turn_count), "profile_id": "isolated", "inventory_hash": "5" * 64, "stream_projection": stream}
    return contract, live_map, catalog, scenario, receipt
def test_one_positive_bundle_and_pending_local_paths_without_triple_counting():
    contract, live_map, _, scenario, receipt = _inputs()
    bundle = build_v4_live_packets(contract, scenario, receipt, live_map=live_map, map_path=MAP)
    assert bundle["scenario_receipt"]["provider_accounting"] == {"positive_calls": 2, "denial_calls": 0, "recovery_calls": 0, "total_calls": 2}
    assert bundle["paths"]["positive"]["trial"].turn_count == 2
    assert bundle["paths"]["positive"]["trial"].classification.value == "COMPLETE"
    assert bundle["paths"]["denial"]["trial"].classification.value == "PENDING"
    assert bundle["paths"]["recovery"]["trial"].classification.value == "PENDING"
    assert not bundle["paths"]["denial"]["trial"].normalized_events
    assert bundle["paths"]["denial"]["packet"] is None and bundle["paths"]["recovery"]["packet"] is None
    assert "provider_calls" not in bundle["paths"]["positive"]["trial"].to_dict()
def test_explicit_denial_recovery_are_zero_turn_host_local_paths():
    contract, live_map, _, scenario, receipt = _inputs()
    expected = next(row["expected_trace"] for row in contract["source_rows"] if f"{row['source_pack']}/{row['source_item_id']}" == scenario.row_key)
    bundle = build_v4_live_packets(contract, scenario, receipt, {"denial": _local("denial", expected, "denied", "b"), "recovery": _local("recovery", expected, "completed", "c")}, live_map=live_map, map_path=MAP)
    assert bundle["paths"]["denial"]["trial"].turn_count == bundle["paths"]["recovery"]["trial"].turn_count == 0
    assert bundle["paths"]["denial"]["packet"]["classification"] == "EXPECTED_NEGATIVE"
    assert bundle["paths"]["recovery"]["packet"]["classification"] == "COMPLETE"
    assert bundle["paths"]["denial"]["packet"]["events"][-1]["terminal_outcome"] == "denied"
def test_completed_as_denial_and_raw_local_data_are_rejected():
    contract, live_map, _, scenario, receipt = _inputs()
    expected = next(row["expected_trace"] for row in contract["source_rows"] if f"{row['source_pack']}/{row['source_item_id']}" == scenario.row_key)
    bad = _local("denial", expected, "completed", "d")
    with pytest.raises(V4LivePacketViolation):
        build_v4_live_packets(contract, scenario, receipt, {"denial": bad}, live_map=live_map, map_path=MAP)
    bad = _local("denial", expected, "denied", "e"); bad["observation"]["raw_content"] = "forbidden"
    with pytest.raises(V4LivePacketViolation):
        build_v4_live_packets(contract, scenario, receipt, {"denial": bad}, live_map=live_map, map_path=MAP)
def test_positive_content_delta_is_hash_bound_but_not_predecessor_trace():
    contract, live_map, _, scenario, receipt = _inputs("v2_non_soak/AUTH-01")
    bundle = build_v4_live_packets(contract, scenario, receipt, live_map=live_map, map_path=MAP)
    assert [event["kind"] for event in bundle["paths"]["positive"]["trial"].normalized_events] == ["start", "state", "usage", "terminal"]
    assert bundle["scenario_receipt"]["content_projection_hash"] != sha256_value(())
