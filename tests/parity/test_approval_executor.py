from __future__ import annotations

import asyncio
import os
import sys
from importlib import metadata
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


def _context(catalog, candidate_fields: dict[str, str]) -> ExecutionContext:
    capability = catalog.by_id["active:approval-turn-tool-followthrough"]
    return ExecutionContext(
        capability=capability,
        path="positive",
        trial_index=1,
        profile_id=candidate_fields["profile_id"],
        profile_hash=candidate_fields["profile_hash"],
        plugin_sha=candidate_fields["plugin_sha"],
        host_sha=candidate_fields["host_sha"],
        sdk_version=candidate_fields["sdk_version"],
        runner_version=candidate_fields["runner_version"],
        inventory_hash=candidate_fields["inventory_hash"],
        contract_hash=catalog.contract_hash,
        catalog_hash=catalog.catalog_hash,
        remaining_turn_budget=180,
    )


@pytest.mark.parametrize("sdk_version", ["0.2.144", "0.2.151"])
def test_approval_followthrough_uses_exact_host_allow_deny_and_recovery(
    catalog,
    candidate_fields,
    monkeypatch: pytest.MonkeyPatch,
    sdk_version: str,
) -> None:
    candidate_fields["sdk_version"] = sdk_version
    monkeypatch.setattr(metadata, "version", lambda _: sdk_version)
    monkeypatch.setenv("HERMES_PARITY_PLUGIN_SHA", candidate_fields["plugin_sha"])
    monkeypatch.setenv("HERMES_AGENT_HOST_SHA", candidate_fields["host_sha"])
    context = _context(catalog, candidate_fields)
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


def test_approval_followthrough_fails_closed_on_sdk_identity_mismatch(
    catalog, candidate_fields, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate_fields["sdk_version"] = "0.2.151"
    monkeypatch.setattr(metadata, "version", lambda _: "0.2.144")
    monkeypatch.setenv("HERMES_PARITY_PLUGIN_SHA", candidate_fields["plugin_sha"])
    monkeypatch.setenv("HERMES_AGENT_HOST_SHA", candidate_fields["host_sha"])

    bundle = asyncio.run(approval_followthrough(_context(catalog, candidate_fields)))

    assert {
        outcome.reason_code for outcome in bundle.outcomes.values()
    } == {"sdk_version_mismatch"}


@pytest.mark.parametrize(
    ("installed_version", "reason_code"),
    [
        ("malformed", "sdk_version_malformed"),
        ("0.2.143", "sdk_version_unsupported"),
        ("0.2.152", "sdk_version_unsupported"),
    ],
)
def test_approval_followthrough_fails_closed_on_invalid_installed_sdk(
    catalog,
    candidate_fields,
    monkeypatch: pytest.MonkeyPatch,
    installed_version: str,
    reason_code: str,
) -> None:
    candidate_fields["sdk_version"] = installed_version
    monkeypatch.setattr(metadata, "version", lambda _: installed_version)
    monkeypatch.setenv("HERMES_PARITY_PLUGIN_SHA", candidate_fields["plugin_sha"])
    monkeypatch.setenv("HERMES_AGENT_HOST_SHA", candidate_fields["host_sha"])

    bundle = asyncio.run(approval_followthrough(_context(catalog, candidate_fields)))

    assert {
        outcome.reason_code for outcome in bundle.outcomes.values()
    } == {reason_code}


def test_approval_followthrough_fails_closed_when_sdk_distribution_is_missing(
    catalog, candidate_fields, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing_distribution(distribution: str) -> str:
        raise metadata.PackageNotFoundError(distribution)

    candidate_fields["sdk_version"] = "0.2.151"
    monkeypatch.setattr(metadata, "version", missing_distribution)
    monkeypatch.setenv("HERMES_PARITY_PLUGIN_SHA", candidate_fields["plugin_sha"])
    monkeypatch.setenv("HERMES_AGENT_HOST_SHA", candidate_fields["host_sha"])

    bundle = asyncio.run(approval_followthrough(_context(catalog, candidate_fields)))

    assert {
        outcome.reason_code for outcome in bundle.outcomes.values()
    } == {"sdk_distribution_unavailable"}


def test_approval_followthrough_fails_closed_without_exact_sha_bindings(
    catalog, candidate_fields, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        metadata,
        "version",
        lambda _: candidate_fields["sdk_version"],
    )
    monkeypatch.delenv("HERMES_PARITY_PLUGIN_SHA", raising=False)
    monkeypatch.delenv("HERMES_AGENT_HOST_SHA", raising=False)
    bundle = asyncio.run(approval_followthrough(_context(catalog, candidate_fields)))
    assert all(
        outcome.classification is ExecutionClassification.ENVIRONMENT_BLOCKED
        for outcome in bundle.outcomes.values()
    )
