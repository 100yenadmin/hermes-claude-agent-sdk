from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from hermes_claude_agent_sdk.parity.canonical import TRACE_REGISTRY, canonical_sha256


PACK_PATH = Path(__file__).resolve().parents[3] / "qa/parity/v3/source-packs/openclaw-active.json"
EXPECTED_ROWS = [f"OPENCLAW-ACTIVE-{index:02d}" for index in range(1, 13)]
EXPECTED_PATH_KEYS = {
    "required",
    "expected_outcome",
    "trace_codes",
    "terminal",
    "tool_calls",
    "side_effect_count",
    "sdk_events",
    "state_before",
    "state_after",
}
EXPECTED_STATE_KEYS = {
    "lifecycle",
    "approval",
    "tool",
    "resume",
    "billing",
    "side_effect_count",
    "boundary_sha256",
}
LIFECYCLE = {"fresh", "bound", "running", "completed", "failed", "cancelled", "closed"}
APPROVAL = {"not_required", "pending", "granted", "denied", "late_rejected"}
TOOL = {"none", "requested", "executed", "denied", "cancelled", "failed", "recovered"}
RESUME = {"absent", "supplied", "accepted", "rejected"}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AssertionError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


@pytest.fixture(scope="module")
def pack() -> dict[str, Any]:
    return json.loads(PACK_PATH.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)


def _assert_state(value: Any) -> None:
    assert set(value) == EXPECTED_STATE_KEYS
    assert value["lifecycle"] in LIFECYCLE
    assert value["approval"] in APPROVAL
    assert value["tool"] in TOOL
    assert value["resume"] in RESUME
    assert value["billing"] == "not_applicable"
    assert type(value["side_effect_count"]) is int and value["side_effect_count"] >= 0
    assert value["boundary_sha256"] is None or re.fullmatch(r"[0-9a-f]{64}", value["boundary_sha256"])


def _assert_path(path: dict[str, Any], expected_outcome: str, terminal_kind: str) -> None:
    assert set(path) == EXPECTED_PATH_KEYS
    assert path["required"] is True
    assert path["expected_outcome"] == expected_outcome
    assert path["trace_codes"]
    assert all(code in TRACE_REGISTRY for code in path["trace_codes"])
    terminal_codes = [code for code in path["trace_codes"] if code.startswith("terminal.")]
    assert len(terminal_codes) == 1
    assert terminal_codes[0] == f"terminal.{terminal_kind}"
    assert path["terminal"] == {"kind": terminal_kind, "count": 1}
    assert path["tool_calls"] == []
    assert type(path["side_effect_count"]) is int and path["side_effect_count"] >= 0
    assert path["sdk_events"] == []
    _assert_state(path["state_before"])
    _assert_state(path["state_after"])
    assert path["state_after"]["side_effect_count"] - path["state_before"]["side_effect_count"] == path["side_effect_count"]


def test_openclaw_pack_has_exact_pinned_source_accounting(pack: dict[str, Any]) -> None:
    assert pack["schema_version"] == "4.0.0"
    assert pack["pack_schema_version"] == "4.0.0"
    assert pack["fragment_kind"] == "openclaw_active_source_pack"
    assert pack["pack_id"] == "openclaw_active"
    assert pack["status"] == "PENDING"

    source_pack = pack["source_pack"]
    assert source_pack["id"] == "openclaw_active"
    assert source_pack["expected_count"] == 12
    assert source_pack["row_ids"] == EXPECTED_ROWS
    assert len(source_pack["row_ids"]) == len(set(source_pack["row_ids"])) == 12
    assert canonical_sha256(source_pack["row_ids"]) == source_pack["row_ids_sha256"]
    assert source_pack["source"] == {
        "kind": "git_commit",
        "repo_id": "openclaw",
        "commit_sha": "ea806575e6450e4d1efdfc72c19f04be982a1b9b9",
        "source_ref": "src:openclaw@ea806575e6450e4d1efdfc72c19f04be982a1b9b9:extensions/qa-lab/src/agentic-parity.ts",
        "artifact_sha256": "853619648d445c52be584e58f732db80d425606ee3956305e2163a82d020c136",
    }
    assert source_pack["provenance"] == {
        "origin_id": "openclaw-v2026-8-1",
        "license_id": "MIT",
        "attribution_ref": "src:openclaw@ea806575e6450e4d1efdfc72c19f04be982a1b9b9:LICENSE",
    }


def test_openclaw_rows_are_bijective_and_v4_closed(pack: dict[str, Any]) -> None:
    rows = pack["rows"]
    assert len(rows) == 12
    assert [row["source_rows"][0]["row_id"] for row in rows] == EXPECTED_ROWS
    assert len({row["id"] for row in rows}) == 12
    assert len({row["scenario_id"] for row in rows}) == 12
    for row in rows:
        assert row["source_rows"] == [{"pack_id": "openclaw_active", "row_id": row["source_rows"][0]["row_id"]}]
        assert row["lane"] == "openclaw"
        assert row["owner"] == row["owner_issue"]["owner"] == "exact_pair"
        assert row["owner_issue"]["issue_ref"] is None
        assert row["owner_issue"]["status"] == "PENDING"
        assert row["consumers"] == ["inventory", "run", "grade"]
        assert row["sdk_classification"] == "not_runtime_applicable"
        assert row["required"] is True
        assert row["terminal_role"] == "plugin"
        assert row["admission_status"] == "PENDING"
        assert row["admission_gaps"] == row["mapping_gap_codes"]
        assert row["admission_gaps"]
        assert row["expected_trace"] == row["positive_path"]["trace_codes"]
        _assert_state(row["state_before"])
        _assert_state(row["state_after"])
        _assert_path(row["positive_path"], "PASS", "complete")
        _assert_path(row["denial_path"], "EXPECTED_NEGATIVE", "failed")
        _assert_path(row["recovery_path"], "PASS", "complete")


def test_openclaw_pack_keeps_all_runtime_gates_fail_closed(pack: dict[str, Any]) -> None:
    required_codes = {
        "GAP-TRACE-MAPPING",
        "GAP-TOOL-INVENTORY",
        "GAP-SDK-CORRELATION",
        "GAP-STATE-EVIDENCE",
        "GAP-SCOPE-METADATA",
        "GAP-RUNTIME-PROOF",
        "GAP-FIXTURE-MANIFEST",
        "GAP-REQUEST-CORRELATION",
    }
    registry = {entry["code"]: entry for entry in pack["gap_registry"]}
    assert set(registry) == required_codes | {"GAP-SOURCE-ROLE-CONFLICT"}
    assert all(entry["status"] == "PENDING" and entry["blocking"] is True for entry in registry.values())
    assert all(required_codes <= set(row["admission_gaps"]) for row in pack["rows"])
    assert len([row for row in pack["rows"] if "GAP-SOURCE-ROLE-CONFLICT" in row["admission_gaps"]]) == 2
    assert "path_status" not in json.dumps(pack, sort_keys=True)

    forbidden = ("/Users/", "https://", "prompt", "transcript", "customer", "provider_payload", "tool_args", "tool_results")
    encoded = json.dumps(pack, sort_keys=True)
    assert not any(token in encoded for token in forbidden)


def test_openclaw_proof_refs_are_safe_and_digest_bound(pack: dict[str, Any]) -> None:
    for row in pack["rows"]:
        for proof in [row["primary_proof"], *row["secondary_proof"]]:
            assert set(proof) == {"kind", "ref", "sha256"}
            assert proof["kind"] in {"focused_test", "deterministic", "integration", "live"}
            assert proof["ref"].startswith("src:")
            assert re.fullmatch(r"[0-9a-f]{64}", proof["sha256"])
