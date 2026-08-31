"""Offline coverage and safety checks for the curated native-36 source pack."""

import ast
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PACK_PATH = ROOT / "qa/parity/v3/source-packs/clawprobench-native.json"
CANONICAL_PATH = ROOT / "src/hermes_claude_agent_sdk/parity/canonical.py"

EXPECTED_SOURCE_ROWS = (
    "constraints_19_cron_conflict_buffer_live",
    "constraints_22_message_audience_boundary_live",
    "constraints_23_external_approval_boundary_live",
    "error_recovery_13_openclaw_memory_search_diagnosis_live",
    "error_recovery_20_browser_cron_message_orchestration_live",
    "error_recovery_22_incident_commander_sequence_live",
    "error_recovery_23_partial_containment_boundary_live",
    "error_recovery_24_partial_vs_containment_live",
    "error_recovery_25_rollback_gate_decision_live",
    "error_recovery_26_duplicate_automation_suppression_live",
    "intel_e01_skill_inventory",
    "intel_h01_skill_gap_remediation",
    "intel_h02_cross_surface_diagnosis",
    "intel_h03_temporal_constraint_scheduling",
    "intel_m01_skill_routing",
    "intel_m02_multi_surface_probe",
    "intel_m05_injection_resist",
    "intel_m06_session_health_check",
    "intel_x01_full_system_audit",
    "intel_x02_adversarial_multi_step",
    "planning_13_openclaw_skill_routing_live",
    "planning_19_agent_delegation_boundary_live",
    "planning_20_session_agent_handoff_live",
    "planning_21_long_horizon_preference_override_live",
    "synthesis_15_openclaw_skill_source_audit_live",
    "synthesis_16_openclaw_runtime_surface_matrix_live",
    "synthesis_17_openclaw_gateway_surface_matrix_live",
    "synthesis_24_browser_message_reschedule_live",
    "synthesis_25_memory_conflict_resolution_live",
    "synthesis_26_memory_staleness_resolution_live",
    "synthesis_27_memory_quadrant_resolution_live",
    "synthesis_28_browser_internal_external_split_live",
    "synthesis_29_memory_conflict_action_gate_live",
    "tool_use_14_openclaw_skill_inventory_live",
    "tool_use_21_recurring_cron_expiry_notice_live",
    "tool_use_22_browser_dom_console_triage_live",
)

STATE_KEYS = {
    "lifecycle",
    "approval",
    "tool",
    "resume",
    "billing",
    "side_effect_count",
    "boundary_sha256",
}
PATH_KEYS = {
    "required",
    "expected_outcome",
    "trace_codes",
    "terminal",
    "tool_calls",
    "side_effect_count",
    "sdk_events",
    "state_before",
    "state_after",
}
FORBIDDEN_CONTENT = (
    "https://",
    "/users/",
    "prompt",
    "transcript",
    "customer",
    "tenant",
    "session_id",
    "external_session_id",
    "tool_args",
    "tool_results",
    "password",
    "cookie",
    "api_key",
)


def _load_pack():
    return json.loads(PACK_PATH.read_text(encoding="utf-8"))


def _trace_registry():
    """Read the checked-in closed registry without importing runtime modules."""
    tree = ast.parse(CANONICAL_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            names = []
        if "TRACE_REGISTRY" in names:
            return set(ast.literal_eval(node.value))
    raise AssertionError("TRACE_REGISTRY assignment not found")


def _assert_no_forbidden_content(value):
    encoded = json.dumps(value, ensure_ascii=False).lower()
    for fragment in FORBIDDEN_CONTENT:
        assert fragment not in encoded, fragment


def _assert_state(state):
    assert set(state) == STATE_KEYS
    assert state["lifecycle"] in {"fresh", "bound", "running", "completed", "failed", "cancelled", "closed"}
    assert state["approval"] in {"not_required", "pending", "granted", "denied", "late_rejected"}
    assert state["tool"] in {"none", "requested", "executed", "denied", "cancelled", "failed", "recovered"}
    assert state["resume"] in {"absent", "supplied", "accepted", "rejected"}
    assert state["billing"] == "not_applicable"
    assert state["side_effect_count"] == 0
    assert state["boundary_sha256"] is None


def test_native_pack_has_exact_pinned_coverage_and_provenance():
    pack = _load_pack()
    rows = pack["capabilities"]
    source_rows = [row["source_rows"][0]["row_id"] for row in rows]
    assert len(rows) == 36
    assert tuple(source_rows) == EXPECTED_SOURCE_ROWS
    assert pack["pack_id"] == "clawprobench_native"
    assert pack["schema_version"] == 1
    assert "source_pack_schema_version" not in pack
    assert pack["status"] == pack["mapping_status"] == "PENDING"
    source = pack["source_pack"]["source"]
    assert source["commit_sha"] == "c4b8395854fe0752eef435b44f140366efd44d8e"
    assert source["license_id"] == "Apache-2.0"
    assert source["license_sha256"] == "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"
    digest = hashlib.sha256(json.dumps(source_rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert digest == pack["source_pack"]["row_ids_sha256"]
    assert pack["source_pack"]["expected_count"] == 36
    assert pack["source_pack"]["row_ids"] == source_rows


def test_native_pack_aliases_are_bounded_collision_checked_and_reversible():
    pack = _load_pack()
    rows = pack["capabilities"]
    aliases = pack["alias_contract"]["aliases"]
    assert pack["alias_contract"]["max_identifier_bytes"] == 68
    assert pack["alias_contract"]["alias_count"] == 17
    assert [item["alias_ordinal"] for item in aliases] == list(range(1, 18))
    assert len({item["capability_id"] for item in aliases}) == 17
    by_source = {row["source_rows"][0]["row_id"]: row for row in rows}
    long_rows = sorted(
        source for source in by_source if len(f"CAP-CLAWPROBENCH-NATIVE-{source}".encode()) > 68
    )
    assert [item["source_row_id"] for item in aliases] == long_rows
    for item in aliases:
        source = item["source_row_id"]
        suffix = hashlib.sha256(f"clawprobench_native:{source}".encode()).hexdigest()[:16]
        ordinal = item["alias_ordinal"]
        assert item["source_key_sha256"] == hashlib.sha256(source.encode()).hexdigest()
        assert item["capability_id"] == f"CAP-CPN-{ordinal:02d}-{suffix}"
        assert item["scenario_id"] == f"SCN-CPN-{ordinal:02d}-{suffix}"
        assert by_source[source]["id"] == item["capability_id"]
        assert by_source[source]["scenario_id"] == item["scenario_id"]
    assert len({row["id"] for row in rows}) == 36
    assert len({row["scenario_id"] for row in rows}) == 36
    assert all(len(row[key].encode()) <= 68 for row in rows for key in ("id", "scenario_id"))
    assert len(pack["alias_contract"]["source_key_map"]) == 36


def test_native_pack_paths_states_and_traces_are_closed():
    pack = _load_pack()
    trace_registry = _trace_registry()
    for row in pack["capabilities"]:
        assert set(row) == {
            "id", "source_rows", "scenario_id", "lane", "surface", "owner", "consumers",
            "positive_path", "denial_path", "recovery_path", "state_before", "state_after",
            "expected_trace", "primary_proof", "secondary_proof", "repeat_policy", "session_scope",
            "sdk_classification", "required", "adapter", "source_metadata", "grader",
            "isolation_resume", "mapping_status", "mapping_gaps",
        }
        assert row["consumers"] == ["inventory", "run", "grade"]
        assert row["required"] is True and row["mapping_status"] == "PENDING"
        assert row["expected_trace"] == row["positive_path"]["trace_codes"]
        for role, expected, terminal in (
            ("positive_path", "PASS", "complete"),
            ("denial_path", "EXPECTED_NEGATIVE", "failed"),
            ("recovery_path", "PASS", "complete"),
        ):
            path = row[role]
            role_name = role.removesuffix("_path")
            assert set(path) == PATH_KEYS
            assert path["expected_outcome"] == expected
            assert path["terminal"] == {"kind": terminal, "count": 1}
            assert path["required"] is True
            assert path["tool_calls"] == [] and path["sdk_events"] == []
            assert path["side_effect_count"] == 0
            assert set(path["trace_codes"]) <= trace_registry
            assert "outcome" not in path.keys()
            assert path["trace_codes"][0] == f"path.{role_name}.begin"
            assert path["trace_codes"][-2:] == [f"terminal.{terminal}", f"path.{role_name}.end"]
            assert path["trace_codes"].count(f"terminal.{terminal}") == 1
            assert sum(code.startswith("terminal.") for code in path["trace_codes"]) == 1
            _assert_state(path["state_before"])
            _assert_state(path["state_after"])
        _assert_state(row["state_before"])
        _assert_state(row["state_after"])
        assert row["grader"]["status"] == "PENDING"
        assert row["adapter"]["side_effect_mode"] == "dry_run"
        assert row["adapter"]["fixture_binding"] == "digest_only"
        assert row["repeat_policy"]["mode"] in {"once", "consecutive_3"}
        assert set(row["primary_proof"]) == {"kind", "ref", "sha256"}
        assert row["primary_proof"]["kind"] == "deterministic"
        for proof in row["secondary_proof"]:
            assert set(proof) == {"kind", "ref", "sha256"}
            assert proof["kind"] == "deterministic"
        assert len(row["secondary_proof"]) == 2


def test_native_pack_gaps_are_explicit_and_content_is_sanitized():
    pack = _load_pack()
    required_gap_codes = {"GAP-CLOSED-TRACE", "GAP-EXPECTED-TOOL-SCHEMA", "GAP-STATE-SUMMARY", "GAP-RUNTIME-PROOF"}
    gap_count = 0
    repeats = {"once": 0, "consecutive_3": 0}
    for row in pack["capabilities"]:
        gaps = row["mapping_gaps"]
        gap_count += len(gaps)
        assert required_gap_codes <= {gap["code"] for gap in gaps}
        assert all(gap["status"] == "PENDING" and gap["action"] == "STOP" for gap in gaps)
        repeats[row["repeat_policy"]["mode"]] += 1
    assert gap_count == 216
    assert repeats == {"once": 15, "consecutive_3": 21}
    _assert_no_forbidden_content(pack)
