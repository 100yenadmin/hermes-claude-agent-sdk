"""Immutable, SDK-free validation for the Hermes parity v4 contract.

The v4 catalog is intentionally a small, closed accounting document.  Its
source rows are compact tuples in YAML; this module expands those tuples using
the byte-frozen v3 catalog and then checks every identity and path.  No SDK,
provider, host, or network operation is performed here.
"""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .hashing import sha256_file, sha256_value

V4_VERSION = "4.0.0"
V4_SCHEMA_VERSION = 4
V4_SDK_DISTRIBUTION = "claude-agent-sdk"
V4_SDK_VERSION = "0.2.151"
V4_CLI_VERSION = "2.1.258"
V4_MODEL = "claude-fable-5-1"
V4_RUNNER_ID = "hermes-parity-v4"
V4_RUNNER_VERSION = V4_VERSION
V4_TURN_BUDGET = 180
V4_RUNTIME_TURNS = 100
REQUIRED_TRIAL_PACKETS = 390
CONSEQUENTIAL_ROW_COUNT = 55
PROVIDER_LIVE_ROW_COUNT = 70
V3_RESULT_CONTRACT_HASH = "aaddc44c53b5648202e34c5682a5c0ee599fa52b896c0530d0945cac95eb3244"
V3_RESULT_CATALOG_HASH = "768c2d8f99077f8557a192d1053fc80401e83dee80d77475d12119df75b63abb"
PROVIDER_LIVE_SOURCE_IDS = frozenset(
    {
        "source-docs-discovery-report",
        "image-understanding-attachment",
        "subagent-handoff",
        "subagent-fanout-synthesis",
        "memory-recall",
        "thread-memory-isolation",
        "config-restart-capability-flip",
        "instruction-followthrough-repo-contract",
    }
)
PACK_COUNTS = {
    "v2_non_soak": (53, 53),
    "openclaw_active": (12, 36),
    "agent_sdk_boundary": (23, 23),
    "clawprobench_native": (36, 108),
}
DISPOSITIONS = ("carry", "replace", "retire-with-successor", "split")
PATH_NAMES = ("positive", "denial", "recovery")
PATH_CODES = {"p": "positive", "d": "denial", "r": "recovery"}
OWNERSHIP_PREFLIGHTS = (
    "zero_native_absence",
    "exact_prompt_settings_tools_mcp",
    "no_native_events_projector",
    "delegate_owner",
    "background_owner",
    "canonical_transcript_content",
    "streaming_owner",
    "redaction_fail_closed",
)
V3_HASHES = {
    "contract_sha256": "e601f41313deb68b77a01402fe3b79c5da90afc7c46e40f87a6bac1850b69d8a",
    "boundary_ledger_sha256": "22e738bebca804514cfd8311d0ff1bf1bc9da6e6a8d21cce5fb9f6aa31f1463b",
    "result_schema_sha256": "dde70d2fbaa5e1cc669ff6167f89f043cc6854cf740ddff8e40c3dcb68ee1295",
}
V3_SOURCE_COMMIT = "228cd52f21ed28b4e314a1d0b2c0225229b859fe"
V3_BASELINE_COMMIT = "ea806575e6450e4d1efdfc72c19f04be982a1b9b"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SAFE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


class V4ContractViolation(ValueError):
    """A v4 artifact is malformed, incomplete, or unsafe to grade."""


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise V4ContractViolation(f"{field} must be a mapping")
    return dict(value)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise V4ContractViolation(f"{field} must be a non-empty string")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise V4ContractViolation(f"{field} contains a control character")
    lowered = value.casefold()
    if any(marker in lowered for marker in ("raw_prompt", "raw_content", "session_id")):
        raise V4ContractViolation(f"{field} contains forbidden raw-data marker")
    return value


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise V4ContractViolation(f"{field} must be a lowercase SHA-256 digest")
    return value


def _sha1(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA1.fullmatch(value) is None:
        raise V4ContractViolation(f"{field} must be a full lowercase Git SHA")
    return value


def _load_yaml(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file() or source.stat().st_size > 8 * 1024 * 1024:
        raise V4ContractViolation(f"artifact is not a bounded regular file: {source}")
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise V4ContractViolation(f"cannot parse artifact: {source}") from exc
    return _mapping(document, str(source))


def _repo_root(path: Path) -> Path:
    # qa artifacts are siblings; this also works when a caller passes a
    # copied artifact from its repository checkout.
    for candidate in (path.parent, *path.parents):
        if (candidate / "qa" / "parity-contract-v3.yaml").is_file():
            return candidate
    return Path(__file__).resolve().parents[3]


def _v3_rows(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    v3_path = _repo_root(path) / "qa" / "parity-contract-v3.yaml"
    try:
        document = yaml.safe_load(v3_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise V4ContractViolation("immutable v3 contract cannot be read") from exc
    rows = document.get("capabilities") if isinstance(document, Mapping) else None
    if not isinstance(rows, list):
        raise V4ContractViolation("immutable v3 contract has no capabilities")
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise V4ContractViolation("immutable v3 capability row is malformed")
        key = (row.get("source_pack"), row.get("source_item_id"))
        if key in result or not all(isinstance(item, str) for item in key):
            raise V4ContractViolation("immutable v3 source identity is malformed or duplicated")
        result[key] = dict(row)
    return result


def _ownership(source_pack: str, source_item_id: str) -> tuple[str, tuple[str, ...]]:
    delegated = (
        source_item_id.startswith(("ORCH-", "subagent-"))
        or source_item_id in {"planning_19_agent_delegation_boundary_live", "planning_20_session_agent_handoff_live"}
    )
    background = (
        source_item_id.startswith("BG-")
        or source_item_id in {"background-provisional-result-settlement", "subagent-stale-child-links"}
    )
    if delegated:
        if source_item_id == "subagent-stale-child-links":
            return "delegate_task", ("zero_native", "delegate_task", "host_background", "explicit_parent", "canonical_transcript", "streaming")
        return "delegate_task", ("zero_native", "delegate_task", "explicit_parent", "canonical_transcript", "streaming")
    if background:
        return "host_background", ("zero_native", "host_background", "explicit_parent", "canonical_transcript", "streaming")
    if source_item_id == "EFF-01":
        return "zero_native", ("zero_native", "explicit_parent", "canonical_transcript", "streaming")
    return "hermes_parent", ("zero_native", "explicit_parent", "canonical_transcript", "streaming")


def required_trial_indexes(row: Mapping[str, Any]) -> tuple[int, ...]:
    policy = _mapping(row.get("repeat_policy"), "source row repeat_policy")
    target = policy.get("consecutive_passes")
    if type(target) is not int or target < 1:
        raise V4ContractViolation("source row repeat policy is malformed")
    if set(policy.get("triggers", ())) & {"consequential", "unstable"}:
        target = max(target, 3)
    return tuple(range(1, target + 1))


def _provider_live_required(source_pack: str, source_item_id: str, predecessor: Mapping[str, Any]) -> bool:
    proofs = tuple(predecessor["primary_proof"]) + tuple(predecessor["secondary_proof"])
    return (
        source_pack in {"clawprobench_native", "runtime_active"}
        or (source_pack == "openclaw_active" and source_item_id in PROVIDER_LIVE_SOURCE_IDS)
        or "live" in proofs
    )


def _expand_row(raw: Any, v3: Mapping[tuple[str, str], Mapping[str, Any]], index: int) -> dict[str, Any]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)) or len(raw) != 4:
        raise V4ContractViolation(f"source_rows[{index}] must be [pack, item, disposition, path-code]")
    source_pack, source_item_id, disposition, path_code = (_text(item, f"source_rows[{index}]") for item in raw)
    if source_pack not in PACK_COUNTS or disposition not in DISPOSITIONS or not path_code:
        raise V4ContractViolation(f"source_rows[{index}] has unsupported accounting values")
    if any(code not in PATH_CODES for code in path_code) or len(set(path_code)) != len(path_code):
        raise V4ContractViolation(f"source_rows[{index}] has duplicate or unknown paths")
    predecessor = v3.get((source_pack, source_item_id))
    if predecessor is None:
        raise V4ContractViolation(f"source_rows[{index}] has no v3 predecessor")
    expected_paths = tuple(name for code, name in PATH_CODES.items() if predecessor[f"{name}_path"]["required"])
    paths = tuple(PATH_CODES[code] for code in path_code)
    if paths != expected_paths:
        raise V4ContractViolation(f"source_rows[{index}] changes predecessor path strength")
    mode, atoms = _ownership(source_pack, source_item_id)
    return {
        "source_pack": source_pack,
        "source_item_id": source_item_id,
        "predecessor_capability_id": predecessor["capability_id"],
        "predecessor_execution_id": predecessor["execution_id"],
        "predecessor_source_ref": predecessor["source_ref"],
        "disposition": disposition,
        "successor_id": f"hermes-v4/{source_pack}/{source_item_id}",
        "mandatory_paths": list(paths),
        "ownership_mode": mode,
        "native_surface": False,
        "proof_atoms": list(atoms),
        "expected_trace": copy.deepcopy(predecessor["expected_trace"]),
        "repeat_policy": copy.deepcopy(predecessor["repeat_policy"]),
        "primary_proof": copy.deepcopy(predecessor["primary_proof"]),
        "secondary_proof": copy.deepcopy(predecessor["secondary_proof"]),
        "provider_live_required": _provider_live_required(source_pack, source_item_id, predecessor),
    }


def _normalize_contract(document: Mapping[str, Any], path: Path) -> dict[str, Any]:
    if set(document) != {"schema_version", "contract", "source_rows", "runtime_soak"}:
        raise V4ContractViolation("v4 contract has an unexpected root field")
    if document["schema_version"] != V4_SCHEMA_VERSION:
        raise V4ContractViolation("v4 contract schema_version must equal 4")
    contract = _mapping(document["contract"], "contract")
    required = {"name", "version", "runner_version", "predecessor", "target", "candidate_inputs", "required_coverage", "mandatory_path_count", "required_trial_packets", "turn_budget", "ownership_preflights"}
    if set(contract) != required:
        raise V4ContractViolation("v4 contract metadata fields are not closed")
    if contract["name"] != "Hermes-owned Claude Subscription Runtime Parity" or contract["version"] != V4_VERSION or contract["runner_version"] != V4_RUNNER_VERSION:
        raise V4ContractViolation("v4 contract identity is unsupported")
    predecessor = _mapping(contract["predecessor"], "contract.predecessor")
    expected_predecessor = {"source_commit", "contract_sha256", "boundary_ledger_sha256", "result_schema_sha256"}
    if set(predecessor) != expected_predecessor or predecessor["source_commit"] != V3_SOURCE_COMMIT:
        raise V4ContractViolation("v4 predecessor identity is incomplete")
    for key in ("contract_sha256", "boundary_ledger_sha256", "result_schema_sha256"):
        if predecessor[key] != V3_HASHES[key]:
            raise V4ContractViolation(f"v4 predecessor {key} is not the frozen v3 hash")
    target = _mapping(contract["target"], "contract.target")
    if target != {"sdk_distribution": V4_SDK_DISTRIBUTION, "sdk_version": V4_SDK_VERSION, "cli_version": V4_CLI_VERSION, "model": V4_MODEL}:
        raise V4ContractViolation("v4 target SDK, CLI, or model identity is wrong")
    candidate_inputs = _mapping(contract["candidate_inputs"], "contract.candidate_inputs")
    if set(candidate_inputs) != {"plugin_sha", "host_sha", "wheel_sha256", "profile_sha256", "runner_id", "runner_version"}:
        raise V4ContractViolation("v4 candidate freeze inputs are incomplete")
    if any(candidate_inputs[key] is not None for key in ("plugin_sha", "host_sha", "wheel_sha256", "profile_sha256")) or candidate_inputs["runner_id"] != V4_RUNNER_ID or candidate_inputs["runner_version"] != V4_RUNNER_VERSION:
        raise V4ContractViolation("v4 candidate freeze inputs must remain explicit and unresolved")
    coverage = _mapping(contract["required_coverage"], "contract.required_coverage")
    if coverage != {pack: values[0] for pack, values in PACK_COUNTS.items()}:
        raise V4ContractViolation("v4 required coverage is not the frozen 124-row set")
    if contract["mandatory_path_count"] != 220 or contract["required_trial_packets"] != REQUIRED_TRIAL_PACKETS or contract["turn_budget"] != V4_TURN_BUDGET:
        raise V4ContractViolation("v4 turn or mandatory-path budget changed")
    preflights = contract["ownership_preflights"]
    if preflights != list(OWNERSHIP_PREFLIGHTS):
        raise V4ContractViolation("ownership preflights must be the closed ordered set")
    raw_rows = document["source_rows"]
    if not isinstance(raw_rows, list):
        raise V4ContractViolation("source_rows must be a list")
    v3 = _v3_rows(path)
    rows = [_expand_row(item, v3, index) for index, item in enumerate(raw_rows)]
    runtime = copy.deepcopy(document["runtime_soak"])
    runtime_predecessor = v3.get(("runtime_active", "soak-100-turn"))
    if runtime_predecessor is None:
        raise V4ContractViolation("immutable v3 runtime predecessor is missing")
    runtime["expected_trace"] = copy.deepcopy(runtime_predecessor["expected_trace"])
    runtime["repeat_policy"] = copy.deepcopy(runtime_predecessor["repeat_policy"])
    runtime["primary_proof"] = copy.deepcopy(runtime_predecessor["primary_proof"])
    runtime["secondary_proof"] = copy.deepcopy(runtime_predecessor["secondary_proof"])
    runtime["provider_live_required"] = _provider_live_required("runtime_active", "soak-100-turn", runtime_predecessor)
    return {"schema_version": V4_SCHEMA_VERSION, "contract": copy.deepcopy(contract), "source_rows": rows, "runtime_soak": runtime, "_path": str(path)}


def load_v4_contract(path: str | Path) -> dict[str, Any]:
    """Load, expand, and validate the v4 contract artifact."""

    source = Path(path).expanduser().resolve()
    normalized = _normalize_contract(_load_yaml(source), source)
    validate_v4_contract(normalized)
    return normalized


def _validate_runtime(document: Mapping[str, Any], v3: Mapping[tuple[str, str], Mapping[str, Any]]) -> None:
    runtime = _mapping(document.get("runtime_soak"), "runtime_soak")
    if set(runtime) != {"source_item_id", "source_pack", "predecessor_execution_id", "successor_id", "mandatory_paths", "turns", "expected_trace", "repeat_policy", "primary_proof", "secondary_proof", "provider_live_required"}:
        raise V4ContractViolation("runtime soak fields are not closed")
    predecessor = v3.get(("runtime_active", "soak-100-turn"))
    expected = {
        "source_item_id": "soak-100-turn",
        "source_pack": "runtime_active",
        "predecessor_execution_id": "runtime-active-100-turn",
        "successor_id": "hermes-v4/runtime_active/soak-100-turn",
        "mandatory_paths": ["positive", "denial", "recovery"],
        "turns": 100,
        "expected_trace": predecessor["expected_trace"] if predecessor else None,
        "repeat_policy": predecessor["repeat_policy"] if predecessor else None,
        "primary_proof": predecessor["primary_proof"] if predecessor else None,
        "secondary_proof": predecessor["secondary_proof"] if predecessor else None,
        "provider_live_required": _provider_live_required("runtime_active", "soak-100-turn", predecessor) if predecessor else None,
    }
    if runtime != expected:
        raise V4ContractViolation("runtime soak must remain the separate 100-turn, 3-path campaign")


def validate_v4_contract(value: Mapping[str, Any], *, ledger: Mapping[str, Any] | None = None, predecessor_map: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate one expanded contract and return deterministic accounting proof."""

    document = _mapping(value, "v4 contract")
    raw_rows = document.get("source_rows")
    if isinstance(raw_rows, list) and (not raw_rows or not isinstance(raw_rows[0], Mapping)):
        document = _normalize_contract(document, Path(document.get("_path", __file__)))
    if set(document) - {"schema_version", "contract", "source_rows", "runtime_soak", "_path"}:
        raise V4ContractViolation("v4 contract contains unknown fields")
    rows = document.get("source_rows")
    if not isinstance(rows, list) or len(rows) != 124:
        raise V4ContractViolation("v4 source row cardinality must equal 124")
    seen: set[tuple[str, str]] = set()
    successors: set[str] = set()
    v3 = _v3_rows(Path(document.get("_path", __file__)))
    for index, row in enumerate(rows):
        item = _mapping(row, f"source_rows[{index}]")
        required_fields = {"source_pack", "source_item_id", "predecessor_capability_id", "predecessor_execution_id", "predecessor_source_ref", "disposition", "successor_id", "mandatory_paths", "ownership_mode", "native_surface", "proof_atoms", "expected_trace", "repeat_policy", "primary_proof", "secondary_proof", "provider_live_required"}
        if set(item) != required_fields:
            raise V4ContractViolation(f"source_rows[{index}] fields are not closed")
        key = (item["source_pack"], item["source_item_id"])
        if key in seen or key not in v3:
            raise V4ContractViolation("source row identity is missing or duplicated")
        seen.add(key)
        predecessor = v3[key]
        if item["predecessor_execution_id"] != predecessor["execution_id"] or item["predecessor_capability_id"] != predecessor["capability_id"] or item["predecessor_source_ref"] != predecessor["source_ref"]:
            raise V4ContractViolation("source row predecessor link does not match immutable v3")
        if item["expected_trace"] != predecessor["expected_trace"] or item["repeat_policy"] != predecessor["repeat_policy"]:
            raise V4ContractViolation("source row trace or repeat policy does not match immutable v3")
        if item["primary_proof"] != predecessor["primary_proof"] or item["secondary_proof"] != predecessor["secondary_proof"]:
            raise V4ContractViolation("source row proof requirements do not match immutable v3")
        if item["provider_live_required"] is not _provider_live_required(*key, predecessor):
            raise V4ContractViolation("source row execution mode does not match the frozen v3 map")
        successor = f"hermes-v4/{key[0]}/{key[1]}"
        if item["successor_id"] != successor or successor in successors:
            raise V4ContractViolation("source row successor identity is missing or duplicated")
        successors.add(successor)
        expected_paths = [name for name in PATH_NAMES if predecessor[f"{name}_path"]["required"]]
        if item["mandatory_paths"] != expected_paths:
            raise V4ContractViolation("source row mandatory paths do not preserve v3 strength")
        if item["disposition"] not in DISPOSITIONS or item["native_surface"] is not False:
            raise V4ContractViolation("source row disposition or native ownership is unsafe")
        mode, atoms = _ownership(*key)
        if item["ownership_mode"] != mode or item["proof_atoms"] != list(atoms):
            raise V4ContractViolation("source row Hermes ownership proof atoms are incomplete")
        if not {"zero_native", "explicit_parent", "canonical_transcript", "streaming"} <= set(item["proof_atoms"]):
            raise V4ContractViolation("source row is missing mandatory Hermes proof atoms")
    if seen != {(pack, item) for pack, item in v3 if pack != "runtime_active"}:
        raise V4ContractViolation("v4 source rows do not form the exact non-runtime v3 bijection")
    counts = Counter(row["source_pack"] for row in rows)
    per_pack = {pack: {"rows": counts[pack], "mandatory_paths": sum(len(row["mandatory_paths"]) for row in rows if row["source_pack"] == pack)} for pack in PACK_COUNTS}
    if per_pack != {pack: {"rows": rows_count, "mandatory_paths": path_count} for pack, (rows_count, path_count) in PACK_COUNTS.items()}:
        raise V4ContractViolation("v4 pack cardinality or mandatory path count drifted")
    dispositions = Counter(row["disposition"] for row in rows)
    expected_dispositions = {"carry": 8, "replace": 102, "retire-with-successor": 3, "split": 11}
    if dict(dispositions) != expected_dispositions:
        raise V4ContractViolation("v4 disposition totals drifted")
    _validate_runtime(document, v3)
    if ledger is not None:
        _validate_ledger(ledger, document)
    if predecessor_map is not None:
        _validate_predecessor_map(predecessor_map, rows)
    required_trial_packets = sum(len(row["mandatory_paths"]) * len(required_trial_indexes(row)) for row in rows)
    consequential_rows = sum("consequential" in row["repeat_policy"]["triggers"] for row in rows)
    provider_live_rows = sum(row["provider_live_required"] is True for row in rows)
    if required_trial_packets != REQUIRED_TRIAL_PACKETS or consequential_rows != CONSEQUENTIAL_ROW_COUNT:
        raise V4ContractViolation("v4 repeat accounting does not preserve immutable v3 semantics")
    if provider_live_rows != PROVIDER_LIVE_ROW_COUNT:
        raise V4ContractViolation("v4 provider-live accounting does not match the frozen execution map")
    return {"counts": per_pack, "total_rows": len(rows), "mandatory_paths": sum(len(row["mandatory_paths"]) for row in rows), "required_trial_packets": required_trial_packets, "provider_live_rows": provider_live_rows, "disposition_totals": dict(dispositions), "predecessor_rows": len(seen)}


def _validate_ledger(value: Mapping[str, Any], contract: Mapping[str, Any]) -> None:
    if set(value) != {"schema_version", "ledger_name", "ledger_version", "predecessor", "target", "rows", "proof_boundary"}:
        raise V4ContractViolation("v4 boundary ledger fields are not closed")
    if value["schema_version"] != 4 or value["ledger_version"] != V4_VERSION:
        raise V4ContractViolation("v4 boundary ledger version is unsupported")
    predecessor = _mapping(value["predecessor"], "ledger.predecessor")
    if predecessor != {"contract_sha256": V3_HASHES["contract_sha256"], "ledger_sha256": V3_HASHES["boundary_ledger_sha256"], "result_schema_sha256": V3_HASHES["result_schema_sha256"]}:
        raise V4ContractViolation("v4 boundary ledger predecessor does not bind frozen v3")
    if value["target"] != contract["contract"]["target"]:
        raise V4ContractViolation("v4 boundary ledger target does not match contract")
    rows = value["rows"]
    if not isinstance(rows, list) or len(rows) != 23:
        raise V4ContractViolation("v4 boundary ledger must retain all 23 rows")
    expected_ids = {row["source_item_id"] for row in contract["source_rows"] if row["source_pack"] == "agent_sdk_boundary"}
    seen = set()
    for index, row in enumerate(rows):
        item = _mapping(row, f"ledger.rows[{index}]")
        if set(item) != {"id", "predecessor_id", "successor_id", "classification", "ownership_mode", "proof_atoms"}:
            raise V4ContractViolation("v4 ledger row fields are not closed")
        if item["id"] not in expected_ids or item["id"] in seen or item["predecessor_id"] != item["id"]:
            raise V4ContractViolation("v4 ledger row identity is missing or duplicated")
        seen.add(item["id"])
        if item["successor_id"] != f"hermes-v4/agent_sdk_boundary/{item['id']}" or item["classification"] not in {"covered_current", "equivalent_host", "requires_0_3_239", "not_runtime_applicable"}:
            raise V4ContractViolation("v4 ledger successor or classification is unsafe")
        expected_mode, expected_atoms = _ownership("agent_sdk_boundary", item["id"])
        if item["ownership_mode"] != expected_mode or set(item["proof_atoms"]) < set(expected_atoms):
            raise V4ContractViolation("v4 ledger ownership proof is incomplete")
    if seen != expected_ids:
        raise V4ContractViolation("v4 ledger does not preserve all 23 v3 boundary rows")


def load_v4_boundary_ledger(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    document = _load_yaml(source)
    # Ledger rows are compact [id, classification] tuples in the artifact.
    raw_rows = document.get("rows")
    if isinstance(raw_rows, list) and raw_rows and all(isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)) for row in raw_rows):
        expanded = []
        for row in raw_rows:
            if len(row) != 2:
                raise V4ContractViolation("v4 ledger compact row is malformed")
            identifier, classification = row
            identifier = _text(identifier, "ledger row id")
            mode, atoms = _ownership("agent_sdk_boundary", identifier)
            expanded.append({"id": identifier, "predecessor_id": identifier, "successor_id": f"hermes-v4/agent_sdk_boundary/{identifier}", "classification": classification, "ownership_mode": mode, "proof_atoms": list(atoms)})
        document = {**document, "rows": expanded}
    # Validate the closed ledger envelope even when loaded independently. The
    # full contract call repeats this check against the v4 target and row set.
    fake_contract = {"contract": {"target": document.get("target")}, "source_rows": [{"source_pack": "agent_sdk_boundary", "source_item_id": row.get("id")} for row in document.get("rows", ()) if isinstance(row, Mapping)]}
    _validate_ledger(document, fake_contract)
    return document


def _validate_predecessor_map(value: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> None:
    required = {"schema_version", "map_version", "predecessor_contract_sha256", "predecessor_ledger_sha256", "predecessor_result_schema_sha256", "source_artifact", "successor_pattern", "row_count", "mandatory_path_count", "runtime_soak", "ownership_preflights"}
    if set(value) != required:
        raise V4ContractViolation("predecessor map fields are not closed")
    if value["schema_version"] != 4 or value["map_version"] != V4_VERSION:
        raise V4ContractViolation("predecessor map version is unsupported")
    if value["predecessor_contract_sha256"] != V3_HASHES["contract_sha256"] or value["predecessor_ledger_sha256"] != V3_HASHES["boundary_ledger_sha256"] or value["predecessor_result_schema_sha256"] != V3_HASHES["result_schema_sha256"]:
        raise V4ContractViolation("predecessor map is not pinned to v3")
    if value["source_artifact"] != "parity-contract-v4.yaml" or value["successor_pattern"] != "hermes-v4/{source_pack}/{source_item_id}" or value["row_count"] != 124 or value["mandatory_path_count"] != 220 or value["runtime_soak"] != {"turns": 100, "mandatory_paths": ["positive", "denial", "recovery"]} or value["ownership_preflights"] != list(OWNERSHIP_PREFLIGHTS):
        raise V4ContractViolation("predecessor map accounting envelope drifted")
    if len(rows) != value["row_count"] or sum(len(row["mandatory_paths"]) for row in rows) != value["mandatory_path_count"]:
        raise V4ContractViolation("predecessor map does not cover every source row/path")


def load_v4_predecessor_map(path: str | Path) -> dict[str, Any]:
    return _load_yaml(Path(path).expanduser().resolve())


def artifact_sha256(path: str | Path) -> str:
    """Return a file digest for a manifest without reading secret material."""

    return sha256_file(Path(path).expanduser().resolve())


def load_v4_manifest(path: str | Path) -> dict[str, Any]:
    """Validate the v4 artifact manifest and all hashes it names."""

    source = Path(path).expanduser().resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V4ContractViolation("v4 manifest cannot be read safely") from exc
    document = _mapping(value, "v4 manifest")
    expected = {"schema_version", "manifest_version", "contract_path", "contract_sha256", "boundary_ledger_path", "boundary_ledger_sha256", "predecessor_map_path", "predecessor_map_sha256", "result_schema_path", "result_schema_sha256", "predecessor", "target", "counts", "source_rows_sha256", "runtime_soak", "ownership_preflights", "candidate_freeze_inputs", "manifest_sha256", "proof_boundary"}
    if set(document) != expected or document["schema_version"] != 4 or document["manifest_version"] != V4_VERSION:
        raise V4ContractViolation("v4 manifest fields or version are not closed")
    for path_key, hash_key in (("contract_path", "contract_sha256"), ("boundary_ledger_path", "boundary_ledger_sha256"), ("predecessor_map_path", "predecessor_map_sha256"), ("result_schema_path", "result_schema_sha256")):
        named = source.parent / _text(document[path_key], path_key)
        if artifact_sha256(named) != _digest(document[hash_key], hash_key):
            raise V4ContractViolation(f"v4 manifest hash mismatch for {path_key}")
    predecessor = _mapping(document["predecessor"], "manifest.predecessor")
    if predecessor != {"plugin_source_commit": V3_SOURCE_COMMIT, "contract_sha256": V3_HASHES["contract_sha256"], "boundary_ledger_sha256": V3_HASHES["boundary_ledger_sha256"], "result_schema_sha256": V3_HASHES["result_schema_sha256"]}:
        raise V4ContractViolation("v4 manifest predecessor mismatch")
    if document["target"] != {"sdk_distribution": V4_SDK_DISTRIBUTION, "sdk_version": V4_SDK_VERSION, "cli_version": V4_CLI_VERSION, "model": V4_MODEL} or document["counts"] != {"v2_non_soak": {"rows": 53, "mandatory_paths": 53}, "openclaw_active": {"rows": 12, "mandatory_paths": 36}, "agent_sdk_boundary": {"rows": 23, "mandatory_paths": 23}, "clawprobench_native": {"rows": 36, "mandatory_paths": 108}, "total_rows": 124, "mandatory_paths": 220, "required_trial_packets": REQUIRED_TRIAL_PACKETS, "provider_live_rows": PROVIDER_LIVE_ROW_COUNT} or document["runtime_soak"] != {"turns": 100, "mandatory_paths": 3} or document["ownership_preflights"] != list(OWNERSHIP_PREFLIGHTS):
        raise V4ContractViolation("v4 manifest accounting or target identity drifted")
    candidate = _mapping(document["candidate_freeze_inputs"], "manifest.candidate_freeze_inputs")
    if set(candidate) != {"plugin_sha", "host_sha", "wheel_sha256", "profile_sha256"} or any(value is not None for value in candidate.values()):
        raise V4ContractViolation("v4 candidate freeze inputs must remain unresolved")
    contract = load_v4_contract(source.parent / document["contract_path"])
    if document["source_rows_sha256"] != source_rows_sha256(contract["source_rows"]):
        raise V4ContractViolation("v4 source-row digest does not match the contract")
    unsigned = dict(document)
    unsigned.pop("manifest_sha256")
    if document["manifest_sha256"] != sha256_value(unsigned):
        raise V4ContractViolation("v4 manifest self-hash does not match")
    return document


def source_rows_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    return sha256_value([{key: row[key] for key in ("source_pack", "source_item_id", "predecessor_execution_id", "disposition", "successor_id", "mandatory_paths", "expected_trace", "repeat_policy", "primary_proof", "secondary_proof", "provider_live_required")} for row in rows])


__all__ = [
    "DISPOSITIONS",
    "OWNERSHIP_PREFLIGHTS",
    "PACK_COUNTS",
    "PATH_NAMES",
    "REQUIRED_TRIAL_PACKETS",
    "V3_RESULT_CATALOG_HASH",
    "V3_RESULT_CONTRACT_HASH",
    "V4_CLI_VERSION",
    "V4_MODEL",
    "V4_RUNNER_ID",
    "V4_RUNNER_VERSION",
    "V4_SDK_DISTRIBUTION",
    "V4_SDK_VERSION",
    "V4_VERSION",
    "V4ContractViolation",
    "artifact_sha256",
    "load_v4_boundary_ledger",
    "load_v4_contract",
    "load_v4_manifest",
    "load_v4_predecessor_map",
    "mandatory_path_count",
    "required_trial_indexes",
    "source_rows_sha256",
    "validate_v4_contract",
]


def mandatory_path_count(rows: Sequence[Mapping[str, Any]]) -> int:
    return sum(len(row.get("mandatory_paths", ())) for row in rows)
