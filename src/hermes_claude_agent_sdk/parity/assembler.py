"""Fail-closed accounting and assembly for parity-v3 source fragments."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .canonical import CanonicalizationError, canonicalize
from .contract import EXPECTED_PACK_COUNTS, validate_catalog
from .inventory import InventoryValidationError, validate_declared_inventory

_PACK_ORDER = tuple(EXPECTED_PACK_COUNTS)
_ROW_FIELDS = (
    "id", "capability_id", "scenario_id", "lane", "surface", "owner",
    "owner_issue", "consumers", "required", "source_rows", "source_row",
    "source_location", "primary_proof", "proof", "secondary_proof",
    "repeat_policy", "session_scope", "sdk_classification", "upgrade_issue_ref",
    "expected_paths", "positive_path", "denial_path", "recovery_path",
    "state_before", "state_after", "expected_trace", "mapping_status",
    "admission_status", "expected_path_status", "source_metadata", "adapter",
    "grader", "isolation_resume",
)
_PROOF_KINDS = frozenset({"focused_test", "deterministic", "integration", "live", "source_map", "receipt", "ledger"})
_SCOPES = frozenset({"isolated_cell", "one_logical_session"})


@dataclass(frozen=True, slots=True)
class AssemblyDiagnostic:
    """Stable metadata-only reason an input cannot be frozen."""

    code: str
    message: str
    source_key: tuple[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.source_key is not None: result["source_key"] = {"pack_id": self.source_key[0], "row_id": self.source_key[1]}
        return result


class CatalogAssemblyError(ValueError):
    """Guarded assembly rejection with stable diagnostics."""

    def __init__(self, diagnostics: Sequence[AssemblyDiagnostic], report: "SourceInspectionReport | None" = None) -> None:
        self.diagnostics = tuple(diagnostics); self.report = report; super().__init__("; ".join(item.code for item in self.diagnostics) or "catalog assembly rejected")


@dataclass(frozen=True, slots=True)
class SourceInspectionReport(Mapping[str, Any]):

    pack_counts: Mapping[str, int]
    rows: tuple[Mapping[str, Any], ...]
    source_keys: tuple[tuple[str, str], ...]
    residual_gaps: tuple[Mapping[str, Any], ...]
    diagnostics: tuple[AssemblyDiagnostic, ...]
    missing_session_scope: tuple[tuple[str, str], ...]
    missing_sdk_proof_kind: tuple[tuple[str, str], ...]
    sdk_rows_without_required_path: tuple[tuple[str, str], ...]
    inventory_gap_rows: tuple[tuple[str, str], ...]
    issue_16: Mapping[str, Any]
    provenance: Mapping[str, Any]
    accounting: Mapping[str, Any]
    catalog: None = None
    catalog_sha256: None = None

    @property
    def source_rows(self) -> tuple[Mapping[str, Any], ...]: return self.rows

    @property
    def gaps(self) -> tuple[Mapping[str, Any], ...]: return self.residual_gaps

    @property
    def sdk_stop(self) -> Mapping[str, Any]: return self.issue_16

    def to_dict(self) -> dict[str, Any]:
        gap_counts = Counter(item["code"] for item in self.residual_gaps)
        return {
            "pack_counts": dict(self.pack_counts), "accounting": dict(self.accounting),
            "source_keys": [{"pack_id": p, "row_id": r} for p, r in self.source_keys], "rows": list(self.rows), "source_rows": list(self.rows),
            "residual_gaps": list(self.residual_gaps), "gaps": list(self.residual_gaps),
            "gap_counts": dict(sorted(gap_counts.items())),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "missing_session_scope": [{"pack_id": p, "row_id": r} for p, r in self.missing_session_scope], "missing_session_scope_assignments": [{"pack_id": p, "row_id": r} for p, r in self.missing_session_scope], "missing_session_scope_count": len(self.missing_session_scope),
            "missing_sdk_proof_kind": [{"pack_id": p, "row_id": r} for p, r in self.missing_sdk_proof_kind], "missing_sdk_primary_proof_kinds": [{"pack_id": p, "row_id": r} for p, r in self.missing_sdk_proof_kind], "missing_sdk_proof_kind_count": len(self.missing_sdk_proof_kind),
            "sdk_rows_without_required_path": [{"pack_id": p, "row_id": r} for p, r in self.sdk_rows_without_required_path], "sdk_no_required_path_rows": [{"pack_id": p, "row_id": r} for p, r in self.sdk_rows_without_required_path], "sdk_rows_without_required_path_count": len(self.sdk_rows_without_required_path),
            "inventory_gap_rows": [{"pack_id": p, "row_id": r} for p, r in self.inventory_gap_rows], "inventory_status": "PENDING" if self.inventory_gap_rows else "UNRESOLVED", "issue_16": dict(self.issue_16), "sdk_stop": dict(self.issue_16), "provenance": dict(self.provenance), "catalog": None, "catalog_sha256": None,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


def _canonical(value: Any) -> Any:
    try: return canonicalize(value)
    except (CanonicalizationError, TypeError, ValueError): return None


def _source_key(row: Mapping[str, Any], pack_id: str) -> tuple[str, str] | None:
    values = row.get("source_rows")
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)) and len(values) == 1:
        item = values[0]
        if isinstance(item, Mapping) and isinstance(item.get("pack_id"), str) and isinstance(item.get("row_id"), str):
            return item["pack_id"], item["row_id"]
    if pack_id == "sdk_boundary" and isinstance(row.get("pack_id"), str) and isinstance(row.get("row_id"), str):
        return row["pack_id"], row["row_id"]
    source_row = row.get("source_row")
    if isinstance(source_row, Mapping) and isinstance(source_row.get("id"), str): return pack_id, source_row["id"]
    return None


def _pack_parts(value: Any, diagnostics: list[AssemblyDiagnostic]) -> tuple[str | None, Mapping[str, Any], list[Any]]:
    if not isinstance(value, Mapping):
        diagnostics.append(AssemblyDiagnostic("PACK_NOT_OBJECT", "source pack must be an object"))
        return None, {}, []
    pack_id = value.get("pack_id")
    nested = value.get("source_pack")
    if isinstance(nested, Mapping):
        pack_id, metadata = nested.get("id", pack_id), nested
    else:
        metadata = value
    if not isinstance(pack_id, str): diagnostics.append(AssemblyDiagnostic("PACK_ID_MISSING", "source pack id is required")); return None, {}, []
    rows = value.get("rows") if isinstance(value.get("rows"), list) else value.get("capabilities")
    if not isinstance(rows, list): diagnostics.append(AssemblyDiagnostic("PACK_ROWS_MISSING", "source pack rows must be an array")); rows = []
    return pack_id, metadata, rows


def _gap_record(gap: Any, key: tuple[str, str]) -> Mapping[str, Any]:
    if isinstance(gap, str):
        return {"code": gap, "status": "PENDING", "action": "STOP", "source_key": {"pack_id": key[0], "row_id": key[1]}}
    if isinstance(gap, Mapping):
        result = _canonical(gap)
        if isinstance(result, Mapping):
            result = dict(result)
            result["source_key"] = {"pack_id": key[0], "row_id": key[1]}
            return result
    return {"code": "MALFORMED_SOURCE_GAP", "status": "PENDING", "action": "STOP", "source_key": {"pack_id": key[0], "row_id": key[1]}}


def _row_gaps(row: Mapping[str, Any], key: tuple[str, str]) -> list[Mapping[str, Any]]:
    values: list[Any] = []
    mapping_codes = row.get("mapping_gap_codes")
    admission_codes = row.get("admission_gaps")
    if isinstance(mapping_codes, list):
        values.extend(mapping_codes)
    if isinstance(admission_codes, list) and admission_codes != mapping_codes:
        values.extend(admission_codes)
    structured = row.get("mapping_gaps")
    if isinstance(structured, list):
        values.extend(structured)
    return [_gap_record(item, key) for item in values]


def _selected_row(row: Mapping[str, Any], key: tuple[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {"pack_id": key[0], "row_id": key[1]}
    for field in _ROW_FIELDS:
        if field in row:
            value = _canonical(row[field])
            if value is not None:
                result[field] = value
    proof = row.get("primary_proof") if "primary_proof" in row else row.get("proof")
    result["primary_proof_kind"] = proof.get("kind") if isinstance(proof, Mapping) else None
    if "sdk_classification" not in result and isinstance(row.get("classification"), str):
        result["sdk_classification"] = row["classification"]
    expected = row.get("expected_paths")
    result["has_required_path"] = any(isinstance(path, Mapping) and path.get("required") is True for path in expected.values()) if isinstance(expected, Mapping) else False
    result["source_gaps"] = list(_row_gaps(row, key))
    return result


def _provenance_check(packs: Sequence[tuple[str, Mapping[str, Any], list[Any]]], supplied: Mapping[str, Any] | None, diagnostics: list[AssemblyDiagnostic]) -> dict[str, Any]:
    if supplied is None:
        diagnostics.append(AssemblyDiagnostic("GAP-V4-PROVENANCE-RECONCILIATION", "source provenance must be explicitly reconciled"))
        return {"status": "PENDING", "supplied": False}
    record = _canonical(supplied)
    if not isinstance(record, Mapping):
        diagnostics.append(AssemblyDiagnostic("PROVENANCE_NOT_OBJECT", "source provenance must be canonical metadata")); return {"status": "PENDING", "supplied": True}
    source_packs, legacy_packs = record.get("source_packs"), record.get("packs")
    if source_packs is not None and legacy_packs is not None and _canonical(source_packs) != _canonical(legacy_packs):
        diagnostics.append(AssemblyDiagnostic("PROVENANCE_CONTRADICTION", "source provenance supplies conflicting pack shapes", None)); return {"status": "CONTRADICTORY", "supplied": True, "mismatch_count": 1}
    pack_records = source_packs if source_packs is not None else legacy_packs
    by_id = {item.get("id"): item for item in pack_records if isinstance(item, Mapping)} if isinstance(pack_records, list) else {}
    mismatches: list[str] = []
    scope = record.get("scope")
    if isinstance(scope, Mapping) and scope.get("pack_counts") != dict(EXPECTED_PACK_COUNTS):
        mismatches.append("scope.pack_counts")
    for pack_id, metadata, _ in packs:
        candidate = by_id.get(pack_id)
        if not isinstance(candidate, Mapping):
            mismatches.append(pack_id); continue
        for section in ("source", "provenance"):
            left = metadata.get(section) if isinstance(metadata, Mapping) else None
            right = candidate.get(section)
            if isinstance(left, Mapping) and isinstance(right, Mapping):
                fields = ("kind", "repo_id", "commit_sha", "artifact_sha256") if section == "source" else ("origin_id", "license_id", "attribution_ref")
                for field in fields:
                    if field in left and right.get(field) != left[field]:
                        mismatches.append(f"{pack_id}.{section}.{field}")
    if mismatches:
        diagnostics.append(AssemblyDiagnostic("PROVENANCE_CONTRADICTION", "source provenance does not reconcile", None))
        return {"status": "CONTRADICTORY", "supplied": True, "mismatch_count": len(mismatches)}
    return {"status": "PASS", "supplied": True, "pack_count": len(by_id)}


def inspect_source_fragments(source_fragments: Mapping[str, Any] | Sequence[Mapping[str, Any]], sdk_ledger: Mapping[str, Any] | None = None, *, provenance: Mapping[str, Any] | None = None) -> SourceInspectionReport:
    """Inspect heterogeneous fragments without producing a catalog or hash."""
    diagnostics: list[AssemblyDiagnostic] = []
    if isinstance(source_fragments, Mapping):
        if any(key in source_fragments for key in _PACK_ORDER): values = [source_fragments[key] for key in _PACK_ORDER if key in source_fragments]
        else: values = [source_fragments]
    elif isinstance(source_fragments, Sequence) and not isinstance(source_fragments, (str, bytes)): values = list(source_fragments)
    else:
        values = []
        diagnostics.append(AssemblyDiagnostic("PACK_SET_NOT_ARRAY", "source fragments must be an array or keyed mapping"))
    parsed = [_pack_parts(item, diagnostics) for item in values]
    seen_packs: set[str] = set()
    packs: list[tuple[str, Mapping[str, Any], list[Any]]] = []
    for pack_id, metadata, rows in parsed:
        if pack_id is None:
            continue
        if pack_id in seen_packs:
            diagnostics.append(AssemblyDiagnostic("DUPLICATE_PACK_ID", "source pack ids must be unique"))
            continue
        seen_packs.add(pack_id)
        packs.append((pack_id, metadata, rows))
    packs.sort(key=lambda item: (_PACK_ORDER.index(item[0]) if item[0] in _PACK_ORDER else len(_PACK_ORDER), item[0]))
    for expected in _PACK_ORDER:
        if expected not in seen_packs:
            diagnostics.append(AssemblyDiagnostic("PACK_MISSING", f"required source pack missing: {expected}"))

    rows: list[Mapping[str, Any]] = []
    source_keys: list[tuple[str, str]] = []
    residual: list[Mapping[str, Any]] = []
    missing_scope: list[tuple[str, str]] = []
    missing_proof: list[tuple[str, str]] = []
    missing_path: list[tuple[str, str]] = []
    inventory_rows: list[tuple[str, str]] = []
    key_seen: set[tuple[str, str]] = set()
    pack_counts: dict[str, int] = {}
    for pack_id, metadata, pack_rows in packs:
        count = metadata.get("expected_count") if isinstance(metadata, Mapping) else None
        pack_counts[pack_id] = len(pack_rows)
        if count != EXPECTED_PACK_COUNTS.get(pack_id) or len(pack_rows) != EXPECTED_PACK_COUNTS.get(pack_id):
            diagnostics.append(AssemblyDiagnostic("PACK_COUNT_MISMATCH", f"source pack count is not the strict expected count: {pack_id}"))
        listed = metadata.get("row_ids") if isinstance(metadata, Mapping) else None
        if isinstance(listed, list) and len(listed) != len(set(listed)):
            diagnostics.append(AssemblyDiagnostic("DUPLICATE_ROW_ID", f"source pack row ids are not unique: {pack_id}"))
        for raw_row in pack_rows:
            if not isinstance(raw_row, Mapping):
                diagnostics.append(AssemblyDiagnostic("ROW_NOT_OBJECT", f"source row is not an object: {pack_id}"))
                continue
            key = _source_key(raw_row, pack_id)
            if key is None:
                diagnostics.append(AssemblyDiagnostic("SOURCE_KEY_MISSING", f"source row key is missing: {pack_id}"))
                continue
            if key[0] != pack_id:
                diagnostics.append(AssemblyDiagnostic("SOURCE_KEY_PACK_MISMATCH", "source row pack id disagrees with containing pack", key))
            if key in key_seen:
                diagnostics.append(AssemblyDiagnostic("DUPLICATE_SOURCE_KEY", "source keys must be unique", key))
                continue
            key_seen.add(key); source_keys.append(key)
            selected = _selected_row(raw_row, key)
            rows.append(selected)
            row_gaps = list(selected["source_gaps"])
            residual.extend(row_gaps)
            if selected.get("session_scope") not in _SCOPES:
                missing_scope.append(key)
                residual.append({"code": "GAP-V4-SESSION-SCOPE", "status": "PENDING", "action": "STOP", "source_key": {"pack_id": key[0], "row_id": key[1]}})
            if pack_id == "sdk_boundary" and selected.get("primary_proof_kind") not in _PROOF_KINDS:
                missing_proof.append(key)
                residual.append({"code": "GAP-V4-SDK-PROOF-KIND", "status": "PENDING", "action": "STOP", "source_key": {"pack_id": key[0], "row_id": key[1]}})
            if pack_id == "sdk_boundary" and not selected["has_required_path"]:
                missing_path.append(key)
                residual.append({"code": "GAP-V4-SDK-REQUIRED-PATH", "status": "PENDING", "action": "STOP", "source_key": {"pack_id": key[0], "row_id": key[1]}})
            if any("INVENTORY" in str(gap.get("code", "")) or "TOOL-SCHEMA" in str(gap.get("code", "")) for gap in row_gaps) or selected.get("surface") == "tool":
                inventory_rows.append(key)

    expected_keys = {(pack_id, row_id) for pack_id, metadata, _ in packs for row_id in (metadata.get("row_ids", []) if isinstance(metadata, Mapping) else [])}
    if len(source_keys) != 124 or len(key_seen) != len(source_keys):
        diagnostics.append(AssemblyDiagnostic("SOURCE_KEY_ACCOUNTING", "source keys do not account for exactly 124 unique rows"))
    if expected_keys and expected_keys != key_seen:
        diagnostics.append(AssemblyDiagnostic("SOURCE_ROW_SET_MISMATCH", "retained source keys differ from declared row ids"))
    source_keys.sort(key=lambda item: (_PACK_ORDER.index(item[0]) if item[0] in _PACK_ORDER else len(_PACK_ORDER), item[1]))
    rows.sort(key=lambda item: (_PACK_ORDER.index(item["pack_id"]) if item["pack_id"] in _PACK_ORDER else len(_PACK_ORDER), item["row_id"]))
    stop_rows = sorted((row["row_id"] for row in rows if row["pack_id"] == "sdk_boundary" and row.get("sdk_classification") == "requires_0_3_239"))
    ledger_stop = [item.get("row_id") for item in (sdk_ledger.get("rows", []) if isinstance(sdk_ledger, Mapping) else []) if isinstance(item, Mapping) and item.get("classification") == "requires_0_3_239" and item.get("executable") is True]
    stop_rows = sorted(set(stop_rows) | {item for item in ledger_stop if isinstance(item, str)})
    if sdk_ledger is None:
        diagnostics.append(AssemblyDiagnostic("GAP-V4-SDK-LEDGER", "sdk ledger must be explicitly supplied for final assembly"))
    issue = {"status": "STOP" if stop_rows else "CLEAR", "issue_ref": "issue:16" if stop_rows else None, "upgrade_issue_ref": "issue:16" if stop_rows else None, "rows": stop_rows, "stop_rows": stop_rows}
    if stop_rows:
        for row_id in stop_rows:
            residual.append({"code": "SDK-STOP-ISSUE-16", "status": "STOP", "action": "STOP", "issue_ref": "issue:16", "source_key": {"pack_id": "sdk_boundary", "row_id": row_id}})
    provenance_result = _provenance_check(packs, provenance, diagnostics)
    if not inventory_rows:
        diagnostics.append(AssemblyDiagnostic("GAP-V4-INVENTORY-UNRESOLVED", "source-backed tool/schema inventory is not established"))
    else:
        for key in sorted(set(inventory_rows)):
            residual.append({"code": "GAP-V4-INVENTORY-UNRESOLVED", "status": "PENDING", "action": "STOP", "source_key": {"pack_id": key[0], "row_id": key[1]}})
    residual.sort(key=lambda item: (str(item.get("code", "")), (item.get("source_key") or {}).get("pack_id", ""), (item.get("source_key") or {}).get("row_id", "")))
    diagnostics.sort(key=lambda item: (item.code, item.source_key or ("", ""), item.message))
    accounting = {"expected_pack_counts": dict(EXPECTED_PACK_COUNTS), "retained_pack_counts": {key: pack_counts.get(key, 0) for key in _PACK_ORDER}, "expected_total": 124, "retained_total": len(source_keys), "unique_source_key_count": len(key_seen)}
    return SourceInspectionReport({key: pack_counts.get(key, 0) for key in _PACK_ORDER}, tuple(rows), tuple(source_keys), tuple(residual), tuple(diagnostics), tuple(sorted(set(missing_scope))), tuple(sorted(set(missing_proof))), tuple(sorted(set(missing_path))), tuple(sorted(set(inventory_rows))), issue, provenance_result, accounting)


def _resolve_key(raw: Any, row_lookup: Mapping[str, tuple[str, str]], expected: set[tuple[str, str]]) -> tuple[str, str] | None:
    if isinstance(raw, Mapping) and isinstance(raw.get("pack_id"), str) and isinstance(raw.get("row_id"), str):
        key = raw["pack_id"], raw["row_id"]
    elif isinstance(raw, (tuple, list)) and len(raw) == 2 and all(isinstance(item, str) for item in raw):
        key = raw[0], raw[1]
    elif isinstance(raw, str) and raw in row_lookup:
        key = row_lookup[raw]
    elif isinstance(raw, str) and "/" in raw:
        key = tuple(raw.split("/", 1))  # type: ignore[assignment]
    else:
        return None
    return key if key in expected else None


def _keyed(value: Any, field: str, expected: set[tuple[str, str]], row_lookup: Mapping[str, tuple[str, str]], diagnostics: list[AssemblyDiagnostic], extractor: Any) -> dict[tuple[str, str], Any]:
    result: dict[tuple[str, str], Any] = {}
    entries: list[tuple[Any, Any]] = []
    if isinstance(value, Mapping):
        entries = list(value.items())
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            if not isinstance(item, Mapping):
                diagnostics.append(AssemblyDiagnostic(f"{field.upper()}_ENTRY_INVALID", f"{field} entries must be objects")); continue
            entries.append((item, item))
    else:
        diagnostics.append(AssemblyDiagnostic(f"{field.upper()}_MISSING", f"{field} must be explicitly supplied"))
        return result
    for raw_key, raw_value in entries:
        key = _resolve_key(raw_key, row_lookup, expected)
        if key is None and isinstance(raw_value, Mapping): key = _resolve_key(raw_value, row_lookup, expected)
        if key is None:
            diagnostics.append(AssemblyDiagnostic(f"{field.upper()}_KEY_INVALID", f"{field} contains an unknown source key")); continue
        if key in result:
            diagnostics.append(AssemblyDiagnostic(f"{field.upper()}_DUPLICATE", f"{field} contains a duplicate source key", key)); continue
        result[key] = extractor(raw_value)
    diagnostics.extend(AssemblyDiagnostic(f"{field.upper()}_MISSING_KEY", f"{field} omits an explicit source key", key) for key in sorted(expected - set(result)))
    diagnostics.extend(AssemblyDiagnostic(f"{field.upper()}_EXTRA_KEY", f"{field} contains an extra source key", key) for key in sorted(set(result) - expected))
    return result


def assemble_catalog(source_fragments: Mapping[str, Any] | Sequence[Mapping[str, Any]], *, catalog: Mapping[str, Any] | None = None, predecessor: Mapping[str, Any] | None = None, source_metadata: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None, provenance: Mapping[str, Any] | None = None, scope_assignments: Any = None, scope_partitions: Sequence[Mapping[str, Any]] | None = None, sdk_proof_kinds: Any = None, sdk_path_decisions: Any = None, declared_inventory: Any = None, sdk_ledger: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Guarded final assembly; every strict input is explicit and digest-bound."""
    report = inspect_source_fragments(source_fragments, sdk_ledger, provenance=provenance)
    diagnostics = list(report.diagnostics)
    if report.residual_gaps:
        diagnostics.append(AssemblyDiagnostic("UNRESOLVED_SOURCE_GAPS", "source gaps remain pending and cannot be discarded"))
    if not isinstance(catalog, Mapping):
        diagnostics.append(AssemblyDiagnostic("CATALOG_INPUT_MISSING", "a complete catalog mapping must be explicitly supplied"))
    if not isinstance(predecessor, Mapping):
        diagnostics.append(AssemblyDiagnostic("PREDECESSOR_INPUT_MISSING", "a finalized predecessor receipt must be explicitly supplied"))
    if source_metadata is None:
        diagnostics.append(AssemblyDiagnostic("SOURCE_METADATA_MISSING", "strict source metadata must be explicitly supplied"))
    if provenance is None:
        diagnostics.append(AssemblyDiagnostic("PROVENANCE_INPUT_MISSING", "source provenance must be explicitly supplied"))
    if scope_partitions is None:
        diagnostics.append(AssemblyDiagnostic("SCOPE_PARTITIONS_MISSING", "scope partitions must be explicitly supplied"))
    if sdk_ledger is None:
        diagnostics.append(AssemblyDiagnostic("SDK_LEDGER_MISSING", "sdk ledger must be explicitly supplied"))
    try:
        candidate = canonicalize(catalog) if isinstance(catalog, Mapping) else None
    except (CanonicalizationError, TypeError, ValueError):
        candidate = None
        diagnostics.append(AssemblyDiagnostic("CATALOG_INPUT_INVALID", "catalog input is not canonical JSON"))
    if isinstance(candidate, Mapping) and isinstance(predecessor, Mapping) and candidate.get("predecessor") != _canonical(predecessor):
        diagnostics.append(AssemblyDiagnostic("PREDECESSOR_CONTRADICTION", "explicit predecessor differs from catalog predecessor"))
    if isinstance(candidate, Mapping):
        supplied_packs = source_metadata.get("source_packs") if isinstance(source_metadata, Mapping) else source_metadata
        if not isinstance(supplied_packs, Sequence) or isinstance(supplied_packs, (str, bytes)):
            diagnostics.append(AssemblyDiagnostic("SOURCE_METADATA_INVALID", "source metadata must contain strict source packs"))
        elif _canonical(list(supplied_packs)) != candidate.get("source_packs"):
            diagnostics.append(AssemblyDiagnostic("SOURCE_METADATA_CONTRADICTION", "strict source metadata differs from catalog source packs"))
    expected_keys = set(report.source_keys)
    row_lookup = {key[1]: key for key in report.source_keys}
    assignments = _keyed(scope_assignments, "scope_assignments", expected_keys, row_lookup, diagnostics, lambda item: item.get("session_scope") if isinstance(item, Mapping) else item)
    for key, scope in assignments.items():
        if scope not in _SCOPES:
            diagnostics.append(AssemblyDiagnostic("SCOPE_VALUE_INVALID", "scope assignment is not a closed scope", key))
    sdk_keys = {key for key in expected_keys if key[0] == "sdk_boundary"}
    proof_kinds = _keyed(sdk_proof_kinds, "sdk_proof_kinds", sdk_keys, row_lookup, diagnostics, lambda item: item.get("kind") if isinstance(item, Mapping) else item)
    for key, kind in proof_kinds.items():
        if kind not in _PROOF_KINDS:
            diagnostics.append(AssemblyDiagnostic("SDK_PROOF_KIND_INVALID", "sdk proof kind is not closed", key))
    path_decisions = _keyed(sdk_path_decisions, "sdk_path_decisions", sdk_keys, row_lookup, diagnostics, lambda item: item.get("paths", item) if isinstance(item, Mapping) else item)
    if isinstance(candidate, Mapping):
        caps = candidate.get("capabilities")
        cap_by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
        if isinstance(caps, list):
            for cap in caps:
                if isinstance(cap, Mapping) and isinstance(cap.get("source_rows"), list):
                    for item in cap["source_rows"]:
                        if isinstance(item, Mapping) and isinstance(item.get("pack_id"), str) and isinstance(item.get("row_id"), str):
                            cap_by_key[(item["pack_id"], item["row_id"])] = cap
        for key, scope in assignments.items():
            cap = cap_by_key.get(key)
            if cap is not None and cap.get("session_scope") != scope:
                diagnostics.append(AssemblyDiagnostic("SCOPE_CATALOG_CONTRADICTION", "scope assignment differs from catalog capability", key))
        for key, kind in proof_kinds.items():
            cap = cap_by_key.get(key)
            if cap is not None and isinstance(cap.get("primary_proof"), Mapping) and cap["primary_proof"].get("kind") != kind:
                diagnostics.append(AssemblyDiagnostic("SDK_PROOF_CATALOG_CONTRADICTION", "sdk proof kind differs from catalog capability", key))
        for key, paths in path_decisions.items():
            cap = cap_by_key.get(key)
            expected_paths = {role.removesuffix("_path"): cap.get(role) for role in ("positive_path", "denial_path", "recovery_path")} if cap else None
            if expected_paths is not None and _canonical(paths) != expected_paths:
                diagnostics.append(AssemblyDiagnostic("SDK_PATH_CATALOG_CONTRADICTION", "sdk path decision differs from catalog capability", key))
    try:
        inventory = validate_declared_inventory(declared_inventory) if declared_inventory is not None else None
    except (InventoryValidationError, TypeError, ValueError):
        inventory = None
        diagnostics.append(AssemblyDiagnostic("INVENTORY_INPUT_INVALID", "declared inventory is not strict"))
    if inventory is None:
        diagnostics.append(AssemblyDiagnostic("INVENTORY_INPUT_MISSING", "declared inventory must be explicitly supplied"))
    elif isinstance(candidate, Mapping) and candidate.get("tool_inventory") != inventory.to_dict():
        diagnostics.append(AssemblyDiagnostic("INVENTORY_CONTRADICTION", "declared inventory differs from catalog inventory"))
    if inventory is not None and not (inventory.tools or inventory.mcp_servers) and report.inventory_gap_rows:
        diagnostics.append(AssemblyDiagnostic("EMPTY_SOURCE_BACKED_INVENTORY", "tool behavior requires a non-empty source-backed inventory"))
    if scope_partitions is not None and isinstance(candidate, Mapping) and _canonical(list(scope_partitions)) != candidate.get("scope_partitions"):
        diagnostics.append(AssemblyDiagnostic("SCOPE_PARTITIONS_CONTRADICTION", "explicit partitions differ from catalog partitions"))
    if isinstance(candidate, Mapping) and sdk_ledger is not None and _canonical(sdk_ledger) != candidate.get("sdk_ledger"):
        diagnostics.append(AssemblyDiagnostic("SDK_LEDGER_CONTRADICTION", "explicit sdk ledger differs from catalog ledger"))
    if diagnostics:
        unique = {(item.code, item.message, item.source_key): item for item in diagnostics}
        raise CatalogAssemblyError(tuple(sorted(unique.values(), key=lambda item: (item.code, item.source_key or ("", ""), item.message))), report)
    try:
        return validate_catalog(candidate)
    except Exception as exc:
        raise CatalogAssemblyError((AssemblyDiagnostic("CATALOG_VALIDATION_FAILED", "strict catalog validation rejected the assembled candidate"),), report) from exc


inspect_source_packs = inspect_source_fragments
assemble_source_fragments = inspect_source_fragments
inspect_source_accounting = inspect_source_fragments
normalize_source_fragments = inspect_source_fragments
guarded_assemble_catalog = assemble_catalog

__all__ = ["AssemblyDiagnostic", "CatalogAssemblyError", "SourceInspectionReport", "assemble_catalog", "assemble_source_fragments", "guarded_assemble_catalog", "inspect_source_accounting", "inspect_source_fragments", "inspect_source_packs", "normalize_source_fragments"]
