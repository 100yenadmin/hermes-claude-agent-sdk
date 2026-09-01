from __future__ import annotations

import copy
import json

import pytest
import yaml

from hermes_claude_agent_sdk.parity.cli import main
from hermes_claude_agent_sdk.parity.hashing import sha256_value
from hermes_claude_agent_sdk.parity.results import (
    ExecutionClassification,
    ResultViolation,
)
from hermes_claude_agent_sdk.parity.runner import (
    ExecutionBundle,
    ExecutionOutcome,
    ExecutorRegistry,
    run_catalog,
)
from hermes_claude_agent_sdk.parity.trace import normalized_path_events

from .conftest import CATALOG_PATH


def _outcome(context):
    denial = context.path == "denial"
    return ExecutionOutcome(
        classification=(
            ExecutionClassification.EXPECTED_NEGATIVE
            if denial
            else ExecutionClassification.COMPLETE
        ),
        billing_classification="subscription_included",
        normalized_events=normalized_path_events(
            context.capability.expected_trace,
            path=context.path,
            evidence_hash="f" * 64,
        ),
        primary_proof_hash="a" * 64,
        secondary_proof_hash="b" * 64,
    )


def _run_fields(tmp_path, candidate_fields):
    return {
        "lane": "rc",
        "profile_id": candidate_fields["profile_id"],
        "profile_hash": candidate_fields["profile_hash"],
        "plugin_sha": candidate_fields["plugin_sha"],
        "host_sha": candidate_fields["host_sha"],
        "sdk_version": candidate_fields["sdk_version"],
        "runner_version": candidate_fields["runner_version"],
        "inventory_hash": candidate_fields["inventory_hash"],
        "output": tmp_path,
    }


def test_missing_executor_writes_pending_packets_and_exit_75(
    catalog, candidate_fields, tmp_path
) -> None:
    packets, report = run_catalog(
        catalog,
        registry=ExecutorRegistry(),
        resume=False,
        capability_ids=("v2:parent-01",),
        **_run_fields(tmp_path, candidate_fields),
    )
    assert len(packets) == 3
    assert all(packet.classification is ExecutionClassification.PENDING for packet in packets)
    assert all(packet.reason_code == "executor_not_registered" for packet in packets)
    assert report.exit_code == 75
    assert len(list(tmp_path.glob("*__*__trial-*.json"))) == 3


def test_registered_executor_produces_sanitized_path_evidence_and_resume_is_read_only(
    catalog, candidate_fields, tmp_path
) -> None:
    capability = catalog.by_id["v2:parent-01"]
    registry = ExecutorRegistry()
    registry.register(capability.execution_id, _outcome)
    packets, _ = run_catalog(
        catalog,
        registry=registry,
        resume=False,
        capability_ids=(capability.capability_id,),
        **_run_fields(tmp_path, candidate_fields),
    )
    assert [packet.classification for packet in packets] == [
        ExecutionClassification.COMPLETE,
        ExecutionClassification.EXPECTED_NEGATIVE,
        ExecutionClassification.COMPLETE,
    ]

    def must_not_run(_context):
        raise AssertionError("resume invoked executor")

    resume_registry = ExecutorRegistry()
    resume_registry.register(capability.execution_id, must_not_run)
    resumed, report = run_catalog(
        catalog,
        registry=resume_registry,
        resume=True,
        capability_ids=(capability.capability_id,),
        **_run_fields(tmp_path, candidate_fields),
    )
    assert [packet.packet_hash for packet in resumed] == [packet.packet_hash for packet in packets]
    assert report.exit_code == 75  # A thin run never passes the whole RC lane.


def test_runner_refuses_overwrite_without_resume(catalog, candidate_fields, tmp_path) -> None:
    capability = catalog.by_id["v2:parent-01"]
    registry = ExecutorRegistry()
    registry.register(capability.execution_id, _outcome)
    kwargs = {
        "catalog": catalog,
        "registry": registry,
        "resume": False,
        "capability_ids": (capability.capability_id,),
        **_run_fields(tmp_path, candidate_fields),
    }
    run_catalog(**kwargs)
    with pytest.raises(ResultViolation, match="refuses an existing"):
        run_catalog(**kwargs)


def test_consequential_capability_runs_strict_three_trials_per_path(
    catalog, candidate_fields, tmp_path
) -> None:
    capability = catalog.by_id["active:approval-turn-tool-followthrough"]
    registry = ExecutorRegistry()
    registry.register(capability.execution_id, _outcome)
    packets, _ = run_catalog(
        catalog,
        registry=registry,
        resume=False,
        capability_ids=(capability.capability_id,),
        **_run_fields(tmp_path, candidate_fields),
    )
    assert len(packets) == 9
    assert {packet.trial_index for packet in packets} == {1, 2, 3}


def test_combined_executor_runs_once_for_positive_denial_and_recovery(
    catalog, candidate_fields, tmp_path
) -> None:
    capability = catalog.by_id["v2:parent-01"]
    calls = []

    def combined(context):
        calls.append(context)
        return ExecutionBundle(
            outcomes={
                "positive": ExecutionOutcome(
                    classification=ExecutionClassification.COMPLETE,
                    billing_classification="subscription_included",
                    normalized_events=(
                        {"sequence": 1, "kind": "terminal", "terminal_outcome": "completed"},
                    ),
                    primary_proof_hash="a" * 64,
                    secondary_proof_hash="b" * 64,
                    turn_count=1,
                ),
                "denial": ExecutionOutcome(
                    classification=ExecutionClassification.EXPECTED_NEGATIVE,
                    billing_classification="subscription_included",
                    normalized_events=(
                        {"sequence": 1, "kind": "terminal", "terminal_outcome": "denied"},
                    ),
                    primary_proof_hash="c" * 64,
                    secondary_proof_hash="d" * 64,
                    turn_count=0,
                ),
                "recovery": ExecutionOutcome(
                    classification=ExecutionClassification.COMPLETE,
                    billing_classification="subscription_included",
                    normalized_events=(
                        {"sequence": 1, "kind": "terminal", "terminal_outcome": "completed"},
                    ),
                    primary_proof_hash="e" * 64,
                    secondary_proof_hash="f" * 64,
                    turn_count=0,
                ),
            },
            turn_count=1,
        )

    registry = ExecutorRegistry()
    registry.register(capability.execution_id, combined)
    packets, _ = run_catalog(
        catalog,
        registry=registry,
        resume=False,
        capability_ids=(capability.capability_id,),
        **_run_fields(tmp_path, candidate_fields),
    )
    assert len(calls) == 1
    assert len(packets) == 3
    assert sum(packet.turn_count for packet in packets) == 1
    manifest = json.loads((tmp_path / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["turn_budget"] == 180
    assert sum(item["turn_count"] for item in manifest["executions"]) == 1
    assert manifest["executions"][0]["scope"] == "bundle"


@pytest.mark.parametrize(
    "reason_code",
    (
        "active_subscription_limit_reached",
        "active_synthetic_provider_notice",
        "native_subscription_limit_reached",
        "native_synthetic_provider_notice",
    ),
)
def test_global_subscription_limit_block_stops_the_remaining_catalog(
    catalog, candidate_fields, tmp_path, reason_code
) -> None:
    first = catalog.by_id["v2:parent-01"]
    second = catalog.by_id["v2:parent-02"]
    calls = []

    def blocked(context):
        calls.append(context.capability.capability_id)
        return ExecutionBundle(
            outcomes={
                path: ExecutionOutcome(
                    classification=ExecutionClassification.ENVIRONMENT_BLOCKED,
                    billing_classification="none",
                    reason_code=reason_code,
                    turn_count=1 if path == "positive" else 0,
                )
                for path in ("positive", "denial", "recovery")
            },
            turn_count=1,
        )

    registry = ExecutorRegistry()
    registry.register(first.execution_id, blocked)
    registry.register(second.execution_id, blocked)

    packets, report = run_catalog(
        catalog,
        registry=registry,
        resume=False,
        capability_ids=(first.capability_id, second.capability_id),
        **_run_fields(tmp_path, candidate_fields),
    )

    assert calls == [first.capability_id]
    assert len(packets) == 1
    assert len(list(tmp_path.glob("*__*__trial-*.json"))) == 3
    assert report.exit_code == 75


def test_interrupted_run_checkpoints_completed_bundle_for_resume(
    catalog, candidate_fields, tmp_path
) -> None:
    first = catalog.by_id["v2:parent-01"]
    second = catalog.by_id["v2:parent-02"]
    first_calls = 0

    def completed_bundle(_context):
        nonlocal first_calls
        first_calls += 1
        outcomes = {}
        for path in ("positive", "denial", "recovery"):
            outcomes[path] = ExecutionOutcome(
                classification=(
                    ExecutionClassification.EXPECTED_NEGATIVE
                    if path == "denial"
                    else ExecutionClassification.COMPLETE
                ),
                billing_classification="subscription_included",
                normalized_events=normalized_path_events(
                    first.expected_trace,
                    path=path,
                    evidence_hash="f" * 64,
                ),
                primary_proof_hash="a" * 64,
                secondary_proof_hash="b" * 64,
            )
        return ExecutionBundle(outcomes=outcomes, turn_count=0)

    def interrupt(_context):
        raise KeyboardInterrupt

    registry = ExecutorRegistry()
    registry.register(first.execution_id, completed_bundle)
    registry.register(second.execution_id, interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_catalog(
            catalog,
            registry=registry,
            resume=False,
            capability_ids=(first.capability_id, second.capability_id),
            **_run_fields(tmp_path, candidate_fields),
        )

    packet_paths = sorted(tmp_path.glob("*__*__trial-*.json"))
    assert first_calls == 1
    assert len(packet_paths) == 3
    manifest = json.loads((tmp_path / "run-manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["packet_hashes"]) == 3
    assert len(manifest["executions"]) == 1

    def must_not_rerun(_context):
        raise AssertionError("resume reran the completed bundle")

    resumed_registry = ExecutorRegistry()
    resumed_registry.register(first.execution_id, must_not_rerun)
    resumed_registry.register(second.execution_id, _outcome)
    packets, _ = run_catalog(
        catalog,
        registry=resumed_registry,
        resume=True,
        capability_ids=(first.capability_id, second.capability_id),
        **_run_fields(tmp_path, candidate_fields),
    )
    assert len(packets) == 6
    assert len(list(tmp_path.glob("*__*__trial-*.json"))) == 6


def test_runtime_bundle_enforces_one_100_turn_campaign(
    catalog, candidate_fields, tmp_path
) -> None:
    capability = catalog.by_id["runtime:active-100-turn"]

    def campaign(_context):
        def events(path):
            return normalized_path_events(
                capability.expected_trace,
                path=path,
                evidence_hash="f" * 64,
            )

        outcomes = {
            "positive": ExecutionOutcome(
                classification=ExecutionClassification.COMPLETE,
                billing_classification="subscription_included",
                normalized_events=events("positive"),
                primary_proof_hash="a" * 64,
                secondary_proof_hash="b" * 64,
                turn_count=98,
            ),
            "denial": ExecutionOutcome(
                classification=ExecutionClassification.EXPECTED_NEGATIVE,
                billing_classification="subscription_included",
                normalized_events=events("denial"),
                primary_proof_hash="c" * 64,
                secondary_proof_hash="d" * 64,
                turn_count=1,
            ),
            "recovery": ExecutionOutcome(
                classification=ExecutionClassification.COMPLETE,
                billing_classification="subscription_included",
                normalized_events=events("recovery"),
                primary_proof_hash="e" * 64,
                secondary_proof_hash="f" * 64,
                turn_count=1,
            ),
        }
        return ExecutionBundle(outcomes=outcomes, turn_count=100)

    registry = ExecutorRegistry()
    registry.register(capability.execution_id, campaign)
    fields = _run_fields(tmp_path, candidate_fields)
    fields["lane"] = "runtime"
    packets, report = run_catalog(
        catalog,
        registry=registry,
        resume=False,
        capability_ids=(capability.capability_id,),
        **fields,
    )
    assert report.exit_code == 0
    assert sum(packet.turn_count for packet in packets) == 100
    manifest = json.loads((tmp_path / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["turn_budget"] == 100
    assert sum(item["turn_count"] for item in manifest["executions"]) == 100


def test_passing_runtime_campaign_cannot_claim_fewer_than_100_turns(
    catalog, candidate_fields, tmp_path
) -> None:
    capability = catalog.by_id["runtime:active-100-turn"]

    def short_campaign(_context):
        outcomes = {}
        for path in ("positive", "denial", "recovery"):
            outcomes[path] = ExecutionOutcome(
                classification=(
                    ExecutionClassification.EXPECTED_NEGATIVE
                    if path == "denial"
                    else ExecutionClassification.COMPLETE
                ),
                billing_classification="subscription_included",
                normalized_events=normalized_path_events(
                    capability.expected_trace,
                    path=path,
                    evidence_hash="f" * 64,
                ),
                primary_proof_hash="a" * 64,
                secondary_proof_hash="b" * 64,
                turn_count=99 if path == "positive" else 0,
            )
        return ExecutionBundle(outcomes=outcomes, turn_count=99)

    registry = ExecutorRegistry()
    registry.register(capability.execution_id, short_campaign)
    fields = _run_fields(tmp_path, candidate_fields)
    fields["lane"] = "runtime"
    with pytest.raises(ResultViolation, match="exactly 100 turns"):
        run_catalog(
            catalog,
            registry=registry,
            resume=False,
            capability_ids=(capability.capability_id,),
            **fields,
        )


def test_executor_cannot_report_turns_beyond_lane_budget(
    catalog, candidate_fields, tmp_path
) -> None:
    capability = catalog.by_id["v2:parent-01"]

    def over_budget(_context):
        return ExecutionOutcome(
            classification=ExecutionClassification.PENDING,
            billing_classification="none",
            turn_count=181,
            reason_code="budget_violation",
        )

    registry = ExecutorRegistry()
    registry.register(capability.execution_id, over_budget)
    with pytest.raises(ResultViolation, match="turn budget"):
        run_catalog(
            catalog,
            registry=registry,
            resume=False,
            capability_ids=(capability.capability_id,),
            **_run_fields(tmp_path, candidate_fields),
        )


def test_resume_rejects_tampered_manifest(catalog, candidate_fields, tmp_path) -> None:
    run_catalog(
        catalog,
        registry=ExecutorRegistry(),
        resume=False,
        capability_ids=("v2:parent-01",),
        **_run_fields(tmp_path, candidate_fields),
    )
    manifest_path = tmp_path / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["turn_budget"] = 181
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ResultViolation, match="exact candidate and lane|manifest hash"):
        run_catalog(
            catalog,
            registry=ExecutorRegistry(),
            resume=True,
            capability_ids=("v2:parent-01",),
            **_run_fields(tmp_path, candidate_fields),
        )


def _inventory_document() -> dict:
    tools = [
        {
            "name": "repo_read",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    ]
    return {
        "schema_version": 1,
        "profile_id": "fable-v3-isolated",
        "profile_hash": "3" * 64,
        "declared_tools": tools,
        "observed_tools": copy.deepcopy(tools),
    }


def _profile_document(profile_id: str = "fable-v3-isolated") -> dict:
    return {
        "schema_version": 1,
        "profile_id": profile_id,
        "isolation_kind": "in_process_fixture",
        "persistent": False,
        "shared_state": False,
        "customer_data": False,
        "configuration_hash": "9" * 64,
    }


def _write_cli_manifests(tmp_path, *, profile_id: str = "fable-v3-isolated"):
    profile_document = _profile_document(profile_id)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(profile_document), encoding="utf-8")
    inventory_document = _inventory_document()
    inventory_document["profile_id"] = profile_id
    inventory_document["profile_hash"] = sha256_value(profile_document)
    inventory = tmp_path / "tools.yaml"
    inventory.write_text(yaml.safe_dump(inventory_document), encoding="utf-8")
    return profile, inventory


def test_cli_inventory_passes_only_with_exact_dynamic_tool_schema(tmp_path) -> None:
    profile, inventory = _write_cli_manifests(tmp_path)
    output = tmp_path / "inventory.json"
    exit_code = main(
        [
            "inventory",
            "--catalog",
            str(CATALOG_PATH),
            "--profile",
            "fable-v3-isolated",
            "--tool-inventory",
            str(inventory),
            "--profile-manifest",
            str(profile),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    assert output.is_file()


def test_cli_inventory_capture_accepts_an_explicit_yaml_target(tmp_path) -> None:
    profile, _ = _write_cli_manifests(tmp_path)
    output = tmp_path / "captured-tools.yaml"
    exit_code = main(
        [
            "inventory",
            "--capture",
            "--catalog",
            str(CATALOG_PATH),
            "--lane",
            "rc",
            "--profile",
            "fable-v3-isolated",
            "--profile-manifest",
            str(profile),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    assert output.is_file()
    assert (tmp_path / "inventory-rc.json").is_file()
    captured = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert [item["name"] for item in captured["observed_tools"]] == [
        "cron",
        "exec",
        "parity_harmless_tool",
        "read",
        "write",
    ]


def test_cli_run_without_registered_executor_is_pending_not_false_green(tmp_path) -> None:
    profile, inventory = _write_cli_manifests(tmp_path)
    output = tmp_path / "results"
    exit_code = main(
        [
            "run",
            "--catalog",
            str(CATALOG_PATH),
            "--lane",
            "rc",
            "--profile",
            "fable-v3-isolated",
            "--plugin-sha",
            "1" * 40,
            "--host-sha",
            "2" * 40,
            "--tool-inventory",
            str(inventory),
            "--profile-manifest",
            str(profile),
            "--output",
            str(output),
            "--capability-id",
            "v2:parent-01",
        ]
    )
    assert exit_code == 75
    assert (output / "grade-rc.json").is_file()


def test_cli_rejects_shared_profile_before_execution(tmp_path) -> None:
    profile, inventory = _write_cli_manifests(tmp_path, profile_id="shared-eva")
    exit_code = main(
        [
            "run",
            "--catalog",
            str(CATALOG_PATH),
            "--profile",
            "shared-eva",
            "--plugin-sha",
            "1" * 40,
            "--host-sha",
            "2" * 40,
            "--tool-inventory",
            str(inventory),
            "--profile-manifest",
            str(profile),
            "--output",
            str(tmp_path / "results"),
        ]
    )
    assert exit_code == 2
    assert not (tmp_path / "results").exists()
