from __future__ import annotations

import json

import pytest

from hermes_claude_agent_sdk.parity.profile import ProfileViolation, load_profile_manifest


def _manifest() -> dict:
    return {
        "schema_version": 1,
        "profile_id": "fable-v3-isolated",
        "isolation_kind": "in_process_fixture",
        "persistent": False,
        "shared_state": False,
        "customer_data": False,
        "configuration_hash": "9" * 64,
    }


def _write(tmp_path, value: dict):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_profile_manifest_binds_sanitized_isolation_state(tmp_path) -> None:
    profile = load_profile_manifest(
        _write(tmp_path, _manifest()), expected_profile="fable-v3-isolated"
    )
    assert profile.isolation_kind == "in_process_fixture"
    assert profile.persistent is False
    assert len(profile.manifest_hash) == 64


@pytest.mark.parametrize(("field", "value"), [("shared_state", True), ("customer_data", True)])
def test_profile_manifest_rejects_shared_or_customer_state(tmp_path, field, value) -> None:
    manifest = _manifest()
    manifest[field] = value
    with pytest.raises(ProfileViolation, match="forbidden"):
        load_profile_manifest(_write(tmp_path, manifest))


def test_profile_manifest_rejects_persistent_fixture_claim(tmp_path) -> None:
    manifest = _manifest()
    manifest["persistent"] = True
    with pytest.raises(ProfileViolation, match="cannot claim persistent"):
        load_profile_manifest(_write(tmp_path, manifest))
