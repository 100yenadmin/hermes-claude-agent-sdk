from __future__ import annotations
import copy
from pathlib import Path
import pytest
from hermes_claude_agent_sdk.parity.hashing import canonical_json_bytes, sha256_value
from hermes_claude_agent_sdk.parity.results import candidate_hash
from hermes_claude_agent_sdk.parity.v4_contract import OWNERSHIP_PREFLIGHTS, V3_RESULT_CATALOG_HASH, load_v4_contract
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
    kinds = {"start": "message.start", "state": "message.state", "usage": "message.usage", "tool_requested": "tool.request", "tool_result": "tool.complete", "approval_requested": "approval.requested", "approval_decision": "approval.responded", "compaction": "compaction", "background": "background", "restart": "restart", "terminal": "message.complete"}
    names = ("start", "tool_requested", "tool_result", "state", "terminal") if scenario.row_key == "v2_non_soak/TOOL-05" else ("start", "state", "usage", "restart", "terminal")
    events = [{"kind": kinds[name], "byte_length": 10 + index, "sha256": f"{turn_index}{index}".ljust(64, "0"), "terminal_status": outcome if name == "terminal" else None} for index, name in enumerate(names, 1)]
    events.insert(1, {"kind": "message.delta", "byte_length": 19, "sha256": f"{turn_index}f".ljust(64, "0"), "terminal_status": None})
    candidate_hash = sha256_value(candidate)
    return {"identity": {"candidate_hash": candidate_hash, "preflight_hash": preflight_hash, "live_map_sha256": LIVE_MAP_SHA256, "row_key": scenario.row_key, "predecessor_execution_id": scenario.predecessor_execution_id, "path": "positive", "trial_index": scenario.trial_indexes[0]}, "candidate": candidate, "classification": "COMPLETE", "terminal_status": outcome, "event_count": len(events), "event_kinds": {event["kind"]: sum(item["kind"] == event["kind"] for item in events) for event in events}, "events": events, "control_calls_used": 1, "provider_calls": 1, "turns_used": 1, "approval": {"decision_class": "deny", "request_count": 0, "decision_count": 0, "requests": [], "decisions": []}, "turn_index": turn_index}
def _scenario_trace(contract, scenario, attempts):
    expected = next(row["expected_trace"] for row in contract["source_rows"] if f"{row['source_pack']}/{row['source_item_id']}" == scenario.row_key)
    refs = {"openclaw_active/source-docs-discovery-report": [(1, "start"), (2, "terminal")], "openclaw_active/thread-memory-isolation": [(1, "start"), (2, "state"), (4, "terminal")], "openclaw_active/config-restart-capability-flip": [(1, "start"), (2, "restart"), (2, "terminal")], "v2_non_soak/AUTH-01": [(1, "start"), (1, "state"), (1, "usage"), (1, "terminal")], "v2_non_soak/ORCH-01": [(1, "start"), (1, "state"), (1, "terminal")], "v2_non_soak/TOOL-05": [(1, "start"), (1, "tool_requested"), (1, "tool_result"), (1, "state"), (1, "terminal")], "v2_non_soak/ORCH-05": [(1, "start"), (1, "state"), (1, "terminal")]}[scenario.row_key]
    raw_kinds = {"start": "message.start", "state": "message.state", "usage": "message.usage", "tool_requested": "tool.request", "tool_result": "tool.complete", "restart": "restart", "terminal": "message.complete"}
    events = []
    for (attempt_index, name), expected_kind in zip(refs, expected, strict=True):
        event = next(item for attempt in attempts if attempt["turn_index"] == attempt_index for item in attempt["events"] if item["kind"] == raw_kinds[name])
        events.append({"kind": expected_kind, "byte_length": event["byte_length"], "sha256": event["sha256"], "terminal_status": event["terminal_status"], "evidence": {"source": "attempt", "attempt_index": attempt_index, "source_sha256": event["sha256"]}})
    return {"schema_version": 1, "row_key": scenario.row_key, "predecessor_execution_id": scenario.predecessor_execution_id, "path": "positive", "trial_index": scenario.trial_indexes[0], "events": events}
def _delegation(scenario, trial_index=None):
    trial_index = scenario.trial_indexes[0] if trial_index is None else trial_index
    count = sum(1 for _, bound_trial, _, _, path in scenario.child_bindings if bound_trial == trial_index and path == "positive")
    return {"count": count, "background_count": 0, "lifecycle": "completed" if count else "none", "parent_link_sha256": "d" * 64 if count else None}
def _host(turn_count, *, assistant_count=None):
    usage = [{"ordinal": index, "sha256": f"{index}".ljust(64, "0"), "provider": "anthropic", "model": "claude-fable-5-1", "selected_model": "claude-fable-5-1", "effective_model": "claude-fable-5-1", "canonical_model": "claude-fable-5-1", "model_resolution": "exact", "billing_mode": "subscription_included", "cost_status": "included", "fallback_used": False, "api_call_count": turn_count, "tokens": {"input_tokens": 1, "output_tokens": 1}} for index in range(1, turn_count + 1)]
    assistant_count = turn_count if assistant_count is None else assistant_count
    return {"schema_version": 1, "status": "PASS", "runtime": "claude-agent-sdk", "invariant_violations": [], "expected_turn_count": turn_count, "transcript": {"row_count": turn_count + assistant_count, "canonical_rows": {"user": {"count": turn_count}, "assistant": {"count": assistant_count}}, "terminal": {"count": turn_count, "persisted": True, "sha256": "a" * 64}}, "runtime_state": {"present": False, "schema_version": None, "sha256": None}, "runtime_usage": {"receipt_count": turn_count, "ordered": usage, "latest": usage[-1]}}
def _local(path, expected, terminal, prefix, row_key, trial_index):
    events = [{"kind": {"start": "message.start", "state": "message.state", "terminal": "message.complete"}[name], "byte_length": 12 + index, "sha256": f"{prefix}{index}".ljust(64, "0"), "terminal_status": terminal if name == "terminal" else None} for index, name in enumerate(expected, 1)]
    observation = {"identity": {"row_key": row_key, "path": path, "trial_index": trial_index}, "surface": "host_local", "observation_count": 1}
    return {"schema_version": 1, "status": "PASS", "path": path, "host_local": True, "provider_calls": 0, "terminal_status": terminal, "events": events, "observation": observation, "proof_hashes": {"primary": sha256_value(observation), "secondary": sha256_value({"identity": observation["identity"], "events": events})}}
def _inputs(row_key="openclaw_active/source-docs-discovery-report"):
    contract = load_v4_contract(ROOT / "qa/parity-contract-v4.yaml")
    live_map = load_v4_live_execution_map(MAP)
    catalog = load_v4_live_scenario_catalog(MAP)
    scenario = next(row for row in catalog.scenarios if row.row_key == row_key)
    candidate = _candidate(); preflights = _preflights(candidate); v4_hash = sha256_value(candidate)
    pf_hash = _preflight_hash(preflights, v4_hash)
    attempts = [_attempt(scenario, candidate, pf_hash, index) for index in range(1, scenario.turn_count + 1)]
    trial_hash = candidate_hash(catalog_hash=V3_RESULT_CATALOG_HASH, plugin_sha=candidate["plugin_sha"], host_sha=candidate["host_sha"], sdk_version=candidate["sdk_version"], profile_hash=candidate["profile_sha256"], runner_version=candidate["runner_version"], inventory_hash="5" * 64)
    stream = {"schema_version": 1, "name": "stream", "candidate_hash": v4_hash, "trial_candidate_hash": trial_hash, "trial_index": scenario.trial_indexes[0], "status": "PASS", "source": {"executable": "pytest", "source_ref": "tests/stream.py", "test_id": "stream:scenario"}, "observation": {"stream_count": 1, "content_hash": "e" * 64}}
    receipt = {"schema_version": 1, "candidate": candidate, "preflight_projections": preflights, "attempts": attempts, "host_observation": _host(scenario.turn_count), "profile_id": "isolated", "inventory_hash": "5" * 64, "stream_projection": stream, "scenario_trace": _scenario_trace(contract, scenario, attempts), "delegation": _delegation(scenario)}
    return contract, live_map, catalog, scenario, receipt
def test_one_positive_bundle_and_pending_local_paths_without_triple_counting():
    contract, live_map, _, scenario, receipt = _inputs()
    bundle = build_v4_live_packets(contract, scenario, receipt, live_map=live_map, map_path=MAP)
    assert bundle["scenario_receipt"]["provider_accounting"] == {"positive_calls": 2, "denial_calls": 0, "recovery_calls": 0, "total_calls": 2}
    assert len(bundle["scenario_receipt"]["approval_projection_hashes"]) == 2
    assert bundle["scenario_receipt"]["delegation_summary_hash"] == sha256_value(receipt["delegation"])
    assert bundle["paths"]["positive"]["trial"].turn_count == 2
    assert bundle["paths"]["positive"]["trial"].classification.value == "COMPLETE"
    assert bundle["paths"]["denial"]["trial"].classification.value == "PENDING"
    assert bundle["paths"]["recovery"]["trial"].classification.value == "PENDING"
    assert not bundle["paths"]["denial"]["trial"].normalized_events
    assert bundle["paths"]["denial"]["packet"] is None and bundle["paths"]["recovery"]["packet"] is None
    assert "provider_calls" not in bundle["paths"]["positive"]["trial"].to_dict()


@pytest.mark.parametrize("field", ("candidate_hash", "trial_candidate_hash", "trial_index"))
def test_stream_projection_identity_mismatch_fails_closed(field):
    contract, live_map, _, scenario, receipt = _inputs()
    receipt["stream_projection"][field] = 99 if field == "trial_index" else "f" * 64
    with pytest.raises(V4LivePacketViolation, match="stream projection identity"):
        build_v4_live_packets(contract, scenario, receipt, live_map=live_map, map_path=MAP)


def test_host_projection_accepts_extra_assistant_rows_with_valid_terminal_invariants():
    contract, live_map, _, scenario, receipt = _inputs()
    receipt["host_observation"] = _host(scenario.turn_count, assistant_count=scenario.turn_count + 1)
    bundle = build_v4_live_packets(contract, scenario, receipt, live_map=live_map, map_path=MAP)
    assert bundle["paths"]["positive"]["classification"] == "COMPLETE"
def test_explicit_denial_recovery_are_zero_turn_host_local_paths():
    contract, live_map, _, scenario, receipt = _inputs()
    expected = next(row["expected_trace"] for row in contract["source_rows"] if f"{row['source_pack']}/{row['source_item_id']}" == scenario.row_key)
    bundle = build_v4_live_packets(contract, scenario, receipt, {"denial": _local("denial", expected, "denied", "b", scenario.row_key, scenario.trial_indexes[0]), "recovery": _local("recovery", expected, "completed", "c", scenario.row_key, scenario.trial_indexes[0])}, live_map=live_map, map_path=MAP)
    assert bundle["paths"]["denial"]["trial"].turn_count == bundle["paths"]["recovery"]["trial"].turn_count == 0
    assert bundle["paths"]["denial"]["packet"]["classification"] == "EXPECTED_NEGATIVE"
    assert bundle["paths"]["recovery"]["packet"]["classification"] == "COMPLETE"
    assert bundle["paths"]["denial"]["packet"]["events"][-1]["terminal_outcome"] == "denied"
def test_completed_as_denial_and_raw_local_data_are_rejected():
    contract, live_map, _, scenario, receipt = _inputs()
    expected = next(row["expected_trace"] for row in contract["source_rows"] if f"{row['source_pack']}/{row['source_item_id']}" == scenario.row_key)
    bad = _local("denial", expected, "completed", "d", scenario.row_key, scenario.trial_indexes[0])
    with pytest.raises(V4LivePacketViolation):
        build_v4_live_packets(contract, scenario, receipt, {"denial": bad}, live_map=live_map, map_path=MAP)
    bad = _local("denial", expected, "denied", "e", scenario.row_key, scenario.trial_indexes[0]); bad["observation"]["raw_content"] = "forbidden"
    with pytest.raises(V4LivePacketViolation):
        build_v4_live_packets(contract, scenario, receipt, {"denial": bad}, live_map=live_map, map_path=MAP)


@pytest.mark.parametrize("mutation", ("absent", "row", "path", "trial", "proof"))
def test_local_packet_identity_and_proofs_cannot_be_relabelled(mutation):
    contract, live_map, _, scenario, receipt = _inputs()
    expected = next(row["expected_trace"] for row in contract["source_rows"] if f"{row['source_pack']}/{row['source_item_id']}" == scenario.row_key)
    bad = copy.deepcopy(_local("denial", expected, "denied", "f", scenario.row_key, scenario.trial_indexes[0]))
    if mutation == "absent":
        bad["observation"].pop("identity")
    elif mutation == "row":
        bad["observation"]["identity"]["row_key"] = "v2_non_soak/TOOL-02"
    elif mutation == "path":
        bad["observation"]["identity"]["path"] = "recovery"
    elif mutation == "trial":
        bad["observation"]["identity"]["trial_index"] = 2
    else:
        bad["proof_hashes"]["primary"] = "0" * 64
    with pytest.raises(V4LivePacketViolation, match="identity|proof"):
        build_v4_live_packets(contract, scenario, receipt, {"denial": bad}, live_map=live_map, map_path=MAP)
def test_scenario_trace_is_required_and_cannot_infer_row_atoms():
    contract, live_map, _, scenario, receipt = _inputs("openclaw_active/config-restart-capability-flip")
    receipt.pop("scenario_trace")
    with pytest.raises(V4LivePacketViolation):
        build_v4_live_packets(contract, scenario, receipt, live_map=live_map, map_path=MAP)
    _, _, _, _, receipt = _inputs("openclaw_active/config-restart-capability-flip")
    receipt["scenario_trace"]["events"][1]["kind"] = "state"
    with pytest.raises(V4LivePacketViolation):
        build_v4_live_packets(contract, scenario, receipt, live_map=live_map, map_path=MAP)


def test_scenario_trace_accepts_state_and_usage_only_from_bound_host_receipts():
    contract, live_map, _, scenario, receipt = _inputs("v2_non_soak/AUTH-01")
    receipt["host_observation"]["runtime_state"] = {"present": True, "schema_version": 1, "sha256": "9" * 64}
    trace = receipt["scenario_trace"]["events"]
    components = {
        "state": ("runtime_state", receipt["host_observation"]["runtime_state"]),
        "usage": ("runtime_usage", receipt["host_observation"]["runtime_usage"]["latest"]),
    }
    for event in trace:
        kind = event["kind"]
        if kind in components:
            name, component = components[kind]
            event.update({"kind": kind, "byte_length": len(canonical_json_bytes(component)), "sha256": component["sha256"], "terminal_status": None, "evidence": {"source": "host_observation", "component": name, "source_sha256": component["sha256"]}})
    attempt = receipt["attempts"][0]
    attempt["events"] = [event for event in attempt["events"] if event["kind"] not in {"message.state", "message.usage"}]
    attempt["event_count"] = len(attempt["events"])
    attempt["event_kinds"] = {event["kind"]: sum(item["kind"] == event["kind"] for item in attempt["events"]) for event in attempt["events"]}
    bundle = build_v4_live_packets(contract, scenario, receipt, live_map=live_map, map_path=MAP)
    assert [event["kind"] for event in bundle["paths"]["positive"]["trial"].normalized_events] == ["start", "state", "usage", "terminal"]
    receipt["scenario_trace"]["events"][1]["sha256"] = "8" * 64
    receipt["scenario_trace"]["events"][1]["evidence"]["source_sha256"] = "8" * 64
    with pytest.raises(V4LivePacketViolation, match="host component"):
        build_v4_live_packets(contract, scenario, receipt, live_map=live_map, map_path=MAP)
@pytest.mark.parametrize("row_key", ("v2_non_soak/AUTH-01", "openclaw_active/source-docs-discovery-report", "openclaw_active/thread-memory-isolation", "openclaw_active/config-restart-capability-flip"))
def test_positive_scenario_trace_binds_each_turn_and_content(row_key):
    contract, live_map, _, scenario, receipt = _inputs(row_key)
    bundle = build_v4_live_packets(contract, scenario, receipt, live_map=live_map, map_path=MAP)
    expected = next(row["expected_trace"] for row in contract["source_rows"] if f"{row['source_pack']}/{row['source_item_id']}" == scenario.row_key)
    assert [event["kind"] for event in bundle["paths"]["positive"]["trial"].normalized_events] == expected
    assert len(bundle["scenario_receipt"]["attempt_hashes"]) == scenario.turn_count
    assert bundle["scenario_receipt"]["content_projection_hash"] != sha256_value(())
def test_delegation_summary_is_explicit_and_scenario_bound():
    contract, live_map, _, scenario, receipt = _inputs("v2_non_soak/ORCH-01")
    with pytest.raises(V4LivePacketViolation):
        build_v4_live_packets(contract, scenario, {**receipt, "delegation": {"count": 0, "background_count": 0, "lifecycle": "none", "parent_link_sha256": None}}, live_map=live_map, map_path=MAP)
@pytest.mark.parametrize("row_key,invalid_count", (("v2_non_soak/TOOL-05", 3), ("v2_non_soak/ORCH-05", 1), ("openclaw_active/source-docs-discovery-report", 1)))
def test_delegation_count_uses_bound_trial_child_bindings(row_key, invalid_count):
    contract, live_map, _, scenario, receipt = _inputs(row_key)
    bundle = build_v4_live_packets(contract, scenario, receipt, live_map=live_map, map_path=MAP)
    expected = sum(1 for _, trial_index, _, _, path in scenario.child_bindings if trial_index == scenario.trial_indexes[0] and path == "positive")
    assert receipt["delegation"]["count"] == expected
    assert bundle["scenario_receipt"]["delegation_summary"]["count"] == expected
    bad = dict(receipt, delegation={**receipt["delegation"], "count": invalid_count, "lifecycle": "completed" if invalid_count else "none", "parent_link_sha256": "d" * 64 if invalid_count else None})
    with pytest.raises(V4LivePacketViolation):
        build_v4_live_packets(contract, scenario, bad, live_map=live_map, map_path=MAP)
