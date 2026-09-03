"""Provider-free execution mapping for immutable parity v4 live rows."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .hashing import sha256_file
from .v4_contract import V4ContractViolation, V4_MODEL, V4_SDK_DISTRIBUTION, V4_VERSION, load_v4_contract, load_v4_predecessor_map, required_trial_indexes, validate_v4_contract

LIVE_MAP_SCHEMA_VERSION = 1
LIVE_MAP_VERSION = "1.0.0"
LIVE_ROW_COUNT, LIVE_MANDATORY_PATH_COUNT, LIVE_TRIAL_PACKET_COUNT = 70, 158, 242
PARENT_CALL_COUNT, CHILD_CALL_COUNT, TOTAL_CALL_COUNT = 120, 16, 136
TURN_BUDGET, RESERVE_CALL_COUNT, EFFECTIVE_PROVIDER = 180, 44, "fable"
_HEX64, _SHA1, _SAFE = re.compile(r"^[0-9a-f]{64}$"), re.compile(r"^[0-9a-f]{40}$"), re.compile(r"^[A-Za-z0-9_.:/-]{1,240}$")
_FEATURES = {"F0": ("route/preflight", "parent_text", 14, 0), "F1": ("parent/input/state", "parent_state", 10, 0), "F2": ("tools/approval/memory", "host_tool_pdr", 22, 3), "F3": ("delegation/handoff", "host_delegate", 13, 11), "F4": ("background/restart", "host_background", 8, 2), "F5": ("memory/session", "memory_session", 18, 0), "F6": ("docs/skills", "docs_skills", 10, 0), "F7": ("browser/cross-surface", "local_cross_surface", 18, 0), "F8": ("adversarial/composite", "adversarial_local", 7, 0)}
_PACKS = {"v2_non_soak": {"rows": 26, "mandatory_paths": 26, "required_trial_packets": 38}, "openclaw_active": {"rows": 8, "mandatory_paths": 24, "required_trial_packets": 36}, "clawprobench_native": {"rows": 36, "mandatory_paths": 108, "required_trial_packets": 168}}
_EXTERNAL = frozenset({"v2_non_soak/OPS-02", "clawprobench_native/constraints_22_message_audience_boundary_live", "clawprobench_native/constraints_23_external_approval_boundary_live", "clawprobench_native/error_recovery_20_browser_cron_message_orchestration_live", "clawprobench_native/synthesis_24_browser_message_reschedule_live", "clawprobench_native/synthesis_28_browser_internal_external_split_live"})
_ALIASES = {"codex-luna": ("openai-codex/gpt-5.6-luna", {"v2_non_soak/AUTH-02", "v2_non_soak/ORCH-03", "v2_non_soak/ORCH-05"}), "codex-sol": ("openai-codex/gpt-5.6-sol", {"v2_non_soak/AUTH-03", "v2_non_soak/ORCH-04", "v2_non_soak/ORCH-05"}), "opencode-free": ("opencode-free", {"v2_non_soak/AUTH-04"})}
_PROOF = ["provider_free_map_construction_and_validation_only", "map_validation_used_zero_provider_auth_gateway_calls", "normal_hermes_gateway_execution_required_for_live_calls", "no_browser_or_external_recipient_delivery", "no_installed_runtime_release_fleet_or_customer_proof"]


class V4LiveMapViolation(ValueError):
    """A provider-free v4 live map is malformed or drifts from v4."""


def _m(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise V4LiveMapViolation(f"{field} must be a mapping")
    return dict(value)


def _s(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or _SAFE.fullmatch(value) is None or any(ord(c) < 32 or ord(c) == 127 for c in value) or any(x in value.casefold() for x in ("raw_prompt", "raw_content", "session_id")):
        raise V4LiveMapViolation(f"{field} is not a safe identifier")
    return value


def _closed(value: Mapping[str, Any], fields: set[str], field: str) -> None:
    if set(value) != fields:
        raise V4LiveMapViolation(f"{field} fields are not closed")


def _artifact(map_path: Path, declared: str) -> Path:
    if Path(declared).is_absolute():
        raise V4LiveMapViolation("source paths must be repository-relative")
    root = map_path.parent.parent if map_path.parent.name == "qa" else map_path.parent
    for candidate in (root / declared, map_path.parent / declared):
        if candidate.is_file():
            return candidate.resolve()
    raise V4LiveMapViolation(f"source artifact is missing: {declared}")


def _source(source: Mapping[str, Any], map_path: Path) -> dict[str, Any]:
    _closed(source, {"contract_path", "contract_sha256", "predecessor_map_path", "predecessor_map_sha256", "predecessor_contract_sha256", "authoring_base_commit", "candidate_identity", "identity_kind", "selection"}, "source")
    cp, pp = _artifact(map_path, _s(source["contract_path"], "source.contract_path")), _artifact(map_path, _s(source["predecessor_map_path"], "source.predecessor_map_path"))
    if not isinstance(source["contract_sha256"], str) or _HEX64.fullmatch(source["contract_sha256"]) is None or source["contract_sha256"] != sha256_file(cp):
        raise V4LiveMapViolation("v4 contract hash does not match the immutable artifact")
    if not isinstance(source["predecessor_map_sha256"], str) or _HEX64.fullmatch(source["predecessor_map_sha256"]) is None or source["predecessor_map_sha256"] != sha256_file(pp):
        raise V4LiveMapViolation("predecessor map hash does not match the immutable artifact")
    if source["predecessor_contract_sha256"] != "e601f41313deb68b77a01402fe3b79c5da90afc7c46e40f87a6bac1850b69d8a" or not isinstance(source["authoring_base_commit"], str) or _SHA1.fullmatch(source["authoring_base_commit"]) is None or source["candidate_identity"] != "unresolved" or source["identity_kind"] != "authoring_base_only" or source["selection"] != "provider_live_required":
        raise V4LiveMapViolation("source predecessor or selection is not frozen")
    try:
        contract, predecessor = load_v4_contract(cp), load_v4_predecessor_map(pp)
        validate_v4_contract(contract, predecessor_map=predecessor)
    except V4ContractViolation as exc:
        raise V4LiveMapViolation("immutable v4 source validation failed") from exc
    return contract


def _rows(raw_rows: Sequence[Any], contract: Mapping[str, Any], feature_ids: set[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    live = [row for row in contract["source_rows"] if row["provider_live_required"]]
    if len(raw_rows) != LIVE_ROW_COUNT or len(live) != LIVE_ROW_COUNT:
        raise V4LiveMapViolation("live map must contain exactly 70 immutable provider-live rows")
    fields = {"source_pack", "source_item_id", "predecessor_execution_id", "ownership_mode", "mandatory_paths", "required_trial_indexes", "feature_id", "mechanism_class", "semantic_aliases", "delivery_mode"}
    rows, order = {}, []
    for index, (raw, frozen) in enumerate(zip(raw_rows, live, strict=True)):
        item = _m(raw, f"rows[{index}]"); _closed(item, fields, f"rows[{index}]")
        pack, identifier = _s(item["source_pack"], "row.source_pack"), _s(item["source_item_id"], "row.source_item_id"); key = f"{pack}/{identifier}"
        if (pack, identifier) != (frozen["source_pack"], frozen["source_item_id"]) or key in rows or item["predecessor_execution_id"] != frozen["predecessor_execution_id"] or item["ownership_mode"] != frozen["ownership_mode"] or item["mandatory_paths"] != frozen["mandatory_paths"] or item["required_trial_indexes"] != list(required_trial_indexes(frozen)):
            raise V4LiveMapViolation(f"{key} identity, path, trial, ownership, or execution binding drifted")
        if not isinstance(item["feature_id"], str) or item["feature_id"] not in feature_ids or item["mechanism_class"] != _FEATURES[item["feature_id"]][1] or not isinstance(item["semantic_aliases"], list) or any(not isinstance(alias, str) for alias in item["semantic_aliases"]) or len(item["semantic_aliases"]) != len(set(item["semantic_aliases"])) or any(alias not in _ALIASES for alias in item["semantic_aliases"]):
            raise V4LiveMapViolation(f"{key} feature or alias binding is unsafe")
        expected_delivery = "host_denial_local_recovery" if key in _EXTERNAL else "local_fixture_only"
        if item["delivery_mode"] != expected_delivery:
            raise V4LiveMapViolation(f"{key} delivery policy is unsafe")
        rows[key], order = item, order + [key]
    return rows, order


def _features(raw_features: Sequence[Any], rows: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    if len(raw_features) != len(_FEATURES):
        raise V4LiveMapViolation("live map must contain exactly nine features")
    result, assigned = {}, []
    fields = {"id", "name", "mechanism_class", "parent_calls", "child_calls", "row_keys"}
    for raw in raw_features:
        item = _m(raw, "feature"); _closed(item, fields, "feature"); fid = item["id"]
        if not isinstance(fid, str) or fid in result or fid not in _FEATURES or tuple(item[x] for x in ("name", "mechanism_class", "parent_calls", "child_calls")) != _FEATURES[fid]:
            raise V4LiveMapViolation("feature identity, mechanism, or budget drifted")
        keys = item["row_keys"]
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys) or len(keys) != len(set(keys)) or any(key not in rows for key in keys):
            raise V4LiveMapViolation(f"{fid} has an invalid row partition")
        result[fid], assigned = item, assigned + keys
    if set(result) != set(_FEATURES) or len(assigned) != len(set(assigned)) or set(assigned) != set(rows) or any(key not in result[row["feature_id"]]["row_keys"] for key, row in rows.items()):
        raise V4LiveMapViolation("features do not partition all 70 live rows exactly once")
    return result


def _children(raw_calls: Sequence[Any], rows: Mapping[str, Mapping[str, Any]], features: Mapping[str, Mapping[str, Any]]) -> None:
    if len(raw_calls) != CHILD_CALL_COUNT:
        raise V4LiveMapViolation("child call budget must contain exactly 16 entries")
    fields = {"call_id", "feature_id", "row_key", "path", "max_iterations", "child_tools", "retry", "delivery", "local_only"}; seen, bindings, counts = set(), set(), Counter()
    for raw in raw_calls:
        item = _m(raw, "child call"); _closed(item, fields, "child call"); call, fid, key, path = _s(item["call_id"], "child call ID"), item["feature_id"], item["row_key"], item["path"]
        if call in seen or not isinstance(fid, str) or fid not in {"F2", "F3", "F4"} or fid not in features or not isinstance(key, str) or not isinstance(path, str) or key not in rows or rows[key]["feature_id"] != fid or path not in rows[key]["mandatory_paths"] or (key, path) in bindings or item["max_iterations"] != 1 or item["child_tools"] != [] or item["retry"] is not False or item["delivery"] is not False or item["local_only"] is not True:
            raise V4LiveMapViolation(f"{call} is not one local no-tool no-retry child call")
        seen.add(call); bindings.add((key, path)); counts[fid] += 1
    if dict(counts) != {fid: spec[3] for fid, spec in _FEATURES.items() if spec[3]}:
        raise V4LiveMapViolation("child call feature budgets drifted")


def _aliases(raw_aliases: Sequence[Any], rows: Mapping[str, Mapping[str, Any]]) -> None:
    if len(raw_aliases) != len(_ALIASES):
        raise V4LiveMapViolation("semantic alias set is incomplete")
    linked = {key: set() for key in rows}; seen = set()
    fields = {"alias_id", "predecessor_route_alias", "row_keys", "effective_model", "execution"}
    for raw in raw_aliases:
        item = _m(raw, "semantic alias"); _closed(item, fields, "semantic alias"); aid = item["alias_id"]
        if not isinstance(aid, str) or aid in seen or aid not in _ALIASES or not isinstance(item["row_keys"], list) or any(not isinstance(key, str) for key in item["row_keys"]) or item["predecessor_route_alias"] != _ALIASES[aid][0] or set(item["row_keys"]) != _ALIASES[aid][1] or item["effective_model"] != V4_MODEL or item["execution"] != "alias_metadata_only":
            raise V4LiveMapViolation("semantic alias metadata attempts alternate-provider execution")
        seen.add(aid)
        for key in _ALIASES[aid][1]:
            if key not in rows:
                raise V4LiveMapViolation("semantic alias references an unknown row")
            linked[key].add(aid)
    if seen != set(_ALIASES) or any(set(rows[key]["semantic_aliases"]) != values for key, values in linked.items()):
        raise V4LiveMapViolation("semantic alias row linkage is incomplete")


def validate_v4_live_execution_map(value: Mapping[str, Any], *, map_path: str | Path | None = None) -> dict[str, Any]:
    """Validate a map and return deterministic accounting metadata only."""
    document = _m(value, "v4 live map"); root_fields = {"schema_version", "map_version", "contract_version", "source", "target", "coverage", "budget", "mechanism_classes", "features", "rows", "child_calls", "external_recipient_policy", "semantic_aliases", "non_executable_rows", "proof_boundary"}; _closed(document, root_fields, "v4 live map")
    if (document["schema_version"], document["map_version"], document["contract_version"]) != (LIVE_MAP_SCHEMA_VERSION, LIVE_MAP_VERSION, V4_VERSION):
        raise V4LiveMapViolation("v4 live map version is unsupported")
    path = Path(map_path).expanduser().resolve() if map_path is not None else Path(__file__).resolve().parents[3] / "qa" / "parity-v4-live-execution-map.yaml"
    contract = _source(_m(document["source"], "source"), path); target = _m(document["target"], "target")
    if target != {"routing_provider": V4_SDK_DISTRIBUTION, "receipt_provider": "anthropic", "effective_model": V4_MODEL, "execution_mode": "normal_hermes_gateway_live", "gateway_entrypoint": "python -m tui_gateway.entry", "map_construction": "provider_free", "external_delivery": "never"}:
        raise V4LiveMapViolation("live map target is not the provider-free Fable route")
    coverage = _m(document["coverage"], "coverage")
    if coverage != {"provider_live_rows": 70, "mandatory_paths": 158, "required_trial_packets": 242, "rows_by_pack": _PACKS}:
        raise V4LiveMapViolation("live map coverage accounting drifted")
    budget = _m(document["budget"], "budget")
    if budget != {"parent_calls": 120, "child_calls": 16, "total_calls": 136, "turn_budget": 180, "reserve_calls": 44} or TOTAL_CALL_COUNT + RESERVE_CALL_COUNT != TURN_BUDGET:
        raise V4LiveMapViolation("live map call budget is unsafe")
    mechanisms = _m(document["mechanism_classes"], "mechanism_classes")
    if set(mechanisms) != {spec[1] for spec in _FEATURES.values()} or any((m := _m(item, "mechanism")).keys() != {"host_owned", "live_call_accounting", "external_delivery", "operation"} or m["host_owned"] is not True or m["live_call_accounting"] != "feature_budgeted" or m["external_delivery"] is not False or not isinstance(m["operation"], str) for item in mechanisms.values()):
        raise V4LiveMapViolation("mechanism classes are not provider-free")
    feature_specs = [_m(item, "feature") for item in document["features"]]; row_map, order = _rows(document["rows"], contract, set(_FEATURES)); features = _features(feature_specs, row_map); _children(document["child_calls"], row_map, features)
    policy = _m(document["external_recipient_policy"], "external_recipient_policy")
    _closed(policy, {"mode", "host_denial", "local_recovery_only", "delivery", "row_keys"}, "external_recipient_policy")
    if policy["mode"] != "host_denial_plus_local_recovery" or policy["host_denial"] is not True or policy["local_recovery_only"] is not True or policy["delivery"] != "never" or not isinstance(policy["row_keys"], list) or set(policy["row_keys"]) != _EXTERNAL or len(policy["row_keys"]) != len(_EXTERNAL):
        raise V4LiveMapViolation("external-recipient predecessors are not denial-plus-local-recovery only")
    _aliases(document["semantic_aliases"], row_map)
    if document["non_executable_rows"] != [] or document["proof_boundary"] != _PROOF:
        raise V4LiveMapViolation("live map proof boundary or executable disposition drifted")
    paths = sum(len(row["mandatory_paths"]) for row in row_map.values()); packets = sum(len(row["mandatory_paths"]) * len(row["required_trial_indexes"]) for row in row_map.values())
    if (paths, packets) != (LIVE_MANDATORY_PATH_COUNT, LIVE_TRIAL_PACKET_COUNT):
        raise V4LiveMapViolation("live map path/trial accounting does not close")
    return {"map_sha256": sha256_file(path) if path.is_file() else None, "provider_live_rows": len(row_map), "mandatory_paths": paths, "required_trial_packets": packets, "parent_calls": PARENT_CALL_COUNT, "child_calls": CHILD_CALL_COUNT, "total_calls": TOTAL_CALL_COUNT, "reserve_calls": RESERVE_CALL_COUNT, "rows_by_pack": dict(Counter(row["source_pack"] for row in row_map.values())), "row_keys": tuple(order), "non_executable_rows": ()}


def load_v4_live_execution_map(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file() or source.stat().st_size > 8 * 1024 * 1024:
        raise V4LiveMapViolation("live map is not a bounded regular file")
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise V4LiveMapViolation("live map cannot be parsed") from exc
    document = _m(document, "v4 live map"); validate_v4_live_execution_map(document, map_path=source); return document


load_live_execution_map = load_v4_live_execution_map
validate_live_execution_map = validate_v4_live_execution_map
__all__ = ["CHILD_CALL_COUNT", "EFFECTIVE_PROVIDER", "LIVE_MANDATORY_PATH_COUNT", "LIVE_MAP_SCHEMA_VERSION", "LIVE_MAP_VERSION", "LIVE_ROW_COUNT", "LIVE_TRIAL_PACKET_COUNT", "PARENT_CALL_COUNT", "RESERVE_CALL_COUNT", "TOTAL_CALL_COUNT", "TURN_BUDGET", "V4LiveMapViolation", "load_live_execution_map", "load_v4_live_execution_map", "validate_live_execution_map", "validate_v4_live_execution_map"]
