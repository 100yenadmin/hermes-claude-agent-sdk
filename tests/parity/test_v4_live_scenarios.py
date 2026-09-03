from __future__ import annotations

import copy
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.v4_live_scenarios import (
    LIVE_MAP_SHA256,
    LIVE_SCENARIO_COUNT,
    V4LiveScenarioViolation,
    load_v4_live_scenario_catalog,
    validate_v4_live_scenario_catalog,
)

ROOT = Path(__file__).parents[2]
MAP_PATH = ROOT / "qa" / "parity-v4-live-execution-map.yaml"


def _catalog():
    return load_v4_live_scenario_catalog(MAP_PATH)


def test_catalog_closes_exact_rows_and_frozen_budget() -> None:
    catalog = _catalog()
    proof = validate_v4_live_scenario_catalog(catalog, map_path=MAP_PATH)
    rows = catalog.scenarios
    assert len(rows) == LIVE_SCENARIO_COUNT == len({row.row_key for row in rows})
    assert proof["live_map_sha256"] == LIVE_MAP_SHA256
    assert (proof["parent_calls"], proof["child_calls"], proof["total_calls"], proof["turn_budget"]) == (120, 16, 136, 180)
    assert sum(row.parent_calls for row in rows) == 120
    assert sum(row.child_calls for row in rows) == 16


def test_scenarios_bind_inputs_paths_surfaces_and_child_ledger() -> None:
    rows = _catalog().scenarios
    assert all(row.input_kind == "synthetic_local_fixture" and row.local_only for row in rows)
    assert all(row.input_ref == f"synthetic/{row.row_key}" for row in rows)
    assert all(1 <= row.turn_count <= 4 and row.path_bundle == row.mandatory_paths for row in rows)
    assert all(row.required_surfaces and "transcript" in row.required_surfaces and "stream" in row.required_surfaces for row in rows)
    assert all(row.child_calls_authorized == bool(row.child_bindings) == bool(row.child_calls) for row in rows)
    assert all(row.provider_calls_by_path.get("positive") == row.turn_count for row in rows)
    assert all(row.provider_calls_by_path.get(path, 0) == 0 for row in rows for path in ("denial", "recovery"))
    assert sum(len(row.child_bindings) for row in rows) == 16


@pytest.mark.parametrize("mutation", [
    lambda value: value["scenarios"].pop(),
    lambda value: value["scenarios"].__setitem__(1, copy.deepcopy(value["scenarios"][0])),
    lambda value: value["scenarios"][0].update({"row_key": "unknown/row"}),
    lambda value: value["scenarios"][0].update({"turn_count": 5}),
])
def test_catalog_rejects_missing_duplicate_unknown_or_unbounded_rows(mutation) -> None:
    catalog = _catalog(); value = copy.deepcopy(catalog.to_dict()); mutation(value)
    with pytest.raises(V4LiveScenarioViolation):
        validate_v4_live_scenario_catalog(value, map_path=MAP_PATH)


def test_catalog_rejects_forbidden_route_drift_and_is_deterministic() -> None:
    first, second = _catalog(), _catalog()
    assert first.to_dict() == second.to_dict() and first.catalog_sha256 == second.catalog_sha256
    assert isinstance(first.scenarios, tuple)
    assert {"direct_provider", "direct_sdk", "raw_auth_material", "unmanaged_network"} <= set(
        first.to_dict()["forbidden_routes"]
    )
    assert not {"provider", "auth", "network"} & set(first.to_dict()["forbidden_routes"])
    value = copy.deepcopy(first.to_dict()); value["forbidden_routes"] = ["normal_provider"]
    with pytest.raises(V4LiveScenarioViolation):
        validate_v4_live_scenario_catalog(value, map_path=MAP_PATH)
    value = copy.deepcopy(first.to_dict()); value["target"]["catalog_invokes_provider"] = True
    with pytest.raises(V4LiveScenarioViolation):
        validate_v4_live_scenario_catalog(value, map_path=MAP_PATH)
    assert first.to_dict() == second.to_dict()


def test_catalog_has_no_expected_runtime_events_or_live_outcome_claim() -> None:
    value = _catalog().to_dict()
    assert all(not {"expected_events", "classification", "terminal_status"} & set(row) for row in value["scenarios"])
    assert value["target"]["observation_mode"] == "host_surfaces_only"
    assert value["target"]["catalog_invokes_provider"] is False
