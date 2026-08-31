from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).parents[3]
PACK_PATH = ROOT / "qa/parity/v3/source-packs/sdk-boundary.json"
LEDGER_PATH = ROOT / "qa/parity/v3/sdk-ledger.json"

PACK_SHA256 = "2f6a7652eb2f3a8e82cb14925b50190b7c9a34a0354b34c880187742ed7547b5"
SOURCE_FRAGMENT_SHA256 = "5ceeb81dfd51d947f5c669c61abdcc788687abed8225c5c9ba04ce1057c4fb6c"
PROVENANCE_SHA256 = "0cecf675e9c96cbc29d26794e235fe690b6e62cc8a91030ba009f3d60bd6638a"
SOURCE_COMMIT = "ea806575e6450e4d1efdfc72c19f04be982a1b9b9"
SOURCE_REF = (
    "src:openclaw@ea806575e6450e4d1efdfc72c19f04be982a1b9b9:"
    "extensions/anthropic/agent-sdk.runtime.test.ts"
)
LEDGER_CLASSIFICATIONS = {
    "covered_current": 11,
    "equivalent_host": 6,
    "requires_0_3_239": 2,
    "not_runtime_applicable": 4,
}
PATH_KEYS = {
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
STATE_KEYS = {
    "lifecycle",
    "approval",
    "tool",
    "resume",
    "billing",
    "side_effect_count",
    "boundary_sha256",
}
TRACE_REGISTRY = {
    "registration.accepted",
    "selection.accepted",
    "preflight.pass",
    "preflight.fail",
    "approval.requested",
    "approval.granted",
    "approval.denied",
    "approval.late_rejected",
    "tool.requested",
    "tool.executed",
    "tool.denied",
    "tool.recovered",
    "state.invalid",
    "resume.supplied",
    "resume.accepted",
    "resume.rejected",
    "session.restarted",
    "sdk.query",
    "sdk.result",
    "recovery.started",
    "recovery.completed",
    "recovery.failed",
    "terminal.complete",
    "terminal.failed",
}
SDK_EVENT_CODES = {
    "ClaudeSDKClient.query": "sdk.query",
    "ResultMessage": "sdk.result",
}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _expected_row_ids() -> list[str]:
    return [f"SDK-BOUNDARY-{index:02d}" for index in range(1, 24)]


def _validate_exact_set(pack: dict[str, Any], ledger: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate canonical row order/set before any hash or STOP calculation."""
    pack_keys = [(pack["pack_id"], row_id) for row_id in pack["row_ids"]]
    ledger_keys = [(row["pack_id"], row["row_id"]) for row in ledger["rows"]]
    if len(pack_keys) != 23 or len(set(pack_keys)) != 23:
        raise ValueError("source exact_set failure")
    if len(ledger_keys) != 23 or len(set(ledger_keys)) != 23:
        raise ValueError("ledger exact_set failure")
    if ledger_keys != sorted(ledger_keys):
        raise ValueError("ledger row order failure")
    if sorted(ledger_keys) != sorted(pack_keys):
        raise ValueError("ledger exact_set failure")
    if [row["ordinal"] for row in ledger["rows"]] != list(range(1, 24)):
        raise ValueError("ledger ordinal failure")
    return ledger["rows"]


def _validated_stop(pack: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    rows = _validate_exact_set(pack, ledger)
    expected_projection = [
        {
            key: row[key]
            for key in (
                "pack_id",
                "row_id",
                "ordinal",
                "executable",
                "classification",
                "proof",
            )
        }
        for row in rows
    ]
    expected_projection = sorted(expected_projection, key=lambda row: (row["pack_id"], row["row_id"]))
    if ledger["rows_sha256"] != _sha256(expected_projection):
        raise ValueError("rows hash failure")
    if ledger["ledger_sha256"] != _sha256({"schema_version": ledger["schema_version"], "rows": rows}):
        raise ValueError("ledger hash failure")
    stop_rows = [
        row["row_id"]
        for row in rows
        if row["executable"] and row["classification"] == "requires_0_3_239"
    ]
    return {
        "status": "STOP" if stop_rows else "CLEAR",
        "upgrade_issue_ref": "issue:16" if stop_rows else None,
        "stop_rows": stop_rows,
    }


def _walk(value: Any, *, key: str | None = None) -> list[str]:
    violations: list[str] = []
    forbidden_keys = {
        "prompt",
        "transcript",
        "session_id",
        "resume_token",
        "provider_payload",
        "auth",
        "secret",
        "exception",
        "environment",
        "filesystem_path",
        "tool_args",
        "tool_results",
        "headers",
        "cookies",
        "customer_identifier",
        "customer_data",
    }
    if key in forbidden_keys:
        violations.append(f"forbidden key: {key}")
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            violations.extend(_walk(child_value, key=child_key))
    elif isinstance(value, list):
        for child in value:
            violations.extend(_walk(child, key=key))
    elif isinstance(value, str):
        if value.startswith(("http://", "https://", "/")) or "\\" in value:
            violations.append("raw URL/path value")
    return violations


def _assert_path_shape(path: dict[str, Any], role: str, classification: str) -> None:
    assert set(path) == PATH_KEYS
    if classification == "not_runtime_applicable":
        assert path == {
            "required": False,
            "expected_outcome": "NOT_APPLICABLE",
            "trace_codes": [],
            "terminal": {"kind": "not_applicable", "count": 0},
            "tool_calls": [],
            "side_effect_count": 0,
            "sdk_events": [],
            "state_before": {},
            "state_after": {},
        }
        return
    assert path["required"] is True
    expected_outcome = "EXPECTED_NEGATIVE" if role == "denial" else "PASS"
    terminal_kind = "failed" if role == "denial" else "complete"
    assert path["expected_outcome"] == expected_outcome
    assert path["terminal"] == {"kind": terminal_kind, "count": 1}
    assert path["trace_codes"]
    assert set(path["trace_codes"]).issubset(TRACE_REGISTRY)
    assert path["trace_codes"][-1] == f"terminal.{terminal_kind}"
    assert path["tool_calls"] == []
    assert path["side_effect_count"] == 0
    for event in path["sdk_events"]:
        assert set(event) == {"event", "trace_code"}
        assert SDK_EVENT_CODES[event["event"]] == event["trace_code"]
    for state in (path["state_before"], path["state_after"]):
        assert set(state) == STATE_KEYS
        assert state["side_effect_count"] == 0
        assert state["boundary_sha256"] is None


def test_sdk_pack_has_exact_23_rows_and_pinned_source() -> None:
    pack = _load(PACK_PATH)
    assert pack["pack_id"] == "sdk_boundary"
    assert pack["sdk_distribution"] == "claude-agent-sdk"
    assert pack["sdk_version"] == "0.2.144"
    assert pack["expected_count"] == 23
    assert pack["row_ids"] == _expected_row_ids()
    assert len(pack["rows"]) == 23
    assert [row["ordinal"] for row in pack["rows"]] == list(range(1, 24))
    assert [row["row_id"] for row in pack["rows"]] == pack["row_ids"]
    assert len({row["row_id"] for row in pack["rows"]}) == 23
    assert pack["row_ids_sha256"] == "efdfed8b9b2b03d2b84c9e8d3b259d11a987bf48d99d0b62436da90a1a885817"
    assert pack["source"]["commit_sha"] == SOURCE_COMMIT
    assert pack["source"]["source_ref"] == SOURCE_REF
    assert pack["source"]["artifact_sha256"] == PACK_SHA256
    assert pack["source_fragment"]["sha256"] == SOURCE_FRAGMENT_SHA256
    assert pack["provenance"]["evidence_sha256"] == PROVENANCE_SHA256
    assert pack["pack_sha256"] == _sha256({key: value for key, value in pack.items() if key != "pack_sha256"})


def test_sdk_pack_classifications_and_executable_policy_are_source_backed() -> None:
    pack = _load(PACK_PATH)
    counts: dict[str, int] = {}
    for row in pack["rows"]:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
        assert row["required"] is True
        assert row["consumers"] == ["inventory", "run", "grade"]
        assert row["executable"] is (row["classification"] != "not_runtime_applicable")
        assert row["proof"] == {"ref": SOURCE_REF, "sha256": PACK_SHA256}
        if row["classification"] == "requires_0_3_239":
            assert row["upgrade_issue_ref"] == "issue:16"
        else:
            assert row["upgrade_issue_ref"] is None
    assert counts == LEDGER_CLASSIFICATIONS


def test_sdk_pack_contains_v4_declaration_only_expected_paths() -> None:
    pack = _load(PACK_PATH)
    assert pack["source_accounting_only"] is True
    assert pack["expected_path_status"] == "PENDING_SOURCE_BINDING"
    assert pack["observed_evidence_present"] is False
    for row in pack["rows"]:
        paths = row["expected_paths"]
        assert list(paths) == ["positive", "denial", "recovery"]
        for role, path in paths.items():
            _assert_path_shape(path, role, row["classification"])


def test_sdk_ledger_exact_set_hashes_and_independent_stop() -> None:
    pack = _load(PACK_PATH)
    ledger = _load(LEDGER_PATH)
    assert set(ledger) == {"schema_version", "rows", "rows_sha256", "ledger_sha256"}
    assert ledger["schema_version"] == 1
    result = _validated_stop(pack, ledger)
    assert result == {
        "status": "STOP",
        "upgrade_issue_ref": "issue:16",
        "stop_rows": ["SDK-BOUNDARY-01", "SDK-BOUNDARY-09"],
    }


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "extra", "reordered"])
def test_sdk_ledger_rejects_set_or_order_bypass_before_stop(mutation: str) -> None:
    pack = _load(PACK_PATH)
    ledger = _load(LEDGER_PATH)
    mutated = copy.deepcopy(ledger)
    if mutation == "duplicate":
        mutated["rows"][-1] = copy.deepcopy(mutated["rows"][0])
    elif mutation == "missing":
        mutated["rows"] = mutated["rows"][:-1]
    elif mutation == "extra":
        mutated["rows"].append(copy.deepcopy(mutated["rows"][0]))
    else:
        mutated["rows"][0], mutated["rows"][1] = mutated["rows"][1], mutated["rows"][0]
    with pytest.raises(ValueError, match="(exact_set|row order|ordinal)"):
        _validated_stop(pack, mutated)


def test_sdk_ledger_cannot_reclassify_or_disable_the_two_stop_rows() -> None:
    pack = _load(PACK_PATH)
    ledger = _load(LEDGER_PATH)
    for row_id in ("SDK-BOUNDARY-01", "SDK-BOUNDARY-09"):
        mutated = copy.deepcopy(ledger)
        row = next(item for item in mutated["rows"] if item["row_id"] == row_id)
        row["classification"] = "covered_current"
        row["executable"] = False
        with pytest.raises(ValueError, match="rows hash failure"):
            _validated_stop(pack, mutated)


def test_sdk_pack_and_ledger_have_no_raw_or_forbidden_content() -> None:
    for path in (PACK_PATH, LEDGER_PATH):
        payload = _load(path)
        assert _walk(payload) == []
        serialized = path.read_text(encoding="utf-8")
        assert '"path_status"' not in serialized
        assert "grade" not in payload
        assert '"prompt"' not in serialized
        assert '"transcript"' not in serialized
        assert "http://" not in serialized
        assert "https://" not in serialized
