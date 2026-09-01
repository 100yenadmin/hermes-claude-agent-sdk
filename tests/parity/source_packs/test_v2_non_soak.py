"""Offline invariants for the frozen-v2 non-soak source-pack input."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

PACK_PATH = Path(__file__).parents[3] / "qa/parity/v3/source-packs/v2-non-soak.json"
CANONICAL_PATH = (
    Path(__file__).parents[3]
    / "src/hermes_claude_agent_sdk/parity/canonical.py"
)
_CANONICAL_SPEC = importlib.util.spec_from_file_location("parity_canonical", CANONICAL_PATH)
assert _CANONICAL_SPEC and _CANONICAL_SPEC.loader
_CANONICAL = importlib.util.module_from_spec(_CANONICAL_SPEC)
_CANONICAL_SPEC.loader.exec_module(_CANONICAL)
SDK_EVENT_CODES = _CANONICAL.SDK_EVENT_CODES
TRACE_REGISTRY = _CANONICAL.TRACE_REGISTRY
CONTRACT_SHA256 = "e4842f3a78c855f18af59a8024c4360bde59143987d133724b81594d0f0bfe2e"
SOURCE_ARTIFACT_SHA256 = "c124ac8f7cdf6d9253efcac472769a6bd0efb1e2a0264c8ca2493d31f84e1b75"
SOURCE_MAP_SHA256 = "abfbf5c36836e4a2eca8205077aa2ecacf45c0379cfa1c32467c2bf253ef2ae8"
SORTED_ROW_IDS_SHA256 = "6933d6abe587ffafdf20d63485445851f2ab0c0b2c12ab2e21b146271e72f861"

EXPECTED_ROW_IDS = [
    *(f"AUTH-{index:02d}" for index in range(1, 12)),
    *(f"PARENT-{index:02d}" for index in range(1, 11)),
    *(f"TOOL-{index:02d}" for index in range(1, 7)),
    *(f"ORCH-{index:02d}" for index in range(1, 11)),
    *(f"BG-{index:02d}" for index in range(1, 5)),
    *(f"OPS-{index:02d}" for index in range(1, 10)),
    *(f"EFF-{index:02d}" for index in range(1, 4)),
]
UNIVERSAL_GAPS = {
    "GAP-V4-TRACE-VOCABULARY",
    "GAP-V4-PATH-SHAPE",
    "GAP-V4-PATH-STATE",
    "GAP-V4-PROOF-DIGEST",
    "GAP-V4-SESSION-SCOPE",
    "GAP-V4-FIXTURE",
    "GAP-V4-INVENTORY",
    "GAP-V4-SDK-CLASSIFICATION",
}
EXPECTED_SURFACES = {
    "selection",
    "approval",
    "tool",
    "denial",
    "recovery",
    "resume",
    "isolation",
    "compaction",
    "usage",
    "packaging",
    "inventory",
}
EXPECTED_STATE = {
    "lifecycle",
    "approval",
    "tool",
    "resume",
    "billing",
    "side_effect_count",
    "boundary_sha256",
}
FORBIDDEN_KEYS = {
    "prompt",
    "messages",
    "transcript",
    "content",
    "session_id",
    "external_session_id",
    "token",
    "password",
    "secret",
    "api_key",
    "auth",
    "cookie",
    "customer",
    "tenant",
    "user_id",
    "email",
    "phone",
    "address",
    "raw",
    "exception",
    "stdout",
    "stderr",
    "environment",
}


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _assert_state(state: dict[str, Any]) -> None:
    assert set(state) == EXPECTED_STATE
    assert state["lifecycle"] in {
        "fresh",
        "bound",
        "running",
        "completed",
        "failed",
        "cancelled",
        "closed",
    }
    assert state["approval"] in {
        "not_required",
        "pending",
        "granted",
        "denied",
        "late_rejected",
    }
    assert state["tool"] in {
        "none",
        "requested",
        "executed",
        "denied",
        "cancelled",
        "failed",
        "recovered",
    }
    assert state["resume"] in {"absent", "supplied", "accepted", "rejected"}
    assert state["billing"] in {"included", "blocked", "unknown", "not_applicable"}
    assert state["side_effect_count"] == 0
    assert state["boundary_sha256"] is None


def _assert_path(row_id: str, path_name: str, path: dict[str, Any]) -> None:
    assert set(path) == {
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
    assert path["required"] is True
    expected_outcome = "EXPECTED_NEGATIVE" if path_name == "denial" else "PASS"
    assert path["expected_outcome"] == expected_outcome
    terminal_kind = path["terminal"]["kind"]
    if path_name == "denial":
        assert terminal_kind in {"failed", "cancelled"}
    else:
        assert terminal_kind == "complete"
    assert path["terminal"]["count"] == 1
    assert path["side_effect_count"] == 0
    assert path["tool_calls"] == []
    assert path["trace_codes"][0] == f"path.{path_name}.begin"
    assert path["trace_codes"][-2] == f"terminal.{terminal_kind}"
    assert path["trace_codes"][-1] == f"path.{path_name}.end"
    assert all(code in TRACE_REGISTRY for code in path["trace_codes"])
    for event in path["sdk_events"]:
        assert set(event) == {"event", "trace_code"}
        assert event["event"] in SDK_EVENT_CODES
        assert event["trace_code"] == SDK_EVENT_CODES[event["event"]]
        assert event["trace_code"] in TRACE_REGISTRY
    _assert_state(path["state_before"])
    _assert_state(path["state_after"])
    assert (
        path["state_after"]["side_effect_count"]
        - path["state_before"]["side_effect_count"]
        == path["side_effect_count"]
    )


def _assert_sanitized(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            assert key not in FORBIDDEN_KEYS, key
            _assert_sanitized(child)
    elif isinstance(value, list):
        for child in value:
            _assert_sanitized(child)
    elif isinstance(value, str):
        assert "/Users/" not in value
        assert "/Volumes/" not in value
        assert ".env" not in value
        assert "Bearer " not in value
        assert "-----BEGIN" not in value
        assert re.search(r"\bsk-[A-Za-z0-9]{8,}\b", value) is None


def test_v2_non_soak_pack_is_exhaustive_closed_and_fail_closed() -> None:
    pack = json.loads(PACK_PATH.read_text(encoding="utf-8"))

    assert pack["contract_sha256"] == CONTRACT_SHA256
    assert pack["contract_hash_status"] == "VERIFIED"
    assert pack["status"] == "PENDING_SOURCE_PROOF"
    assert pack["execution_claim"] is False
    assert pack["source_pack"]["id"] == "v2_non_soak"
    assert pack["source_pack"]["expected_count"] == 53

    sorted_ids = sorted(EXPECTED_ROW_IDS)
    assert pack["source_pack"]["row_ids"] == sorted_ids
    assert pack["source_pack"]["row_ids_sha256"] == SORTED_ROW_IDS_SHA256
    assert _sha256_json(sorted_ids) == SORTED_ROW_IDS_SHA256
    assert pack["source_pack"]["source"] == {
        "kind": "immutable_artifact",
        "repo_id": "hermes-evidence",
        "commit_sha": None,
        "source_ref": "src:hermes-evidence:v2-catalog-rows.json",
        "artifact_sha256": SOURCE_ARTIFACT_SHA256,
    }
    assert pack["source_pack"]["provenance"] == {
        "origin_id": "hermes-v2-frozen-source",
        "license_id": "proprietary-approved",
        "attribution_ref": "evidence:hermes-claude-agent-sdk-plugin-20260831/source-snapshot/parity-contract-v2.json",
    }

    rows = pack["rows"]
    assert len(rows) == 53
    assert [row["source_row"]["id"] for row in rows] == EXPECTED_ROW_IDS
    assert len({row["source_row"]["id"] for row in rows}) == 53
    assert all(not row["source_row"]["id"].startswith("SOAK-") for row in rows)

    for row in rows:
        source_id = row["source_row"]["id"]
        assert row["id"] == f"CAP-V2-{source_id}"
        assert row["scenario_id"] == f"SCN-V2-{source_id}"
        assert row["source_rows"] == [{"pack_id": "v2_non_soak", "row_id": source_id}]
        assert row["lane"] == "catalog"
        assert row["surface"] in EXPECTED_SURFACES
        assert row["owner"] in {"plugin", "host", "exact_pair"}
        assert row["consumers"] == ["inventory", "run", "grade"]
        assert row["required"] is True
        assert row["owner_issue"] == "issue:11"
        assert row["admission_status"] == "PENDING"
        assert UNIVERSAL_GAPS <= set(row["mapping_gap_codes"])
        if source_id in {"OPS-02", "OPS-03", "OPS-04", "OPS-05", "OPS-06", "OPS-07"}:
            assert "GAP-V4-OWNER-DISPOSITION" in row["mapping_gap_codes"]
        if source_id in {"TOOL-02", "TOOL-03", "TOOL-04", "TOOL-05"}:
            assert "GAP-V4-TOOL-EVIDENCE" in row["mapping_gap_codes"]

        source_row = row["source_row"]
        assert set(source_row) == {"id", "mandatory", "requirement", "required_proof"}
        assert source_row["mandatory"] is True
        assert isinstance(source_row["requirement"], str) and source_row["requirement"]
        assert isinstance(source_row["required_proof"], list)
        assert source_row["required_proof"]

        _assert_path(source_id, "positive", row["positive_path"])
        _assert_path(source_id, "denial", row["denial_path"])
        _assert_path(source_id, "recovery", row["recovery_path"])
        assert set(row["state_before"]) == EXPECTED_STATE
        assert set(row["state_after"]) == EXPECTED_STATE
        assert row["state_before"] == row["positive_path"]["state_before"]
        assert row["state_after"] == row["positive_path"]["state_after"]
        assert row["expected_trace"] == row["positive_path"]["trace_codes"]
        assert set(row["expected_trace"]) <= set(TRACE_REGISTRY)

        assert set(row["primary_proof"]) == {"kind", "ref", "sha256"}
        assert row["primary_proof"] == {
            "kind": "deterministic",
            "ref": "evidence:v2-catalog-rows.json",
            "sha256": SOURCE_ARTIFACT_SHA256,
        }
        assert row["secondary_proof"] == [
            {
                "kind": "deterministic",
                "ref": "evidence:v2-nonsoak-source-map.md",
                "sha256": SOURCE_MAP_SHA256,
            }
        ]
        assert set(row["repeat_policy"]) == {"mode", "reason"}
        assert row["repeat_policy"]["mode"] in {"once", "consecutive_3"}
        assert row["repeat_policy"]["reason"] in {
            "stable",
            "consequential",
            "initial_failure",
            "unstable",
        }
        assert "session_scope" not in row
        assert "sdk_classification" not in row
        serialized = json.dumps(row, ensure_ascii=False)
        assert "path_status" not in serialized
        assert '"outcome"' not in serialized

    _assert_sanitized(pack)
