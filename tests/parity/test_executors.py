from __future__ import annotations

import asyncio
from pathlib import Path
import tomllib

from hermes_claude_agent_sdk.parity import executors
from hermes_claude_agent_sdk.parity.results import ExecutionClassification
from hermes_claude_agent_sdk.parity.runner import ExecutionContext


def _context(catalog, capability_id: str, sdk_version: str = "0.2.151"):
    return ExecutionContext(
        capability=catalog.by_id[capability_id],
        path="positive",
        trial_index=1,
        profile_id="fable-v3-isolated",
        profile_hash="3" * 64,
        plugin_sha="1" * 40,
        host_sha="2" * 40,
        sdk_version=sdk_version,
        runner_version="3.0.0",
        inventory_hash="4" * 64,
        contract_hash=catalog.contract_hash,
        catalog_hash=catalog.catalog_hash,
        remaining_turn_budget=180,
    )


def test_approval_preflight_rejects_bundled_cli_drift_before_host_call(
    catalog, monkeypatch
) -> None:
    monkeypatch.setattr(
        executors,
        "check_model_compatibility",
        lambda _: {
            "compatible": False,
            "reason": "bundled_cli_version_unsupported",
        },
    )

    bundle = asyncio.run(
        executors.approval_followthrough(
            _context(catalog, "active:approval-turn-tool-followthrough")
        )
    )

    assert bundle.turn_count == 0
    assert all(
        outcome.classification is ExecutionClassification.ENVIRONMENT_BLOCKED
        for outcome in bundle.outcomes.values()
    )
    assert {
        outcome.reason_code for outcome in bundle.outcomes.values()
    } == {"bundled_cli_version_unsupported"}


def test_source_executor_mapping_is_not_published_as_entry_point() -> None:
    assert (
        executors.EXECUTORS["active-approval-turn-tool-followthrough"]
        is executors.approval_followthrough
    )
    project = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    entry_points = project["project"].get("entry-points", {})
    scripts = project["project"].get("scripts", {})
    assert "hermes_claude_agent_sdk.parity_executors" not in entry_points
    assert "hermes-claude-agent-sdk-parity" not in scripts
    assert "hermes-claude-agent-sdk-parity-v4" not in scripts
