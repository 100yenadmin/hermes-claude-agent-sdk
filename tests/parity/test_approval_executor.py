from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest


_HOST_ROOT_VALUE = os.environ.get("HERMES_AGENT_HOST_ROOT")
if not _HOST_ROOT_VALUE:
    pytest.skip("HERMES_AGENT_HOST_ROOT is not configured", allow_module_level=True)
HOST_ROOT = Path(_HOST_ROOT_VALUE)
if not HOST_ROOT.is_dir():
    pytest.skip("configured Hermes host checkout is absent", allow_module_level=True)
if str(HOST_ROOT) not in sys.path:
    sys.path.insert(0, str(HOST_ROOT))

from hermes_claude_agent_sdk.parity.executors import approval_followthrough
from hermes_claude_agent_sdk.parity.results import ExecutionClassification
from hermes_claude_agent_sdk.parity.runner import ExecutionContext


def test_approval_followthrough_uses_exact_host_allow_deny_and_recovery(
    catalog, candidate_fields, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = catalog.by_id["active:approval-turn-tool-followthrough"]
    monkeypatch.setenv("HERMES_PARITY_PLUGIN_SHA", candidate_fields["plugin_sha"])
    monkeypatch.setenv("HERMES_AGENT_HOST_SHA", candidate_fields["host_sha"])
    context = ExecutionContext(
        capability=capability,
        path="positive",
        trial_index=1,
        profile_id=candidate_fields["profile_id"],
        plugin_sha=candidate_fields["plugin_sha"],
        host_sha=candidate_fields["host_sha"],
        sdk_version=candidate_fields["sdk_version"],
        runner_version=candidate_fields["runner_version"],
        inventory_hash=candidate_fields["inventory_hash"],
        contract_hash=catalog.contract_hash,
        catalog_hash=catalog.catalog_hash,
        remaining_turn_budget=180,
    )
    bundle = asyncio.run(approval_followthrough(context))
    assert bundle.turn_count == 0
    assert bundle.outcomes["positive"].classification is ExecutionClassification.COMPLETE
    assert (
        bundle.outcomes["denial"].classification
        is ExecutionClassification.EXPECTED_NEGATIVE
    )
    assert bundle.outcomes["recovery"].classification is ExecutionClassification.COMPLETE
    assert bundle.outcomes["denial"].normalized_events[-1]["terminal_outcome"] == "denied"
    assert bundle.outcomes["recovery"].normalized_events[-1]["terminal_outcome"] == "completed"


def test_approval_followthrough_fails_closed_without_exact_sha_bindings(
    catalog, candidate_fields, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = catalog.by_id["active:approval-turn-tool-followthrough"]
    monkeypatch.delenv("HERMES_PARITY_PLUGIN_SHA", raising=False)
    monkeypatch.delenv("HERMES_AGENT_HOST_SHA", raising=False)
    context = ExecutionContext(
        capability=capability,
        path="positive",
        trial_index=1,
        profile_id=candidate_fields["profile_id"],
        plugin_sha=candidate_fields["plugin_sha"],
        host_sha=candidate_fields["host_sha"],
        sdk_version=candidate_fields["sdk_version"],
        runner_version=candidate_fields["runner_version"],
        inventory_hash=candidate_fields["inventory_hash"],
        contract_hash=catalog.contract_hash,
        catalog_hash=catalog.catalog_hash,
        remaining_turn_budget=180,
    )
    bundle = asyncio.run(approval_followthrough(context))
    assert all(
        outcome.classification is ExecutionClassification.ENVIRONMENT_BLOCKED
        for outcome in bundle.outcomes.values()
    )
