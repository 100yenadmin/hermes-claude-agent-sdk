from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from hermes_claude_agent_sdk.parity.canonical import CanonicalizationError, canonical_sha256, load_json
from hermes_claude_agent_sdk.parity.assembler import CatalogAssemblyError, assemble_catalog


ROOT = Path(__file__).parents[2]
OWNER_PATH = ROOT / "qa/parity/v3/owner-decisions.json"
LEDGER_PATH = ROOT / "qa/parity/v3/sdk-ledger.json"
PACK_PATHS = {
    "v2_non_soak": ROOT / "qa/parity/v3/source-packs/v2-non-soak.json",
    "openclaw_active": ROOT / "qa/parity/v3/source-packs/openclaw-active.json",
    "sdk_boundary": ROOT / "qa/parity/v3/source-packs/sdk-boundary.json",
}
SCOPE_VALUES = {"isolated_cell", "one_logical_session"}
EXPECTED_PACK_COUNTS = {"v2_non_soak": 53, "openclaw_active": 12, "sdk_boundary": 23}
EXPECTED_DECISION_KEYS = {
    "schema_version",
    "decision_id",
    "authority_class",
    "session_scope_assignments",
    "sdk_primary_proof_kinds",
    "issue_16_stop",
    "decisions_sha256",
}
FORBIDDEN_MARKERS = (
    "prompt",
    "transcript",
    "cookie",
    "authorization",
    "private_key",
    "customer",
    "token",
    "secret",
)


def _load_owner() -> dict[str, Any]:
    value = load_json(OWNER_PATH.read_bytes(), source=str(OWNER_PATH))
    assert isinstance(value, dict)
    return value


def _pack_row_ids(pack_id: str) -> set[tuple[str, str]]:
    value = load_json(PACK_PATHS[pack_id].read_bytes(), source=pack_id)
    assert isinstance(value, dict)
    metadata = value.get("source_pack", value)
    assert isinstance(metadata, dict)
    rows = metadata["row_ids"]
    assert isinstance(rows, list)
    return {(pack_id, row_id) for row_id in rows}


def test_owner_decisions_are_strict_duplicate_free_and_hash_bound() -> None:
    raw = OWNER_PATH.read_bytes()
    owner = _load_owner()
    assert set(owner) == EXPECTED_DECISION_KEYS
    assert owner["schema_version"] == 1
    assert owner["decision_id"] == "parity-v3-owner-decisions"
    assert owner["authority_class"] == "normative_owner_decision"
    assert owner["decisions_sha256"] == canonical_sha256(
        {key: value for key, value in owner.items() if key != "decisions_sha256"}
    )
    assert b"ea806575e6450e4d1efdfc72c19f04be982a1b9b" not in raw
    with pytest.raises(CanonicalizationError):
        load_json(b'{"duplicate": 1, "duplicate": 2}', source="duplicate-test")


def test_session_scope_keys_equal_current_v2_openclaw_sdk_rows() -> None:
    owner = _load_owner()
    assignments = owner["session_scope_assignments"]
    expected = set().union(*(_pack_row_ids(pack_id) for pack_id in PACK_PATHS))
    actual = {(item["pack_id"], item["row_id"]) for item in assignments}
    assert actual == expected
    assert len(actual) == 88
    assert len(actual) == len({(pack_id, row_id) for pack_id, row_id in actual})
    assert len(assignments) == 88
    assert assignments == sorted(assignments, key=lambda item: (item["pack_id"], item["row_id"]))
    assert all(set(item) == {"pack_id", "row_id", "session_scope"} for item in assignments)
    assert Counter(item["pack_id"] for item in assignments) == Counter(EXPECTED_PACK_COUNTS)
    assert all(item["session_scope"] in SCOPE_VALUES for item in assignments)
    assert Counter(item["session_scope"] for item in assignments) == Counter(
        isolated_cell=73,
        one_logical_session=15,
    )

    for pack_id, path in PACK_PATHS.items():
        source = load_json(path.read_bytes(), source=pack_id)
        assert isinstance(source, dict)
        for row in source["rows"]:
            assert "session_scope" not in row


def test_assembler_consumes_owner_decisions_but_stays_fail_closed() -> None:
    owner = _load_owner()
    fragments = [load_json(path.read_bytes(), source=pack_id) for pack_id, path in PACK_PATHS.items()]
    ledger = load_json(LEDGER_PATH.read_bytes(), source=str(LEDGER_PATH))
    with pytest.raises(CatalogAssemblyError) as raised:
        assemble_catalog(
            fragments,
            scope_assignments=owner["session_scope_assignments"],
            sdk_proof_kinds=owner["sdk_primary_proof_kinds"],
            sdk_ledger=ledger,
        )
    codes = {item.code for item in raised.value.diagnostics}
    assert not any(code.startswith("SCOPE_ASSIGNMENTS_") for code in codes)
    assert not any(code.startswith("SDK_PROOF_KINDS_") for code in codes)
    assert "UNRESOLVED_SOURCE_GAPS" in codes
    assert "CATALOG_INPUT_MISSING" in codes
    assert "PACK_MISSING" in codes
    assert "SOURCE_KEY_ACCOUNTING" in codes
    assert "SDK_PATH_DECISIONS_MISSING" in codes
    assert raised.value.report is not None
    assert raised.value.report.catalog is None
    assert raised.value.report.catalog_sha256 is None


def test_sdk_primary_proof_kinds_equal_all_23_rows_and_issue_16_is_clear() -> None:
    owner = _load_owner()
    proof_kinds = owner["sdk_primary_proof_kinds"]
    expected = {row_id for _, row_id in _pack_row_ids("sdk_boundary")}
    assert set(proof_kinds) == expected
    assert len(proof_kinds) == 23
    assert set(proof_kinds.values()) == {"ledger"}
    assert owner["issue_16_stop"] == {
        "status": "CLEAR",
        "issue_ref": None,
        "rows": [],
        "stop_rows": [],
    }


def test_owner_decisions_have_closed_safe_content_and_no_source_identity_binding() -> None:
    owner = _load_owner()
    assert len(owner["session_scope_assignments"]) == 88
    assert set(owner["issue_16_stop"]) == {"status", "issue_ref", "rows", "stop_rows"}
    serialized = json.dumps(owner, ensure_ascii=False, sort_keys=True).casefold()
    assert all(marker not in serialized for marker in FORBIDDEN_MARKERS)
    assert "pack_sha256" not in serialized
    assert "source_map_sha256" not in serialized
    assert "commit_sha" not in serialized
    assert "artifact_sha256" not in serialized
