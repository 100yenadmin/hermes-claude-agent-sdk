from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.v4_release_ready import (
    OWNERSHIP_PREFLIGHTS, V4ReleaseReadyViolation, load_v4_release_ready,
    validate_v4_release_ready, write_v4_release_ready,
)

ROOT = Path(__file__).parents[2]

def _receipt() -> dict:
    sha, digest = "a" * 40, "b" * 64
    return {"schema_version": 4, "issue": 9, "status": "release_ready", "phase": "A", "publication_authorized": False, "merge_performed": False, "tag_created": False, "release_created": False, "artifact_immutable": True, "plugin_sha": sha, "host_sha": "c" * 40, "sdk_distribution": "claude-agent-sdk", "sdk_version": "0.2.151", "cli_version": "2.1.258", "model": "claude-fable-5-1", "wheel_sha256": digest, "sdist_sha256": "d" * 64, "profile_sha256": "e" * 64, "contract_sha256": "53864834496403388f3475291475fea70acfa3105609ad49f5edf75ad1c67d94", "map_sha256": "85583a44b797a58e6a3f6fcc9f4f5234b445b49c5ab6bf38b153e872473a16ff", "artifact_sha256": "2" * 64, "ownership_preflights": dict.fromkeys(OWNERSHIP_PREFLIGHTS, "PASS"), "parity": {"required_paths": 220, "observed_paths": 220, "required_trial_packets": 390, "observed_trial_packets": 390, "complete_paths": 220, "failed_paths": 0, "pending_paths": 0, "partial_paths": 0, "not_run_paths": 0, "environment_blocked_paths": 0}, "ci": {"plugin": {"status": "success", "head_sha": sha}, "host": {"status": "success", "head_sha": "c" * 40}}, "semantic_checkers": {"checker_a": {"blind": True, "status": "PASS", "score": 95}, "checker_b": {"blind": True, "status": "PASS", "score": 100}}, "package_lifecycle": dict.fromkeys(("install", "uninstall", "reinstall", "rollback"), "PASS"), "installed_subscription_gate": {"status": "PASS", "billing_classification": "subscription_included", "silent_fallback": False, "native_tools": False, "native_settings": False, "native_preset": False, "native_agent_events": False}, "parent_calls": 120, "child_calls": 16, "total_calls": 136, "reserve_calls": 44, "direct_sdk_calls": 0, "alternate_route_calls": 0, "proof_boundary": "Phase-A evidence receipt includes exact local provider-live runtime-safe evidence only; no merge, tag, release, publication, fleet, or customer proof."}
def test_validates_and_schema_accepts_same_valid_receipt() -> None:
    value = _receipt()
    checked = validate_v4_release_ready(value)
    assert checked["phase"] == "A" and (checked["parent_calls"], checked["child_calls"], checked["total_calls"], checked["reserve_calls"]) == (120, 16, 136, 44)
    schema = json.loads((ROOT / "qa/runtime-release-ready-receipt-v4.schema.json").read_text())
    validator = pytest.importorskip("jsonschema").Draft202012Validator(schema)
    assert validator.is_valid(value)


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(status="pending"), lambda r: r.update(phase="B"),
    lambda r: r.update(publication_authorized=True), lambda r: r.update(tag_created=True),
    lambda r: r.update(merge_performed=True, release_created=True),
    lambda r: r.update(artifact_immutable=1),
    lambda r: r.update(wheel_sha256="0" * 64), lambda r: r["parity"].update(observed_paths=219),
    lambda r: r["ownership_preflights"].update(delegate_owner="FAIL"),
    lambda r: r["semantic_checkers"]["checker_a"].update(score=94),
    lambda r: r["package_lifecycle"].update(rollback="FAIL"),
    lambda r: r["installed_subscription_gate"].update(billing_classification="unknown"),
    lambda r: r.update(provider_calls=0, network_calls=0), lambda r: r.update(direct_sdk_calls=1), lambda r: r.update(total_calls=135), lambda r: r.update(contract_sha256="f" * 64), lambda r: r.update(unknown="x"),
])
def test_schema_and_validator_reject_incomplete_or_unsafe_receipts(mutation) -> None:
    value = _receipt(); mutation(value)
    with pytest.raises(V4ReleaseReadyViolation): validate_v4_release_ready(value)
    schema = json.loads((ROOT / "qa/runtime-release-ready-receipt-v4.schema.json").read_text())
    assert not pytest.importorskip("jsonschema").Draft202012Validator(schema).is_valid(value)


def test_ci_must_bind_both_exact_candidate_heads() -> None:
    value = _receipt(); value["ci"]["host"]["head_sha"] = "d" * 40
    with pytest.raises(V4ReleaseReadyViolation): validate_v4_release_ready(value)


def test_persistence_is_create_only_and_round_trips(tmp_path: Path) -> None:
    destination = write_v4_release_ready(_receipt(), tmp_path / "receipt.json")
    assert load_v4_release_ready(destination)["status"] == "release_ready"
    with pytest.raises(V4ReleaseReadyViolation): write_v4_release_ready(_receipt(), destination)


def test_loader_rejects_symlink_before_resolution(tmp_path: Path) -> None:
    target = write_v4_release_ready(_receipt(), tmp_path / "receipt.json")
    link = tmp_path / "receipt-link.json"
    link.symlink_to(target)
    with pytest.raises(V4ReleaseReadyViolation, match="bounded regular file"):
        load_v4_release_ready(link)
