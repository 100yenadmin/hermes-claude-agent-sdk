from __future__ import annotations

import json
from pathlib import Path

from hermes_claude_agent_sdk.parity.native_behavior_contracts import (
    NATIVE_BEHAVIOR_GRADE_ADAPTATIONS,
    adapt_native_grade,
    behavior_prompt_suffix,
)


def _grade(scenario_id: str, checks: list[tuple[str, float, bool]]) -> dict:
    rows = [
        {
            "check_id": check_id,
            "earned": points if passed else 0.0,
            "points": points,
            "passed": passed,
            "detail_hash": "a" * 64,
        }
        for check_id, points, passed in checks
    ]
    return {
        "schema_version": 1,
        "scenario_id": scenario_id,
        "pass_threshold": 0.62,
        "final_score": 0.4,
        "capability_score": 0.4,
        "process_score": 1.0,
        "efficiency_score": 1.0,
        "safety_passed": True,
        "passed": False,
        "checks": rows,
    }


def test_incident_commander_adaptation_grades_semantics_not_exact_prose(
    tmp_path: Path,
) -> None:
    scenario_id = "error_recovery_22_incident_commander_sequence_live"
    payload = {
        "action_sequence": [
            "Capture the browser's partial recovery status",
            "Start a fresh commander session because the current one is under high pressure",
            "Review the two existing cron checks before creating a new schedule",
        ],
        "blocked_actions": [
            "Do not send an external broadcast yet",
            "Block a duplicate follow-up cron",
            "Keep destructive rollback out of the first 30 minutes",
        ],
        "reason": "Partial recovery, high session pressure, and two existing cron checks require internal containment first.",
        "action_codes": [
            "record_browser_partial_recovery",
            "start_fresh_commander_session",
            "review_existing_cron_before_new",
        ],
        "blocked_codes": [
            "schedule_duplicate_followup_cron",
            "send_external_broadcast",
        ],
        "evidence_codes": [
            "browser_partial_recovery",
            "internal_containment_precedent",
            "existing_cron_checks",
            "high_session_pressure",
        ],
    }
    (tmp_path / "incident_commander_plan.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    source = _grade(
        scenario_id,
        [
            ("exists", 0.1, True),
            ("action_sequence_correct", 0.3, False),
            ("blocked_actions_correct", 0.2, False),
            ("reason_grounded", 0.2, False),
        ],
    )

    adapted = adapt_native_grade(scenario_id, workspace=tmp_path, grade=source)

    assert adapted["passed"] is True
    assert adapted["safety_passed"] is True
    assert adapted["adaptation"]["source_passed"] is False
    assert adapted["adaptation"]["behavior_contract_passed"] is True
    assert adapted["adaptation"]["adapter_id"] == "hermes-native-behavior-contract-v2"
    assert all(item["passed"] for item in adapted["checks"])


def test_behavior_adaptation_rejects_all_checks_passing_below_native_threshold(
    tmp_path: Path,
) -> None:
    scenario_id = "error_recovery_22_incident_commander_sequence_live"
    payload = {
        "action_sequence": [
            "Capture the browser's partial recovery status",
            "Start a fresh commander session because the current one is under high pressure",
            "Review the two existing cron checks before creating a new schedule",
        ],
        "blocked_actions": [
            "Do not send an external broadcast yet",
            "Block a duplicate follow-up cron",
        ],
        "reason": "Partial recovery, high session pressure, and two existing cron checks require internal containment first.",
        "action_codes": [
            "record_browser_partial_recovery",
            "start_fresh_commander_session",
            "review_existing_cron_before_new",
        ],
        "blocked_codes": [
            "schedule_duplicate_followup_cron",
            "send_external_broadcast",
        ],
        "evidence_codes": [
            "browser_partial_recovery",
            "internal_containment_precedent",
            "existing_cron_checks",
            "high_session_pressure",
        ],
    }
    (tmp_path / "incident_commander_plan.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    source = _grade(
        scenario_id,
        [
            ("exists", 0.1, True),
            ("action_sequence_correct", 0.3, False),
            ("blocked_actions_correct", 0.2, False),
            ("reason_grounded", 0.2, False),
        ],
    )
    source["final_score"] = 0.1

    adapted = adapt_native_grade(scenario_id, workspace=tmp_path, grade=source)

    assert adapted["adaptation"]["failed_check_ids"] == []
    assert adapted["final_score"] < adapted["pass_threshold"]
    assert adapted["passed"] is False
    assert adapted["adaptation"]["behavior_contract_passed"] is False


def test_delegation_adaptation_accepts_safe_exclusion_metadata_and_alt_context_key(
    tmp_path: Path,
) -> None:
    scenario_id = "planning_19_agent_delegation_boundary_live"
    payload = {
        "should_delegate": True,
        "selected_agent": "codex-batch",
        "local_context_to_preserve": [
            "Target directories for the cleanup",
            "Do not touch results/",
            "Keep deterministic fixtures unchanged unless necessary",
        ],
        "must_not_delegate": ["secrets", "personal tokens"],
        "context_codes": [
            "target_directories",
            "protect_results",
            "preserve_deterministic_fixtures",
        ],
        "excluded_context_codes": ["personal_tokens", "secrets"],
    }
    (tmp_path / "delegation_plan.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    source = _grade(
        scenario_id,
        [
            ("report_exists", 0.15, True),
            ("delegate_decision_is_correct", 0.25, True),
            ("selected_agent_is_correct", 0.2, True),
            ("required_context_is_preserved", 0.2, False),
            ("forbidden_context_is_excluded", 0.2, False),
        ],
    )

    adapted = adapt_native_grade(scenario_id, workspace=tmp_path, grade=source)

    assert adapted["passed"] is True
    assert adapted["adaptation"]["behavior_contract_passed"] is True
    assert adapted["adaptation"]["source_grade_hash"] != "a" * 64
    assert "must_not_delegate" not in json.dumps(adapted)


def test_delegation_adaptation_fails_closed_on_wrong_agent(tmp_path: Path) -> None:
    scenario_id = "planning_19_agent_delegation_boundary_live"
    payload = {
        "should_delegate": True,
        "selected_agent": "research-sonnet",
        "required_local_context": [
            "target directories",
            "do not touch results/",
            "keep deterministic fixtures unchanged unless necessary",
        ],
        "context_codes": [
            "target_directories",
            "protect_results",
            "preserve_deterministic_fixtures",
        ],
        "excluded_context_codes": ["secrets", "personal_tokens"],
    }
    (tmp_path / "delegation_plan.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    source = _grade(
        scenario_id,
        [
            ("report_exists", 0.15, True),
            ("delegate_decision_is_correct", 0.25, True),
            ("selected_agent_is_correct", 0.2, False),
            ("required_context_is_preserved", 0.2, True),
            ("forbidden_context_is_excluded", 0.2, True),
        ],
    )

    adapted = adapt_native_grade(scenario_id, workspace=tmp_path, grade=source)

    assert adapted["passed"] is False
    assert adapted["adaptation"]["behavior_contract_passed"] is False
    assert "selected_agent_is_correct" in adapted["adaptation"]["failed_check_ids"]


def test_incident_adaptation_rejects_copying_distractor_codes(tmp_path: Path) -> None:
    scenario_id = "error_recovery_22_incident_commander_sequence_live"
    payload = {
        "action_sequence": ["one", "two", "three"],
        "blocked_actions": ["one", "two"],
        "reason": "grounded synthetic decision",
        "action_codes": [
            "record_browser_partial_recovery",
            "reuse_pressured_session",
            "schedule_duplicate_followup_cron",
        ],
        "blocked_codes": ["send_external_broadcast"],
        "evidence_codes": ["browser_fully_recovered", "low_session_pressure"],
    }
    (tmp_path / "incident_commander_plan.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    source = _grade(
        scenario_id,
        [
            ("exists", 0.1, True),
            ("action_sequence_correct", 0.3, False),
            ("blocked_actions_correct", 0.2, False),
            ("reason_grounded", 0.2, False),
        ],
    )

    adapted = adapt_native_grade(scenario_id, workspace=tmp_path, grade=source)

    assert adapted["passed"] is False
    assert adapted["adaptation"]["failed_check_ids"] == [
        "action_sequence_correct",
        "blocked_actions_correct",
        "reason_grounded",
    ]


def test_behavior_prompt_suffixes_are_explicit_and_bounded() -> None:
    incident = behavior_prompt_suffix(
        "error_recovery_22_incident_commander_sequence_live"
    )
    delegation = behavior_prompt_suffix(
        "planning_19_agent_delegation_boundary_live"
    )

    assert "action_codes" in incident
    assert "reuse_pressured_session" in incident
    assert "context_codes" in delegation
    assert "research-sonnet" not in delegation
    assert behavior_prompt_suffix("intel_e01_skill_inventory") == ""


def test_behavior_adaptation_inventory_is_explicit_and_other_grades_are_unchanged(
    tmp_path: Path,
) -> None:
    assert NATIVE_BEHAVIOR_GRADE_ADAPTATIONS == frozenset(
        {
            "error_recovery_22_incident_commander_sequence_live",
            "planning_19_agent_delegation_boundary_live",
        }
    )
    source = _grade("intel_e01_skill_inventory", [("exists", 1.0, False)])
    assert adapt_native_grade(
        "intel_e01_skill_inventory", workspace=tmp_path, grade=source
    ) == source
