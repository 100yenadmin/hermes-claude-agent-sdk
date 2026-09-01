from __future__ import annotations

import copy
import json

import pytest
import yaml

from hermes_claude_agent_sdk.parity.catalog import CatalogViolation, load_catalog

from .conftest import CATALOG_PATH


def test_catalog_binds_exact_source_inventory_and_runtime_lane(catalog) -> None:
    assert catalog.version == "3.0.0"
    assert len(catalog.for_lane("rc")) == 124
    assert len(catalog.for_lane("runtime")) == 1
    assert dict(catalog.source_counts) == {
        "agent_sdk_boundary": 23,
        "clawprobench_native": 36,
        "openclaw_active": 12,
        "runtime_active": 1,
        "v2_non_soak": 53,
    }
    assert len(catalog.contract_hash) == 64
    assert len(catalog.catalog_hash) == 64
    assert len(catalog.file_hash) == 64
    assert "v2:soak-01" not in catalog.by_id
    assert catalog.contract["profile_policy"]["allowed_ids"] == (
        "fable-v3-isolated",
    )


def test_boundary_ledger_has_exactly_one_status_per_source_row(catalog) -> None:
    rows = [item for item in catalog.capabilities if item.source_pack == "agent_sdk_boundary"]
    assert len(rows) == 23
    assert all(
        item.sdk_ledger_status
        in {
            "covered_current",
            "equivalent_host",
            "requires_0_3_239",
            "not_runtime_applicable",
        }
        for item in rows
    )


def test_boundary_ledger_matches_catalog_and_has_no_proofless_pending_target(catalog) -> None:
    ledger = yaml.safe_load(
        (CATALOG_PATH.parent / "agent-sdk-boundary-ledger-v3.yaml").read_text(
            encoding="utf-8"
        )
    )
    rows = ledger["rows"]
    catalog_rows = {
        item.source_item_id: item
        for item in catalog.capabilities
        if item.source_pack == "agent_sdk_boundary"
    }
    assert len(rows) == 23
    assert {row["id"] for row in rows} == set(catalog_rows)
    assert all(row["release_blocking"] is True for row in rows)
    assert all(row["evidence_refs"] for row in rows)
    assert all(row["status"] == catalog_rows[row["id"]].sdk_ledger_status for row in rows)
    assert all(row["evidence_state"] == "existing_candidate" for row in rows)
    assert all(
        not reference.endswith(".pending")
        for row in rows
        for reference in row["evidence_refs"]
    )
    assert ledger["policy"]["exclusion_is_pass"] is False


def test_portable_result_schema_requires_every_sanitized_packet_field(
    catalog, candidate_fields
) -> None:
    from .conftest import make_packet

    packet = make_packet(catalog, "v2:auth-01", "positive", 1, candidate_fields)
    schema = json.loads(
        (CATALOG_PATH.parent / "result-packet-v3.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(schema["required"]) == set(packet.to_dict())
    assert schema["properties"]["silent_fallback"] == {"const": False}
    assert schema["properties"]["invariant_violations"]["maxItems"] == 0


def test_every_catalog_entry_resolves_all_required_behavior_fields(catalog) -> None:
    for capability in catalog.capabilities:
        assert capability.source_ref
        assert capability.owner
        assert capability.state_before
        assert capability.state_after
        assert capability.expected_trace
        assert capability.primary_proof
        assert capability.secondary_proof
        assert capability.execution_id
        assert set(capability.paths) == {"positive", "denial", "recovery"}
        if capability.sdk_ledger_status == "not_runtime_applicable":
            assert capability.paths["positive"]["required"] is False
            assert capability.paths["denial"]["required"] is True
            assert capability.paths["recovery"]["required"] is True
        else:
            assert all(path["required"] is True for path in capability.paths.values())


def test_catalog_rejects_source_substitution_even_when_count_is_unchanged(tmp_path) -> None:
    document = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(document)
    row = next(item for item in mutated["capabilities"] if item["source_pack"] == "openclaw_active")
    row["source_item_id"] = "invented-replacement"
    path = tmp_path / "mutated.yaml"
    path.write_text(yaml.safe_dump(mutated, sort_keys=False), encoding="utf-8")
    with pytest.raises(CatalogViolation, match="pinned v3 manifest"):
        load_catalog(path)


def test_catalog_rejects_temporal_reason_in_rc_lane(tmp_path) -> None:
    document = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    document["capabilities"][0]["temporal_reason"] = "idle time"
    path = tmp_path / "temporal.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    with pytest.raises(CatalogViolation, match="runtime lane"):
        load_catalog(path)


def test_catalog_rejects_reference_drift(tmp_path) -> None:
    document = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    document["contract"]["references"]["openclaw"]["commit"] = "0" * 40
    path = tmp_path / "drift.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    with pytest.raises(CatalogViolation, match="pinned value"):
        load_catalog(path)
