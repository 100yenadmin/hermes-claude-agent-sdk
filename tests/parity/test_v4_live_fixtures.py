from __future__ import annotations

import copy
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.v4_live_fixtures import (
    LIVE_FIXTURE_COUNT,
    LIVE_MAP_SHA256,
    V4LiveFixtureViolation,
    build_v4_live_fixture_manifest,
    load_v4_live_fixture_manifest,
    validate_v4_live_fixture_manifest,
)

ROOT = Path(__file__).parents[2]
MAP_PATH = ROOT / "qa" / "parity-v4-live-execution-map.yaml"
MANIFEST_PATH = ROOT / "qa" / "parity-v4-live-fixtures.yaml"


def test_manifest_is_map_bound_and_closes_all_fixture_accounting() -> None:
    manifest = load_v4_live_fixture_manifest(MANIFEST_PATH)
    proof = validate_v4_live_fixture_manifest(manifest, map_path=MAP_PATH)
    assert len(manifest.fixtures) == LIVE_FIXTURE_COUNT == 70
    assert proof["live_map_sha256"] == LIVE_MAP_SHA256
    assert {key: proof[key] for key in ("parent_calls", "child_calls", "total_calls", "turn_budget", "reserve_calls")} == {
        "parent_calls": 134, "child_calls": 16, "total_calls": 150, "turn_budget": 180, "reserve_calls": 30,
    }
    assert len({fixture.row_key for fixture in manifest.fixtures}) == 70
    assert all(fixture["fixture_id"] == f"synthetic/{fixture.row_key}" for fixture in manifest.fixtures)


def test_manifest_binds_recipe_intents_policies_and_children() -> None:
    manifest = load_v4_live_fixture_manifest(MANIFEST_PATH)
    rows = {fixture.row_key: fixture for fixture in manifest.fixtures}
    assert rows["openclaw_active/source-docs-discovery-report"]["turn_count"] == 2
    assert rows["openclaw_active/memory-recall"]["turn_count"] == 2
    assert rows["openclaw_active/thread-memory-isolation"]["turn_count"] == 4
    assert rows["openclaw_active/config-restart-capability-flip"]["turn_count"] == 2
    assert rows["v2_non_soak/ORCH-05"]["child_call_ids"] == ["F3-C05", "F3-C06"]
    assert rows["openclaw_active/subagent-fanout-synthesis"]["child_binding_refs"] == ["F3-C10", "F3-C11"]
    assert all(fixture["hermes_tool_intents"] == fixture["tool_intents"] for fixture in manifest.fixtures)
    assert all(fixture["path_policy"] in {"local_only", "host_denial_local_recovery"} for fixture in manifest.fixtures)
    assert manifest["target"]["fixture_mode"] == "synthetic_local_only"
    assert manifest["target"]["validation_mode"] == "provider_free"
    assert manifest["target"]["external_delivery"] == "never"


def test_builder_is_deterministic_and_matches_checked_in_manifest() -> None:
    loaded = load_v4_live_fixture_manifest(MANIFEST_PATH)
    built = build_v4_live_fixture_manifest(map_path=MAP_PATH)
    assert loaded.to_dict() == built.to_dict()
    assert loaded.manifest_sha256 == loaded.catalog_sha256 == built.manifest_sha256


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["fixtures"].pop(),
        lambda value: value["fixtures"].__setitem__(1, copy.deepcopy(value["fixtures"][0])),
        lambda value: value["fixtures"][0].update({"row_key": "unknown/fixture"}),
        lambda value: value["fixtures"][0].pop("turn_recipe"),
        lambda value: value["fixtures"][0].update({"fixture_id": "raw_prompt/body"}),
        lambda value: value["fixtures"][0].update({"child_call_ids": ["F3-C01"]}),
        lambda value: value["target"].update({"validation_mode": "direct_sdk"}),
        lambda value: value["budget"].update({"child_calls": 15}),
    ],
)
def test_manifest_rejects_malformed_unknown_duplicate_or_unsafe_fixture(mutation) -> None:
    value = copy.deepcopy(load_v4_live_fixture_manifest(MANIFEST_PATH).to_dict())
    mutation(value)
    with pytest.raises(V4LiveFixtureViolation):
        validate_v4_live_fixture_manifest(value, map_path=MAP_PATH)


def test_manifest_rejects_missing_or_extra_root_fields() -> None:
    value = load_v4_live_fixture_manifest(MANIFEST_PATH).to_dict()
    value.pop("manifest_sha256")
    with pytest.raises(V4LiveFixtureViolation):
        validate_v4_live_fixture_manifest(value, map_path=MAP_PATH)
    value = load_v4_live_fixture_manifest(MANIFEST_PATH).to_dict()
    value["unexpected"] = True
    with pytest.raises(V4LiveFixtureViolation):
        validate_v4_live_fixture_manifest(value, map_path=MAP_PATH)
