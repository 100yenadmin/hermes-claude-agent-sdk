from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.assembler import (
    CatalogAssemblyError,
    assemble_catalog,
    inspect_source_fragments,
)


ROOT = Path(__file__).parents[2]
PACKS = ROOT / "qa/parity/v3/source-packs"
LEDGER = ROOT / "qa/parity/v3/sdk-ledger.json"


def _inputs() -> tuple[list[dict], dict]:
    paths = [
        PACKS / "v2-non-soak.json",
        PACKS / "openclaw-active.json",
        PACKS / "sdk-boundary.json",
        PACKS / "clawprobench-native.json",
    ]
    return [json.loads(path.read_text(encoding="utf-8")) for path in paths], json.loads(LEDGER.read_text(encoding="utf-8"))


def _provenance_record(packs: list[dict], *, key: str = "packs") -> dict:
    records = []
    counts = {}
    for pack in packs:
        metadata = pack.get("source_pack", pack)
        pack_id = pack.get("pack_id") or metadata["id"]
        counts[pack_id] = metadata["expected_count"]
        records.append({
            "id": pack_id,
            "source": copy.deepcopy(metadata["source"]),
            "provenance": copy.deepcopy(metadata["provenance"]),
        })
    record = {"schema_version": 1, key: records}
    if key == "packs":
        record["scope"] = {"pack_counts": counts}
    return record


def test_inspection_accounts_all_rows_and_preserves_current_gates() -> None:
    packs, ledger = _inputs()
    report = inspect_source_fragments(packs, ledger)

    assert report.pack_counts == {
        "v2_non_soak": 53,
        "openclaw_active": 12,
        "sdk_boundary": 23,
        "clawprobench_native": 36,
    }
    assert len(report.rows) == len(report.source_keys) == 124
    assert len(set(report.source_keys)) == 124
    assert len(report.missing_session_scope) == 88
    assert len(report.missing_sdk_proof_kind) == 23
    assert [row[1] for row in report.sdk_rows_without_required_path] == [
        "SDK-BOUNDARY-02", "SDK-BOUNDARY-03", "SDK-BOUNDARY-06", "SDK-BOUNDARY-19",
    ]
    assert report.issue_16 == {
        "status": "CLEAR",
        "issue_ref": None,
        "upgrade_issue_ref": None,
        "rows": [],
        "stop_rows": [],
    }
    assert report.catalog is None
    assert report.catalog_sha256 is None
    assert any(gap["code"] == "GAP-V4-INVENTORY" for gap in report.residual_gaps)
    assert any(gap["code"] == "GAP-CLOSED-TRACE" for gap in report.residual_gaps)


def test_inspection_is_order_independent_and_mapping_compatible() -> None:
    packs, ledger = _inputs()
    reversed_report = inspect_source_fragments(list(reversed(packs),), ledger)
    keyed = {pack.get("pack_id") or pack["source_pack"]["id"]: pack for pack in packs}
    keyed_report = inspect_source_fragments(keyed, ledger)

    assert reversed_report.to_dict() == keyed_report.to_dict()
    assert reversed_report["missing_session_scope_count"] == 88
    assert reversed_report["catalog_sha256"] is None


def test_source_gaps_and_provenance_reconciliation_are_not_synthesized() -> None:
    packs, ledger = _inputs()
    provenance = _provenance_record(packs, key="source_packs")
    provenance["source_packs"][0]["provenance"]["origin_id"] = "contradictory-origin"
    report = inspect_source_fragments(packs, ledger, provenance=provenance)

    assert report.provenance["status"] == "CONTRADICTORY"
    assert {item.code for item in report.diagnostics} == {"PROVENANCE_CONTRADICTION"}
    assert report["gap_counts"]["GAP-V4-SDK-PROOF-KIND"] == 23
    assert "SDK-STOP-ISSUE-16" not in report["gap_counts"]


def test_source_packs_and_legacy_packs_provenance_shapes_are_compatible() -> None:
    packs, ledger = _inputs()
    dual = _provenance_record(packs, key="source_packs")
    dual["packs"] = copy.deepcopy(dual["source_packs"])
    for provenance in (_provenance_record(packs, key="source_packs"), _provenance_record(packs), dual):
        report = inspect_source_fragments(packs, ledger, provenance=provenance)
        assert report.provenance["status"] == "PASS"


def test_contradictory_dual_provenance_keys_fail_closed() -> None:
    packs, ledger = _inputs()
    provenance = _provenance_record(packs, key="source_packs")
    provenance["packs"] = copy.deepcopy(_provenance_record(packs)["packs"])
    provenance["packs"][0]["provenance"]["origin_id"] = "contradictory-origin"
    report = inspect_source_fragments(packs, ledger, provenance=provenance)
    assert report.provenance["status"] == "CONTRADICTORY"
    assert {item.code for item in report.diagnostics} == {"PROVENANCE_CONTRADICTION"}


def test_duplicate_source_key_is_reported_and_guard_rejects() -> None:
    packs, ledger = _inputs()
    mutated = copy.deepcopy(packs)
    duplicate = copy.deepcopy(mutated[0]["rows"][0])
    mutated[0]["rows"].append(duplicate)
    report = inspect_source_fragments(mutated, ledger)
    assert any(item.code == "DUPLICATE_SOURCE_KEY" for item in report.diagnostics)
    with pytest.raises(CatalogAssemblyError) as raised:
        assemble_catalog(mutated, sdk_ledger=ledger)
    assert "DUPLICATE_SOURCE_KEY" in str(raised.value)
    assert raised.value.report is not None
    assert raised.value.report.catalog is None


def test_guard_requires_every_strict_input_and_never_hashes_incomplete_source() -> None:
    packs, ledger = _inputs()
    with pytest.raises(CatalogAssemblyError) as raised:
        assemble_catalog(packs, sdk_ledger=ledger)
    codes = {item.code for item in raised.value.diagnostics}
    assert {
        "CATALOG_INPUT_MISSING", "PREDECESSOR_INPUT_MISSING", "SOURCE_METADATA_MISSING",
        "PROVENANCE_INPUT_MISSING", "SCOPE_PARTITIONS_MISSING", "INVENTORY_INPUT_MISSING",
        "SDK_PROOF_KINDS_MISSING", "SDK_PATH_DECISIONS_MISSING", "SCOPE_ASSIGNMENTS_MISSING",
        "UNRESOLVED_SOURCE_GAPS",
    } <= codes
    assert raised.value.report is not None
    assert raised.value.report.catalog is None
    assert raised.value.report.catalog_sha256 is None
