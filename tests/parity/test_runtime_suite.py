from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path

from hermes_claude_agent_sdk.parity.executors import EXECUTORS
from hermes_claude_agent_sdk.parity.results import ExecutionClassification
from hermes_claude_agent_sdk.parity.runner import ExecutionContext
from hermes_claude_agent_sdk.parity.runtime_suite import (
    RUNTIME_EXECUTION_ID,
    _load_release_receipt,
    _running_package_matches_wheel,
    _turn_content,
    active_runtime_100_turn,
    runtime_execution_ids,
)


def _context(catalog, candidate_fields, **overrides) -> ExecutionContext:
    fields = {
        "capability": catalog.by_id["runtime:active-100-turn"],
        "path": "positive",
        "trial_index": 1,
        "profile_id": candidate_fields["profile_id"],
        "profile_hash": candidate_fields["profile_hash"],
        "plugin_sha": candidate_fields["plugin_sha"],
        "host_sha": candidate_fields["host_sha"],
        "sdk_version": candidate_fields["sdk_version"],
        "runner_version": candidate_fields["runner_version"],
        "inventory_hash": candidate_fields["inventory_hash"],
        "contract_hash": catalog.contract_hash,
        "catalog_hash": catalog.catalog_hash,
        "remaining_turn_budget": 100,
        "repo_root": str(catalog.path.parent.parent),
    }
    fields.update(overrides)
    return ExecutionContext(**fields)


def test_runtime_executor_is_registered_as_one_exact_campaign() -> None:
    assert runtime_execution_ids() == (RUNTIME_EXECUTION_ID,)
    assert EXECUTORS[RUNTIME_EXECUTION_ID] is active_runtime_100_turn
    markers = [f"TURN_{index:03d}_OK" for index in range(1, 101)]
    assert len(set(markers)) == 100
    for index, marker in enumerate(markers, 1):
        assert marker in str(_turn_content(index))


def test_runtime_executor_fails_closed_for_nonpersistent_fixture_profile(
    catalog, candidate_fields, monkeypatch
) -> None:
    monkeypatch.setattr(
        "hermes_claude_agent_sdk.parity.runtime_suite._exact_source_preflight",
        lambda _context, _root: None,
    )
    context = _context(
        catalog,
        candidate_fields,
        profile_isolation_kind="in_process_fixture",
        profile_persistent=False,
    )

    bundle = asyncio.run(active_runtime_100_turn(context))

    assert bundle.turn_count == 0
    assert all(
        outcome.classification is ExecutionClassification.ENVIRONMENT_BLOCKED
        and outcome.reason_code == "runtime_profile_not_persistent_isolated"
        for outcome in bundle.outcomes.values()
    )


def test_release_ready_receipt_binds_issue_artifact_and_exact_heads(
    catalog, candidate_fields, tmp_path: Path
) -> None:
    context = _context(catalog, candidate_fields)
    wheel_hash = "a" * 64
    receipt = {
        "schema_version": 1,
        "issue": 9,
        "status": "release_ready",
        "artifact_immutable": True,
        "plugin_sha": context.plugin_sha,
        "host_sha": context.host_sha,
        "sdk_version": context.sdk_version,
        "wheel_sha256": wheel_hash,
        "contract_hash": context.contract_hash,
        "catalog_hash": context.catalog_hash,
    }
    path = tmp_path / "release-ready.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")

    assert _load_release_receipt(path, context=context, wheel_hash=wheel_hash)
    receipt["artifact_immutable"] = False
    path.write_text(json.dumps(receipt), encoding="utf-8")
    assert not _load_release_receipt(path, context=context, wheel_hash=wheel_hash)


def test_running_package_must_byte_match_the_immutable_wheel(tmp_path: Path) -> None:
    import hermes_claude_agent_sdk

    installed = Path(hermes_claude_agent_sdk.__file__).resolve()
    wheel = tmp_path / "candidate.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "hermes_claude_agent_sdk/__init__.py",
            installed.read_bytes(),
        )
    assert _running_package_matches_wheel(wheel)

    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("hermes_claude_agent_sdk/__init__.py", b"tampered")
    assert not _running_package_matches_wheel(wheel)
