"""Bind the executable v3 catalog to its exhaustive repo-owned source inputs."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from .canonical import CanonicalizationError, load_json
from .hashing import json_compatible, sha256_file, sha256_value


class SourceAuthorityViolation(ValueError):
    """The catalog and its pinned source-accounting inputs disagree."""


_PACKS = (
    "v2_non_soak",
    "openclaw_active",
    "agent_sdk_boundary",
    "clawprobench_native",
)
_EXPECTED_COUNTS = {
    "v2_non_soak": 53,
    "openclaw_active": 12,
    "agent_sdk_boundary": 23,
    "clawprobench_native": 36,
}
_PRELIMINARY_INPUTS = {
    "parity/v3/source-packs/sdk-boundary.json": "PRELIMINARY_RUNTIME_TEST_ONLY_SELECTION",
    "parity/v3/sdk-ledger.json": "PRELIMINARY_TYPESCRIPT_VERSION_ASSUMPTION",
}
_ACTIVE_REF = re.compile(r":qa/scenarios/(?:[^#]+/)?([^/#]+)\.yaml(?:#|$)")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_SOURCE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SourceAuthorityReport:
    """Sanitized deterministic receipt for the source-to-execution bijection."""

    source_counts: Mapping[str, int]
    source_hashes: Mapping[str, str]
    active_aliases: Mapping[str, str]
    boundary_status_counts: Mapping[str, int]
    authority_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "execution_authority": "qa/parity-contract-v3.yaml",
            "source_counts": dict(self.source_counts),
            "source_hashes": dict(self.source_hashes),
            "active_aliases": dict(self.active_aliases),
            "boundary_status_counts": dict(self.boundary_status_counts),
            "requires_0_3_239_rows": [],
            "authority_hash": self.authority_hash,
        }


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SourceAuthorityViolation(f"{field} must be a mapping")
    return dict(value)


def _sequence(value: Any, field: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise SourceAuthorityViolation(f"{field} must be a list")
    return list(value)


def _safe_path(root: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SourceAuthorityViolation(f"{field} must be a safe relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise SourceAuthorityViolation(f"{field} must stay below the catalog directory")
    resolved = (root / relative).resolve()
    if root != resolved and root not in resolved.parents:
        raise SourceAuthorityViolation(f"{field} escapes the catalog directory")
    if not resolved.is_file() or resolved.stat().st_size > _MAX_SOURCE_BYTES:
        raise SourceAuthorityViolation(f"{field} is not a bounded regular file")
    return resolved


def _json(path: Path, field: str) -> dict[str, Any]:
    try:
        value = load_json(path.read_bytes(), source=str(path))
    except (OSError, CanonicalizationError, TypeError, ValueError) as exc:
        raise SourceAuthorityViolation(f"{field} is not canonical JSON: {exc}") from exc
    return _mapping(value, field)


def _yaml(path: Path, field: str) -> dict[str, Any]:
    try:
        value = json_compatible(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, yaml.YAMLError, TypeError) as exc:
        raise SourceAuthorityViolation(f"{field} is not safe YAML: {exc}") from exc
    return _mapping(value, field)


def _catalog_rows(catalog: Any, pack: str) -> dict[str, Any]:
    rows = {
        item.source_item_id: item
        for item in catalog.capabilities
        if item.source_pack == pack
    }
    if len(rows) != _EXPECTED_COUNTS[pack]:
        raise SourceAuthorityViolation(f"catalog {pack} coverage is incomplete")
    return rows


def _pack_row_ids(document: Mapping[str, Any], pack: str) -> list[str]:
    metadata = _mapping(document.get("source_pack", document), f"{pack}.source_pack")
    if metadata.get("id") != pack or metadata.get("expected_count") != _EXPECTED_COUNTS[pack]:
        raise SourceAuthorityViolation(f"{pack} metadata does not bind the expected source pack")
    rows = _sequence(metadata.get("row_ids"), f"{pack}.row_ids")
    if not all(isinstance(row, str) and row for row in rows):
        raise SourceAuthorityViolation(f"{pack}.row_ids contains an invalid identifier")
    if len(rows) != len(set(rows)) or len(rows) != _EXPECTED_COUNTS[pack]:
        raise SourceAuthorityViolation(f"{pack}.row_ids is not an exact unique source set")
    return rows


def _active_aliases(document: Mapping[str, Any], catalog_rows: Mapping[str, Any]) -> dict[str, str]:
    declared = set(_pack_row_ids(document, "openclaw_active"))
    aliases: dict[str, str] = {}
    for raw in _sequence(document.get("rows"), "openclaw_active.rows"):
        row = _mapping(raw, "openclaw_active.row")
        source_rows = _sequence(row.get("source_rows"), "openclaw_active.source_rows")
        if len(source_rows) != 1:
            raise SourceAuthorityViolation("each OpenClaw accounting row must map one source row")
        source = _mapping(source_rows[0], "openclaw_active.source_row")
        if source.get("pack_id") != "openclaw_active" or not isinstance(source.get("row_id"), str):
            raise SourceAuthorityViolation("OpenClaw accounting source key is invalid")
        proof = _mapping(row.get("primary_proof"), "openclaw_active.primary_proof")
        reference = proof.get("ref")
        if not isinstance(reference, str):
            raise SourceAuthorityViolation("OpenClaw primary proof ref is missing")
        match = _ACTIVE_REF.search(reference)
        if match is None:
            raise SourceAuthorityViolation("OpenClaw primary proof does not identify a scenario")
        row_id, execution_id = source["row_id"], match.group(1)
        if row_id in aliases or execution_id in aliases.values():
            raise SourceAuthorityViolation("OpenClaw source alias mapping is not bijective")
        aliases[row_id] = execution_id
    if set(aliases) != declared or set(aliases.values()) != set(catalog_rows):
        raise SourceAuthorityViolation("OpenClaw source aliases do not map all active-12 rows")
    return dict(sorted(aliases.items()))


def _boundary_rows(
    ledger: Mapping[str, Any], catalog: Any, catalog_rows: Mapping[str, Any]
) -> Counter[str]:
    source = _mapping(ledger.get("source"), "agent_sdk_boundary.source")
    openclaw = _mapping(catalog.contract["references"]["openclaw"], "catalog.openclaw")
    if (
        ledger.get("schema_version") != 1
        or ledger.get("ledger_version") != "3.0.0"
        or ledger.get("sdk_version") != "0.2.144"
        or source.get("commit") != openclaw.get("commit")
        or source.get("root") != "extensions/anthropic"
    ):
        raise SourceAuthorityViolation("Agent SDK ledger identity is not the pinned v3 source")
    policy = _mapping(ledger.get("policy"), "agent_sdk_boundary.policy")
    if policy.get("exclusion_is_pass") is not False or policy.get("release_blocking_source_rows") != 23:
        raise SourceAuthorityViolation("Agent SDK ledger policy must fail closed for all 23 rows")
    rows = _sequence(ledger.get("rows"), "agent_sdk_boundary.rows")
    by_id: dict[str, dict[str, Any]] = {}
    statuses: Counter[str] = Counter()
    for raw in rows:
        row = _mapping(raw, "agent_sdk_boundary.row")
        row_id = row.get("id")
        if not isinstance(row_id, str) or row_id in by_id:
            raise SourceAuthorityViolation("Agent SDK ledger row IDs must be unique")
        if row.get("release_blocking") is not True:
            raise SourceAuthorityViolation("every Agent SDK source row must remain release-blocking")
        if not _sequence(row.get("evidence_refs"), "agent_sdk_boundary.evidence_refs"):
            raise SourceAuthorityViolation("Agent SDK ledger rows require an evidence target")
        status = row.get("status")
        if status not in {
            "covered_current",
            "equivalent_host",
            "requires_0_3_239",
            "not_runtime_applicable",
        }:
            raise SourceAuthorityViolation("Agent SDK ledger row has an unsupported status")
        by_id[row_id] = row
        statuses[status] += 1
    if set(by_id) != set(catalog_rows) or len(by_id) != 23:
        raise SourceAuthorityViolation("Agent SDK ledger does not map the exact boundary-23 set")
    for row_id, capability in catalog_rows.items():
        row = by_id[row_id]
        if row.get("status") != capability.sdk_ledger_status:
            raise SourceAuthorityViolation(f"Agent SDK status drift for {row_id}")
        if row.get("source_ref") != capability.source_ref:
            raise SourceAuthorityViolation(f"Agent SDK source-ref drift for {row_id}")
    if statuses["requires_0_3_239"]:
        raise SourceAuthorityViolation(
            "TypeScript SDK 0.3.239 cannot be a Python dependency requirement without reproduced evidence"
        )
    return statuses


def validate_source_authority(catalog: Any) -> SourceAuthorityReport:
    """Validate the only repo-owned source inputs allowed to back v3 execution."""

    authority = _mapping(catalog.contract.get("source_authority"), "contract.source_authority")
    if set(authority) != {
        "schema_version",
        "execution_authority",
        "accounting_sources",
        "excluded_preliminary_inputs",
    }:
        raise SourceAuthorityViolation("contract.source_authority has an unknown shape")
    if authority["schema_version"] != 1 or authority["execution_authority"] != catalog.path.name:
        raise SourceAuthorityViolation("source authority does not name the supplied execution catalog")
    excluded = _sequence(
        authority["excluded_preliminary_inputs"],
        "contract.source_authority.excluded_preliminary_inputs",
    )
    excluded_map = {
        item.get("path"): item.get("reason_code")
        for item in (_mapping(raw, "excluded_preliminary_input") for raw in excluded)
    }
    if excluded_map != _PRELIMINARY_INPUTS:
        raise SourceAuthorityViolation("preliminary inputs are not explicitly excluded from pass authority")

    entries = _mapping(authority["accounting_sources"], "source_authority.accounting_sources")
    if set(entries) != set(_PACKS):
        raise SourceAuthorityViolation("source authority must bind exactly four RC source packs")
    root = catalog.path.parent.resolve()
    documents: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for pack in _PACKS:
        entry = _mapping(entries[pack], f"source_authority.{pack}")
        if set(entry) != {"path", "sha256"} or not isinstance(entry["sha256"], str) or _SHA256.fullmatch(entry["sha256"]) is None:
            raise SourceAuthorityViolation(f"source_authority.{pack} must bind path and sha256")
        path = _safe_path(root, entry["path"], f"source_authority.{pack}.path")
        actual = sha256_file(path)
        if actual != entry["sha256"]:
            raise SourceAuthorityViolation(f"source_authority.{pack} file hash drift")
        hashes[pack] = actual
        documents[pack] = _yaml(path, pack) if pack == "agent_sdk_boundary" else _json(path, pack)

    catalog_rows = {pack: _catalog_rows(catalog, pack) for pack in _PACKS}
    for pack in ("v2_non_soak", "clawprobench_native"):
        if set(_pack_row_ids(documents[pack], pack)) != set(catalog_rows[pack]):
            raise SourceAuthorityViolation(f"{pack} accounting rows differ from the execution catalog")
    aliases = _active_aliases(documents["openclaw_active"], catalog_rows["openclaw_active"])
    statuses = _boundary_rows(documents["agent_sdk_boundary"], catalog, catalog_rows["agent_sdk_boundary"])
    counts = {pack: len(catalog_rows[pack]) for pack in _PACKS}
    projection = {
        "schema_version": 1,
        "execution_authority": "qa/parity-contract-v3.yaml",
        "source_counts": counts,
        "source_hashes": hashes,
        "active_aliases": aliases,
        "boundary_status_counts": dict(sorted(statuses.items())),
        "requires_0_3_239_rows": [],
    }
    return SourceAuthorityReport(
        source_counts=MappingProxyType(counts),
        source_hashes=MappingProxyType(hashes),
        active_aliases=MappingProxyType(aliases),
        boundary_status_counts=MappingProxyType(dict(sorted(statuses.items()))),
        authority_hash=sha256_value(projection),
    )


__all__ = [
    "SourceAuthorityReport",
    "SourceAuthorityViolation",
    "validate_source_authority",
]
