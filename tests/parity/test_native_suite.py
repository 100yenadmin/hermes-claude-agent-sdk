from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.native_sandbox import (
    SKILLS_INVENTORY,
    native_environment_snapshot,
)
from hermes_claude_agent_sdk.parity.native_suite import (
    CLAWPROBENCH_SHA,
    NATIVE_SOURCE_IDS,
    grade_native_trace,
    load_native_scenario,
    native_execution_ids,
)


def test_native_execution_inventory_is_exactly_36_and_unique() -> None:
    assert len(NATIVE_SOURCE_IDS) == 36
    assert len(set(NATIVE_SOURCE_IDS)) == 36
    assert len(native_execution_ids()) == 36
    assert set(native_execution_ids()) == {
        f"native-{source_id}" for source_id in NATIVE_SOURCE_IDS
    }


def _pinned_root() -> Path:
    raw = os.environ.get("CLAWPROBENCH_ROOT")
    if not raw:
        pytest.skip("CLAWPROBENCH_ROOT is not configured")
    root = Path(raw).resolve()
    if not root.is_dir():
        pytest.skip("CLAWPROBENCH_ROOT is unavailable")
    return root


def test_pinned_source_inventory_and_one_exact_grader_path() -> None:
    root = _pinned_root()
    scenario = load_native_scenario(root, "intel_e01_skill_inventory")
    assert scenario.scenario_id == "intel_e01_skill_inventory"
    assert scenario.path.is_relative_to(root / "scenarios")
    assert len(scenario.source_bundle_hash) == 64

    ready = sorted(
        item["name"] for item in SKILLS_INVENTORY["skills"] if item["eligible"]
    )
    missing = sorted(
        item["name"] for item in SKILLS_INVENTORY["skills"] if not item["eligible"]
    )
    report = {
        "total_skills": len(ready) + len(missing),
        "ready_count": len(ready),
        "missing_count": len(missing),
        "ready_list": ready,
        "missing_list": missing,
    }
    trace = {
        "events": [
            {
                "type": "tool_call",
                "tool": "exec",
                "args": {"command": "openclaw skills list --json"},
                "seq": 0,
            },
            {
                "type": "tool_call",
                "tool": "write",
                "args": {
                    "path": "skill_report.json",
                    "file_path": "skill_report.json",
                },
                "seq": 1,
            },
        ],
        "audit_state": {
            "native_environment": native_environment_snapshot(("skills",))
        },
        "metrics": {"tool_calls": 2, "assistant_turns": 1},
    }
    with tempfile.TemporaryDirectory() as temp_name:
        temp_root = Path(temp_name)
        workspace = temp_root / "workspace"
        workspace.mkdir()
        (workspace / "skill_report.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        grade = grade_native_trace(
            scenario,
            source_root=root,
            workspace=workspace,
            trace=trace,
            temp_root=temp_root,
        )
    assert grade["scenario_id"] == scenario.scenario_id
    assert grade["passed"] is True
    assert grade["safety_passed"] is True
    assert CLAWPROBENCH_SHA == "c4b8395854fe0752eef435b44f140366efd44d8e"
