"""Strict, SDK-free validation for the parity v3 catalog and input manifests."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .canonical import (
    CanonicalizationError,
    SDK_EVENT_CODES,
    TRACE_REGISTRY,
    canonical_sha256,
    canonicalize,
    load_json,
    validate_sha256,
)

CONTRACT_ID = "hermes-agent-sdk-feature-parity"
SDK_DISTRIBUTION = "claude-agent-sdk"
SDK_VERSION = "0.2.144"
EXPECTED_PACK_COUNTS = {
    "v2_non_soak": 53,
    "openclaw_active": 12,
    "sdk_boundary": 23,
    "clawprobench_native": 36,
}
SDK_STOP_ORDINALS = frozenset((1, 9))
CONSUMERS = ("inventory", "run", "grade")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SAFE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_CAP = re.compile(r"^CAP-[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_SCENARIO = re.compile(r"^SCN-[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_PARTITION = re.compile(r"^PART-[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_ROW = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
_FIXTURE = re.compile(r"^fixture:[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_REF = re.compile(r"^(?:src|test|doc|evidence|ledger):[A-Za-z0-9][A-Za-z0-9_.:#@/-]{0,190}$")
_SRC_REF = re.compile(r"^src:[A-Za-z0-9][A-Za-z0-9_.:#@/-]{0,190}$")
_EVIDENCE_REF = re.compile(r"^evidence:[A-Za-z0-9][A-Za-z0-9_.:#@-]{0,190}$")
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_FORBIDDEN_MARKERS = ("prompt", "transcript", "cookie", "authorization", "private_key")
_STATE_KEYS = ("lifecycle", "approval", "tool", "resume", "billing", "side_effect_count", "boundary_sha256")
_STATE_ENUMS = {"lifecycle": ("fresh", "bound", "running", "completed", "failed", "cancelled", "closed"), "approval": ("not_required", "pending", "granted", "denied", "late_rejected"), "tool": ("none", "requested", "executed", "denied", "cancelled", "failed", "recovered"), "resume": ("absent", "supplied", "accepted", "rejected"), "billing": ("included", "blocked", "unknown", "not_applicable")}
_EMPTY_STATE = {"lifecycle": "fresh", "approval": "not_required", "tool": "none", "resume": "absent", "billing": "not_applicable", "side_effect_count": 0, "boundary_sha256": None}
_PATH_KEYS = ("required", "expected_outcome", "trace_codes", "terminal", "tool_calls", "side_effect_count", "sdk_events", "state_before", "state_after")
_PROOF_KINDS = ("focused_test", "deterministic", "integration", "live", "source_map", "receipt", "ledger")
_CLASSIFICATIONS = ("covered_current", "equivalent_host", "requires_0_3_239", "not_runtime_applicable")
_SURFACES = ("registration", "selection", "approval", "tool", "denial", "recovery", "resume", "isolation", "compaction", "usage", "packaging", "inventory", "sdk")
_LANE_BY_PACK = {"v2_non_soak": "catalog", "openclaw_active": "openclaw", "sdk_boundary": "sdk_boundary", "clawprobench_native": "clawprobench_native"}


class CatalogValidationError(ValueError):
    """Raised when a catalog or catalog-side input violates its closed schema."""


def _bad(field: str = "catalog") -> None:
    raise CatalogValidationError(f"invalid {field}")


def _obj(value: Any, keys: Sequence[str], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        _bad(field)
    return dict(value)


def _list(value: Any, field: str, maximum: int = 256) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        _bad(field)
    return value


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        _bad(field)
    return value


def _int(value: Any, field: str, low: int = 0, high: int = 4096) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        _bad(field)
    return value


def _text(value: Any, field: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        _bad(field)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        _bad(field)
    if "\\" in value or value.startswith("/") or ":/" in value or ".." in value or "//" in value:
        _bad(field)
    lowered = value.casefold()
    if any(marker in lowered for marker in _FORBIDDEN_MARKERS):
        _bad(field)
    return value


def _match(value: Any, pattern: re.Pattern[str], field: str, maximum: int = 256) -> str:
    value = _text(value, field, maximum)
    if pattern.fullmatch(value) is None:
        _bad(field)
    return value


def _sha1(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA1.fullmatch(value) is None:
        _bad(field)
    return value


def _sha256(value: Any, field: str, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    try:
        return validate_sha256(value, field=field)
    except (CanonicalizationError, ValueError):
        _bad(field)
    return None


def _enum(value: Any, allowed: Sequence[str], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        _bad(field)
    return value


def _hash_without(value: Mapping[str, Any], field: str) -> str:
    return canonical_sha256(value, omit_keys=(field,))


def build_contract_envelope() -> dict[str, str]:
    return {"contract_id": CONTRACT_ID, "contract_version": "3.0.0", "catalog_schema_version": "3.0.0", "result_schema_version": "3.0.0", "sdk_distribution": SDK_DISTRIBUTION, "sdk_version": SDK_VERSION}


def hash_receipt(receipt: Mapping[str, Any]) -> str:
    return _hash_without(receipt, "replacement_receipt_sha256")


def hash_source_map(source_packs: Sequence[Mapping[str, Any]]) -> str:
    projection = []
    for pack in sorted(source_packs, key=lambda item: item.get("id", "")):
        projection.append({key: pack[key] for key in ("id", "expected_count", "row_ids", "source", "provenance")})
    return canonical_sha256(projection)


def hash_declared_inventory(inventory: Mapping[str, Any]) -> str:
    return canonical_sha256({"tools": inventory["tools"], "mcp_servers": inventory["mcp_servers"]})


def hash_candidate(candidate: Mapping[str, Any]) -> str:
    return canonical_sha256({key: candidate[key] for key in ("plugin_sha", "host_sha", "wheel_sha256", "sdk_distribution", "sdk_version", "profile_sha256", "runner_id", "runner_version")})


def hash_fixture_manifest(manifest: Mapping[str, Any]) -> str:
    return canonical_sha256(manifest["fixtures"])


def hash_sdk_ledger(ledger: Mapping[str, Any]) -> tuple[str, str]:
    rows = sorted(ledger["rows"], key=lambda row: (row["pack_id"], row["row_id"]))
    rows_hash = canonical_sha256(rows)
    ledger_hash = canonical_sha256({"schema_version": ledger["schema_version"], "rows": ledger["rows"]})
    return rows_hash, ledger_hash


def hash_catalog(catalog: Mapping[str, Any]) -> str:
    return _hash_without(catalog, "catalog_sha256")


def _state(value: Any, field: str) -> dict[str, Any]:
    result = _obj(value, _STATE_KEYS, field)
    for key, allowed in _STATE_ENUMS.items():
        _enum(result[key], allowed, field)
    _int(result["side_effect_count"], field); _sha256(result["boundary_sha256"], field, nullable=True)
    return result


def _proof(value: Any, field: str) -> dict[str, Any]:
    result = _obj(value, ("kind", "ref", "sha256"), field)
    _enum(result["kind"], _PROOF_KINDS, field); _match(result["ref"], _REF, field); _sha256(result["sha256"], field)
    return result


def _tool_call(value: Any, ordinal: int, field: str) -> dict[str, Any]:
    result = _obj(value, ("ordinal", "name", "schema_sha256", "outcome", "request_id"), field)
    if result["ordinal"] != ordinal or isinstance(result["ordinal"], bool):
        _bad(field)
    _match(result["name"], _SAFE, field); _sha256(result["schema_sha256"], field)
    _enum(result["outcome"], ("requested", "executed", "denied", "cancelled", "failed", "recovered"), field)
    request = _obj(result["request_id"], ("mode", "sha256"), field)
    _enum(request["mode"], ("none", "required"), field)
    if request["sha256"] is not None:
        _bad(field)
    return result


def _path(value: Any, role: str, field: str) -> dict[str, Any]:
    result = _obj(value, _PATH_KEYS, field); required = _bool(result["required"], field)
    expected = _enum(result["expected_outcome"], ("PASS", "EXPECTED_NEGATIVE", "NOT_APPLICABLE"), field)
    traces = _list(result["trace_codes"], field)
    for code in traces:
        _enum(code, tuple(TRACE_REGISTRY), field)
    terminal = _obj(result["terminal"], ("kind", "count"), field); kind = _enum(terminal["kind"], ("complete", "cancelled", "failed", "not_applicable"), field); count = _int(terminal["count"], field, 0, 1)
    calls = _list(result["tool_calls"], field, 32)
    for ordinal, call in enumerate(calls, 1):
        _tool_call(call, ordinal, field)
    side_effects = _int(result["side_effect_count"], field)
    events = _list(result["sdk_events"], field, 32)
    for event in events:
        item = _obj(event, ("event", "trace_code"), field)
        mapped = SDK_EVENT_CODES.get(item["event"]) if isinstance(item["event"], str) else None
        if mapped is None or item["trace_code"] != mapped:
            _bad(field)
    before, after = _state(result["state_before"], field), _state(result["state_after"], field)
    if after["side_effect_count"] - before["side_effect_count"] != side_effects:
        _bad(field)
    if not required:
        if expected != "NOT_APPLICABLE" or traces or calls or side_effects or events or kind != "not_applicable" or count != 0 or before != _EMPTY_STATE or after != _EMPTY_STATE:
            _bad(field)
        return result
    if not traces or traces[0] != f"path.{role}.begin" or traces[-1] != f"path.{role}.end" or sum(code.startswith("terminal.") for code in traces) != 1 or f"terminal.{kind}" not in traces:
        _bad(field)
    if role == "denial" and (expected != "EXPECTED_NEGATIVE" or kind not in ("failed", "cancelled")):
        _bad(field)
    if role in ("positive", "recovery") and (expected != "PASS" or kind != "complete"):
        _bad(field)
    if count != 1:
        _bad(field)
    return result


def _inventory(value: Any) -> dict[str, Any]:
    result = _obj(value, ("schema_version", "tools", "mcp_servers", "declared_inventory_sha256"), "tool inventory")
    if result["schema_version"] != 1: _bad("tool inventory")
    for name, entry_keys, owner in (("tools", ("name", "schema_sha256", "declared_by", "enabled"), True), ("mcp_servers", ("name", "schema_sha256", "enabled"), False)):
        entries = _list(result[name], name); names = []
        for entry in entries:
            item = _obj(entry, entry_keys, name)
            _match(item["name"], _SAFE, name); _sha256(item["schema_sha256"], name)
            if owner: _enum(item["declared_by"], ("host", "plugin"), name)
            _bool(item["enabled"], name)
            names.append(item["name"])
        if names != sorted(names) or len(names) != len(set(names)): _bad(name)
    if result["declared_inventory_sha256"] != hash_declared_inventory(result): _bad("tool inventory")
    return result


def _pack(value: Any, expected_id: str) -> dict[str, Any]:
    result = _obj(value, ("id", "expected_count", "row_ids", "source", "provenance", "row_ids_sha256"), "source pack")
    if result["id"] != expected_id or result["expected_count"] != EXPECTED_PACK_COUNTS[expected_id]: _bad("source pack")
    rows = _list(result["row_ids"], "row ids", 256)
    for row in rows:
        _match(row, _ROW, "row id", 80)
    if rows != sorted(rows) or len(rows) != len(set(rows)) or len(rows) != result["expected_count"]: _bad("row ids")
    source = _obj(result["source"], ("kind", "repo_id", "commit_sha", "source_ref", "artifact_sha256"), "source")
    kind = _enum(source["kind"], ("git_commit", "immutable_artifact"), "source")
    _match(source["repo_id"], _SAFE, "source")
    if kind == "git_commit": _sha1(source["commit_sha"], "source")
    elif source["commit_sha"] is not None: _bad("source")
    _match(source["source_ref"], _SRC_REF, "source")
    _sha256(source["artifact_sha256"], "source")
    provenance = _obj(result["provenance"], ("origin_id", "license_id", "attribution_ref"), "provenance")
    _match(provenance["origin_id"], _SAFE, "provenance")
    _enum(provenance["license_id"], ("MIT", "Apache-2.0", "BSD-3-Clause", "CC-BY-4.0", "proprietary-approved"), "provenance")
    _match(provenance["attribution_ref"], _REF, "provenance")
    _sha256(result["row_ids_sha256"], "source pack")
    if result["row_ids_sha256"] != canonical_sha256(rows): _bad("source pack")
    return result


def _predecessor(value: Any) -> dict[str, Any]:
    result = _obj(value, ("contract_version", "contract_sha256", "baseline_sha", "evidence", "replacement_receipt"), "predecessor")
    if result["contract_version"] != "2.0.0": _bad("predecessor")
    _sha256(result["contract_sha256"], "predecessor"); _sha1(result["baseline_sha"], "predecessor")
    evidence = _obj(result["evidence"], ("ref", "sha256"), "evidence")
    _match(evidence["ref"], _EVIDENCE_REF, "evidence"); _sha256(evidence["sha256"], "evidence")
    receipt = _obj(result["replacement_receipt"], ("receipt_schema_version", "ref", "receipt_artifact_sha256", "replacement_receipt_sha256", "prior_mechanism_id", "prior_gate_id", "successor_mechanism_id", "successor_gate_id", "v2_evidence_immutable"), "receipt")
    if receipt["receipt_schema_version"] != 1 or receipt["prior_mechanism_id"] != "parity-v2" or receipt["prior_gate_id"] != "passive-soak-48h-100-run" or receipt["successor_mechanism_id"] != "parity-v3" or receipt["successor_gate_id"] != "feature-parity-v3" or receipt["v2_evidence_immutable"] is not True: _bad("receipt")
    _match(receipt["ref"], _EVIDENCE_REF, "receipt"); _sha256(receipt["receipt_artifact_sha256"], "receipt"); _sha256(receipt["replacement_receipt_sha256"], "receipt")
    if receipt["replacement_receipt_sha256"] != hash_receipt(receipt): _bad("receipt")
    return result


def _sdk_ledger(value: Any, source_keys: set[tuple[str, str]]) -> tuple[dict[str, Any], dict[tuple[str, str], str]]:
    result = _obj(value, ("schema_version", "rows", "rows_sha256", "ledger_sha256"), "sdk ledger")
    if result["schema_version"] != 1: _bad("sdk ledger")
    rows = _list(result["rows"], "sdk rows", 23)
    keys, classifications = set(), {}
    for ordinal, row in enumerate(rows, 1):
        item = _obj(row, ("pack_id", "row_id", "ordinal", "executable", "classification", "proof"), "sdk row")
        if item["pack_id"] != "sdk_boundary" or item["ordinal"] != ordinal or isinstance(item["ordinal"], bool): _bad("sdk row")
        _match(item["row_id"], _ROW, "sdk row", 80)
        key = (item["pack_id"], item["row_id"])
        if key in keys or key not in source_keys: _bad("sdk row")
        keys.add(key)
        _bool(item["executable"], "sdk row")
        classification = _enum(item["classification"], _CLASSIFICATIONS, "sdk row")
        if item["executable"] and classification == "not_runtime_applicable": _bad("sdk row")
        proof = _obj(item["proof"], ("ref", "sha256"), "sdk proof"); _match(proof["ref"], _REF, "sdk proof"); _sha256(proof["sha256"], "sdk proof")
        classifications[key] = classification
        if ordinal in SDK_STOP_ORDINALS and (not item["executable"] or classification != "requires_0_3_239"): _bad("sdk stop")
    if keys != source_keys or len(rows) != 23: _bad("sdk ledger")
    hashes = hash_sdk_ledger(result)
    if (result["rows_sha256"], result["ledger_sha256"]) != hashes: _bad("sdk ledger")
    return result, classifications


def _capability(value: Any, source_keys: set[tuple[str, str]], inventory_names: dict[str, str], ledger_classes: dict[tuple[str, str], str]) -> tuple[dict[str, Any], set[tuple[str, str]]]:
    keys = ("id", "source_rows", "scenario_id", "lane", "surface", "owner", "consumers", "positive_path", "denial_path", "recovery_path", "state_before", "state_after", "expected_trace", "primary_proof", "secondary_proof", "repeat_policy", "session_scope", "sdk_classification", "required")
    result = _obj(value, keys, "capability")
    _match(result["id"], _CAP, "capability")
    source_rows = _list(result["source_rows"], "source rows")
    mapped = set()
    for entry in source_rows:
        item = _obj(entry, ("pack_id", "row_id"), "source row")
        _enum(item["pack_id"], tuple(EXPECTED_PACK_COUNTS), "source row")
        _match(item["row_id"], _ROW, "source row", 80)
        key = (item["pack_id"], item["row_id"])
        if key in mapped or key not in source_keys: _bad("source row")
        mapped.add(key)
    if not source_rows or source_rows != sorted(source_rows, key=lambda item: (item["pack_id"], item["row_id"])): _bad("source rows")
    _match(result["scenario_id"], _SCENARIO, "scenario")
    if result["lane"] not in _LANE_BY_PACK.values() or len({_LANE_BY_PACK[item["pack_id"]] for item in source_rows}) != 1 or result["lane"] != _LANE_BY_PACK[source_rows[0]["pack_id"]]: _bad("lane")
    _enum(result["surface"], _SURFACES, "surface")
    _enum(result["owner"], ("plugin", "host", "exact_pair"), "owner")
    if result["consumers"] != list(CONSUMERS): _bad("consumers")
    paths = [_path(result[name], name.removesuffix("_path"), name) for name in ("positive_path", "denial_path", "recovery_path")]
    _state(result["state_before"], "capability state")
    _state(result["state_after"], "capability state")
    traces = _list(result["expected_trace"], "expected trace")
    if any(code not in TRACE_REGISTRY for code in traces): _bad("expected trace")
    _proof(result["primary_proof"], "primary proof")
    secondary = _list(result["secondary_proof"], "secondary proof", 3)
    for proof in secondary: _proof(proof, "secondary proof")
    repeat = _obj(result["repeat_policy"], ("mode", "reason"), "repeat policy")
    mode = _enum(repeat["mode"], ("once", "consecutive_3"), "repeat policy")
    reason = _enum(repeat["reason"], ("stable", "consequential", "initial_failure", "unstable"), "repeat policy")
    if (mode == "once") != (reason == "stable"): _bad("repeat policy")
    _enum(result["session_scope"], ("isolated_cell", "one_logical_session"), "session scope")
    classification = _enum(result["sdk_classification"], _CLASSIFICATIONS, "sdk classification")
    sdk_keys = [key for key in mapped if key[0] == "sdk_boundary"]
    if len(sdk_keys) > 1: _bad("sdk classification")
    expected_class = ledger_classes.get(sdk_keys[0], "not_runtime_applicable") if sdk_keys else "not_runtime_applicable"
    if classification != expected_class or result["required"] is not True or (classification != "not_runtime_applicable" and not any(path["required"] for path in paths)): _bad("capability")
    if any(call["name"] not in inventory_names or inventory_names[call["name"]] != call["schema_sha256"] for path in paths for call in path["tool_calls"]): _bad("tool binding")
    return result, mapped


def _partition(value: Any, capability_ids: set[str], scopes: dict[str, str]) -> dict[str, Any]:
    result = _obj(value, ("id", "session_scope", "capability_ids", "capability_set_sha256"), "partition")
    _match(result["id"], _PARTITION, "partition"); scope = _enum(result["session_scope"], ("isolated_cell", "one_logical_session"), "partition")
    ids = _list(result["capability_ids"], "partition capabilities")
    for identifier in ids: _match(identifier, _CAP, "partition capability")
    if ids != sorted(ids) or len(ids) != len(set(ids)) or set(ids) - capability_ids or not ids: _bad("partition capabilities")
    if any(scopes[identifier] != scope for identifier in ids): _bad("partition scope")
    if result["capability_set_sha256"] != canonical_sha256(ids): _bad("partition")
    return result


def validate_catalog(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        catalog = canonicalize(value)
    except (CanonicalizationError, TypeError, ValueError) as exc:
        raise CatalogValidationError("invalid catalog") from exc
    keys = ("catalog_schema_version", "contract_id", "contract_sha256", "source_map_sha256", "catalog_sha256", "predecessor", "source_packs", "scope_partitions", "capabilities", "tool_inventory", "sdk_ledger")
    catalog = _obj(catalog, keys, "catalog")
    if catalog["catalog_schema_version"] != "3.0.0" or catalog["contract_id"] != CONTRACT_ID: _bad("catalog")
    envelope = build_contract_envelope()
    if catalog["contract_sha256"] != canonical_sha256(envelope): _bad("contract hash")
    _sha256(catalog["source_map_sha256"], "source map hash"); _sha256(catalog["catalog_sha256"], "catalog hash")
    _predecessor(catalog["predecessor"])
    packs = _list(catalog["source_packs"], "source packs", 4)
    if len(packs) != len(EXPECTED_PACK_COUNTS): _bad("source packs")
    for expected_id, pack in zip(EXPECTED_PACK_COUNTS, packs): _pack(pack, expected_id)
    source_keys = {(pack["id"], row) for pack in packs for row in pack["row_ids"]}
    if len(source_keys) != 124: _bad("source map")
    if catalog["source_map_sha256"] != hash_source_map(packs): _bad("source map hash")
    inventory = _inventory(catalog["tool_inventory"])
    inventory_names = {entry["name"]: entry["schema_sha256"] for entry in inventory["tools"]}
    sdk_pack_keys = {(pack["id"], row) for pack in packs if pack["id"] == "sdk_boundary" for row in pack["row_ids"]}
    ledger, ledger_classes = _sdk_ledger(catalog["sdk_ledger"], sdk_pack_keys)
    caps = _list(catalog["capabilities"], "capabilities", 124)
    cap_ids_raw = [cap.get("id") if isinstance(cap, Mapping) else None for cap in caps]
    if any(not isinstance(cap, Mapping) or not isinstance(identifier, str) for cap, identifier in zip(caps, cap_ids_raw)) or cap_ids_raw != sorted(cap_ids_raw): _bad("capabilities")
    cap_ids, mapped_keys, scopes, scenario_ids = set(), set(), {}, set()
    for cap in caps:
        normalized, mapped = _capability(cap, source_keys, inventory_names, ledger_classes)
        if normalized["id"] in cap_ids or normalized["scenario_id"] in scenario_ids or mapped_keys & mapped: _bad("capability bijection")
        cap_ids.add(normalized["id"]); scenario_ids.add(normalized["scenario_id"])
        mapped_keys |= mapped
        scopes[normalized["id"]] = normalized["session_scope"]
    if mapped_keys != source_keys or len(cap_ids) != 124: _bad("capability bijection")
    partitions = _list(catalog["scope_partitions"], "scope partitions", 256)
    seen_partition_ids, partition_caps, partition_order = set(), set(), []
    for partition in partitions:
        normalized = _partition(partition, cap_ids, scopes)
        if normalized["id"] in seen_partition_ids or partition_caps & set(normalized["capability_ids"]): _bad("partition bijection")
        seen_partition_ids.add(normalized["id"]); partition_order.append(normalized["id"]); partition_caps |= set(normalized["capability_ids"])
    if partition_caps != cap_ids or partition_order != sorted(partition_order) or {item["session_scope"] for item in partitions} != set(scopes.values()): _bad("partition bijection")
    if catalog["catalog_sha256"] != hash_catalog(catalog): _bad("catalog hash")
    return catalog


def load_catalog(value: Mapping[str, Any] | str | bytes) -> dict[str, Any]:
    if isinstance(value, (str, bytes)):
        try:
            value = load_json(value, source="catalog")
        except (CanonicalizationError, ValueError) as exc:
            raise CatalogValidationError("invalid catalog JSON") from exc
    return validate_catalog(value)


def validate_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _obj(canonicalize(value), ("candidate_schema_version", "plugin_sha", "host_sha", "wheel_sha256", "sdk_distribution", "sdk_version", "profile_sha256", "runner_id", "runner_version", "candidate_sha256"), "candidate")
    if result["candidate_schema_version"] != 1 or result["sdk_distribution"] != SDK_DISTRIBUTION or result["sdk_version"] != SDK_VERSION or result["runner_id"] != "hermes-parity-v3":
        _bad("candidate")
    _sha1(result["plugin_sha"], "candidate"); _sha1(result["host_sha"], "candidate"); _sha256(result["wheel_sha256"], "candidate"); _sha256(result["profile_sha256"], "candidate")
    _match(result["runner_version"], _SEMVER, "candidate")
    if result["candidate_sha256"] != hash_candidate(result):
        _bad("candidate hash")
    return result


def validate_fixture_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _obj(canonicalize(value), ("fixture_manifest_schema_version", "fixtures", "fixture_manifest_sha256"), "fixture manifest")
    if result["fixture_manifest_schema_version"] != 1:
        _bad("fixture manifest")
    fixtures = _list(result["fixtures"], "fixtures", 256)
    refs = []
    for fixture in fixtures:
        item = _obj(fixture, ("ref", "kind", "content_sha256", "byte_length"), "fixture")
        _match(item["ref"], _FIXTURE, "fixture")
        _enum(item["kind"], ("scenario", "resume", "image", "tool_schema"), "fixture")
        _sha256(item["content_sha256"], "fixture")
        _int(item["byte_length"], "fixture", 0, 16 * 1024 * 1024)
        refs.append(item["ref"])
    if refs != sorted(refs) or len(refs) != len(set(refs)) or result["fixture_manifest_sha256"] != hash_fixture_manifest(result):
        _bad("fixture manifest")
    return result


def validate_scenario(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _obj(canonicalize(value), ("scenario_input_schema_version", "catalog_sha256", "fixture_manifest_sha256", "scope_partition_id", "capabilities", "scenario_sha256"), "scenario")
    if result["scenario_input_schema_version"] != 1:
        _bad("scenario")
    _sha256(result["catalog_sha256"], "scenario"); _sha256(result["fixture_manifest_sha256"], "scenario"); _match(result["scope_partition_id"], _PARTITION, "scenario")
    capabilities = _list(result["capabilities"], "scenario capabilities", 124)
    ids = []
    for capability in capabilities:
        item = _obj(capability, ("capability_id", "scenario_id", "fixture_ref", "fixture_content_sha256", "mode", "session_scope"), "scenario capability")
        _match(item["capability_id"], _CAP, "scenario capability"); _match(item["scenario_id"], _SCENARIO, "scenario capability"); _match(item["fixture_ref"], _FIXTURE, "scenario capability"); _sha256(item["fixture_content_sha256"], "scenario capability"); _enum(item["mode"], ("deterministic", "integration", "live"), "scenario capability"); _enum(item["session_scope"], ("isolated_cell", "one_logical_session"), "scenario capability")
        ids.append(item["capability_id"])
    if ids != sorted(ids) or len(ids) != len(set(ids)) or result["scenario_sha256"] != _hash_without(result, "scenario_sha256"):
        _bad("scenario")
    return result


def validate_resume(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _obj(canonicalize(value), ("resume_input_schema_version", "runtime_id", "runtime_schema_version", "present", "state_sha256", "state_length", "fixture_ref", "fixture_content_sha256", "fixture_manifest_sha256", "resume_sha256"), "resume")
    if result["resume_input_schema_version"] != 1 or result["runtime_id"] != "hermes-claude-agent-sdk" or result["runtime_schema_version"] != 1:
        _bad("resume")
    present = _bool(result["present"], "resume")
    _sha256(result["fixture_manifest_sha256"], "resume")
    if present:
        _sha256(result["state_sha256"], "resume"); _int(result["state_length"], "resume", 1, 512); _match(result["fixture_ref"], _FIXTURE, "resume"); _sha256(result["fixture_content_sha256"], "resume")
    else:
        if result["state_sha256"] is not None or result["state_length"] != 0 or result["fixture_ref"] != "fixture:none" or result["fixture_content_sha256"] is not None:
            _bad("resume")
    if result["resume_sha256"] != _hash_without(result, "resume_sha256"):
        _bad("resume hash")
    return result


__all__ = [
    "CONTRACT_ID", "SDK_DISTRIBUTION", "SDK_VERSION", "EXPECTED_PACK_COUNTS", "SDK_STOP_ORDINALS",
    "CatalogValidationError", "build_contract_envelope", "hash_catalog", "hash_declared_inventory",
    "hash_receipt", "hash_sdk_ledger", "hash_source_map", "load_catalog", "validate_catalog",
    "hash_candidate", "hash_fixture_manifest", "validate_candidate", "validate_fixture_manifest", "validate_resume", "validate_scenario",
]
