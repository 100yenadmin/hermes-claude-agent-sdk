"""Strict loader for the versioned feature-first parity catalog."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from .hashing import json_compatible, sha256_file, sha256_value


class CatalogViolation(ValueError):
    """The catalog cannot be used safely or reproducibly."""


_CAPABILITY_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,127}$")
_SOURCE_PACKS = frozenset(
    {
        "v2_non_soak",
        "openclaw_active",
        "agent_sdk_boundary",
        "clawprobench_native",
        "runtime_active",
    }
)
_SOURCE_COUNTS = {
    "v2_non_soak": 53,
    "openclaw_active": 12,
    "agent_sdk_boundary": 23,
    "clawprobench_native": 36,
}
_SOURCE_ID_HASHES = {
    "v2_non_soak": "6933d6abe587ffafdf20d63485445851f2ab0c0b2c12ab2e21b146271e72f861",
    "openclaw_active": "5542995d9213b31b227910ef7337057cb01301b2d87546c5375459e9184439a7",
    "agent_sdk_boundary": "3b5aaf3c5282061fd7a7f34345d514aad55b91ee820fd65b99133b36dab85b32",
    "clawprobench_native": "b0c79c09ea2f640962f875ecc222235f9f26df59cedb077fef740a03a8bd9048",
}
_PINNED_REFERENCES = {
    ("hermes_v2", "sha256"): "e4842f3a78c855f18af59a8024c4360bde59143987d133724b81594d0f0bfe2e",
    ("openclaw", "commit"): "ea806575e6450e4d1efdfc72c19f04be982a1b9b",
    ("clawprobench", "commit"): "c4b8395854fe0752eef435b44f140366efd44d8e",
}
_REQUIRED_CAPABILITY_FIELDS = frozenset(
    {
        "capability_id",
        "source_item_id",
        "source_pack",
        "source_ref",
        "lane",
        "surface",
        "owner",
        "positive_path",
        "denial_path",
        "recovery_path",
        "state_before",
        "state_after",
        "expected_trace",
        "primary_proof",
        "secondary_proof",
        "repeat_policy",
        "execution_id",
    }
)
_PATH_NAMES = ("positive", "denial", "recovery")
_REPEAT_TRIGGERS = frozenset({"consequential", "initially_failing", "unstable"})
_MAX_CATALOG_BYTES = 8 * 1024 * 1024


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CatalogViolation(f"{field} must be a mapping")
    return dict(value)


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogViolation(f"{field} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CatalogViolation(f"{field} must be a list of strings")
    result = tuple(_nonempty_string(item, field) for item in value)
    if not result and not allow_empty:
        raise CatalogViolation(f"{field} must not be empty")
    return result


def _validate_path(value: Any, field: str) -> Mapping[str, Any]:
    path = _mapping(value, field)
    unknown = set(path) - {"required", "assertion", "rationale"}
    if unknown:
        raise CatalogViolation(f"{field} has unknown fields: {sorted(unknown)}")
    if type(path.get("required")) is not bool:
        raise CatalogViolation(f"{field}.required must be a boolean")
    _nonempty_string(path.get("assertion"), f"{field}.assertion")
    if "rationale" in path:
        _nonempty_string(path["rationale"], f"{field}.rationale")
    if path["required"] is False and "rationale" not in path:
        raise CatalogViolation(f"{field}.rationale is required when the path is not required")
    return _freeze(path)


def _validate_repeat_policy(value: Any, field: str) -> Mapping[str, Any]:
    policy = _mapping(value, field)
    unknown = set(policy) - {"minimum_passes", "consecutive_passes", "triggers"}
    if unknown:
        raise CatalogViolation(f"{field} has unknown fields: {sorted(unknown)}")
    minimum = policy.get("minimum_passes")
    consecutive = policy.get("consecutive_passes")
    if type(minimum) is not int or minimum < 1:
        raise CatalogViolation(f"{field}.minimum_passes must be a positive integer")
    if type(consecutive) is not int or consecutive < 1:
        raise CatalogViolation(f"{field}.consecutive_passes must be a positive integer")
    if consecutive < minimum:
        raise CatalogViolation(f"{field}.consecutive_passes cannot be below minimum_passes")
    triggers = frozenset(_string_list(policy.get("triggers", []), f"{field}.triggers", allow_empty=True))
    if not triggers <= _REPEAT_TRIGGERS:
        raise CatalogViolation(
            f"{field}.triggers contains unsupported values: {sorted(triggers - _REPEAT_TRIGGERS)}"
        )
    return _freeze(policy)


@dataclass(frozen=True, slots=True)
class Capability:
    """One source-visible contract row backed by a deduplicatable execution id."""

    capability_id: str
    source_item_id: str
    source_pack: str
    source_ref: str
    lane: str
    surface: str
    owner: str
    paths: Mapping[str, Mapping[str, Any]]
    state_before: str
    state_after: str
    expected_trace: tuple[str, ...]
    primary_proof: tuple[str, ...]
    secondary_proof: tuple[str, ...]
    repeat_policy: Mapping[str, Any]
    execution_id: str
    temporal_reason: str | None = None
    sdk_ledger_status: str | None = None

    def path(self, name: str) -> Mapping[str, Any]:
        try:
            return self.paths[name]
        except KeyError as exc:
            raise KeyError(f"unknown capability path: {name}") from exc


@dataclass(frozen=True, slots=True)
class Catalog:
    """Validated immutable catalog plus independently useful hashes."""

    path: Path
    contract: Mapping[str, Any]
    capabilities: tuple[Capability, ...]
    contract_hash: str
    catalog_hash: str
    file_hash: str

    @property
    def version(self) -> str:
        return str(self.contract["version"])

    @property
    def by_id(self) -> Mapping[str, Capability]:
        return MappingProxyType({item.capability_id: item for item in self.capabilities})

    def for_lane(self, lane: str) -> tuple[Capability, ...]:
        if lane not in {"rc", "runtime"}:
            raise CatalogViolation(f"unsupported lane: {lane}")
        return tuple(item for item in self.capabilities if item.lane == lane)

    @property
    def source_counts(self) -> Mapping[str, int]:
        return MappingProxyType(dict(Counter(item.source_pack for item in self.capabilities)))


def _validate_references(contract: Mapping[str, Any]) -> None:
    references = _mapping(contract.get("references"), "contract.references")
    for (name, field), expected in _PINNED_REFERENCES.items():
        reference = _mapping(references.get(name), f"contract.references.{name}")
        actual = reference.get(field)
        if actual != expected:
            raise CatalogViolation(
                f"contract.references.{name}.{field} must equal pinned value {expected}"
            )


def _validate_source_manifest(contract: Mapping[str, Any], capabilities: Sequence[Capability]) -> None:
    coverage = _mapping(contract.get("required_coverage"), "contract.required_coverage")
    manifest = _mapping(contract.get("source_manifest"), "contract.source_manifest")
    counts = Counter(item.source_pack for item in capabilities)
    for pack, expected_count in _SOURCE_COUNTS.items():
        if coverage.get(pack) != expected_count:
            raise CatalogViolation(
                f"contract.required_coverage.{pack} must equal {expected_count}"
            )
        if counts[pack] != expected_count:
            raise CatalogViolation(
                f"source pack {pack} has {counts[pack]} rows; expected {expected_count}"
            )
        ids = sorted(item.source_item_id for item in capabilities if item.source_pack == pack)
        actual_hash = sha256_value(ids)
        expected_hash = _SOURCE_ID_HASHES[pack]
        if actual_hash != expected_hash:
            raise CatalogViolation(
                f"source pack {pack} item set does not match its pinned v3 manifest"
            )
        manifest_entry = _mapping(manifest.get(pack), f"contract.source_manifest.{pack}")
        if manifest_entry.get("count") != expected_count or manifest_entry.get("ids_sha256") != expected_hash:
            raise CatalogViolation(
                f"contract.source_manifest.{pack} must bind count and pinned ids_sha256"
            )
    if counts["runtime_active"] < 1:
        raise CatalogViolation("at least one runtime_active capability is required")


def _load_capability(raw: Any, index: int) -> Capability:
    item = _mapping(raw, f"capabilities[{index}]")
    missing = _REQUIRED_CAPABILITY_FIELDS - set(item)
    if missing:
        raise CatalogViolation(f"capabilities[{index}] is missing fields: {sorted(missing)}")
    allowed = _REQUIRED_CAPABILITY_FIELDS | {
        "title",
        "temporal_reason",
        "sdk_ledger_status",
    }
    unknown = set(item) - allowed
    if unknown:
        raise CatalogViolation(f"capabilities[{index}] has unknown fields: {sorted(unknown)}")

    capability_id = _nonempty_string(item["capability_id"], f"capabilities[{index}].capability_id")
    if not _CAPABILITY_ID.fullmatch(capability_id):
        raise CatalogViolation(f"capabilities[{index}].capability_id is malformed")
    source_item_id = _nonempty_string(item["source_item_id"], f"capabilities[{index}].source_item_id")
    source_pack = _nonempty_string(item["source_pack"], f"capabilities[{index}].source_pack")
    if source_pack not in _SOURCE_PACKS:
        raise CatalogViolation(f"capabilities[{index}].source_pack is unsupported")
    lane = _nonempty_string(item["lane"], f"capabilities[{index}].lane")
    if lane not in {"rc", "runtime"}:
        raise CatalogViolation(f"capabilities[{index}].lane must be rc or runtime")
    if source_pack == "runtime_active" and lane != "runtime":
        raise CatalogViolation("runtime_active capabilities must use the runtime lane")
    if source_pack != "runtime_active" and lane != "rc":
        raise CatalogViolation(f"{source_pack} capabilities must use the rc lane")

    temporal_reason = item.get("temporal_reason")
    if temporal_reason is not None:
        temporal_reason = _nonempty_string(
            temporal_reason, f"capabilities[{index}].temporal_reason"
        )
        if lane != "runtime":
            raise CatalogViolation("temporal_reason is allowed only in the runtime lane")

    sdk_ledger_status = item.get("sdk_ledger_status")
    if source_pack == "agent_sdk_boundary":
        sdk_ledger_status = _nonempty_string(
            sdk_ledger_status, f"capabilities[{index}].sdk_ledger_status"
        )
        if sdk_ledger_status not in {
            "covered_current",
            "equivalent_host",
            "requires_0_3_239",
            "not_runtime_applicable",
        }:
            raise CatalogViolation(f"capabilities[{index}].sdk_ledger_status is unsupported")
    elif sdk_ledger_status is not None:
        raise CatalogViolation("sdk_ledger_status is allowed only for agent_sdk_boundary")

    paths = MappingProxyType(
        {
            name: _validate_path(item[f"{name}_path"], f"capabilities[{index}].{name}_path")
            for name in _PATH_NAMES
        }
    )
    if source_pack in {"v2_non_soak", "agent_sdk_boundary"}:
        if paths["positive"]["required"] is not True:
            raise CatalogViolation(
                f"{source_pack} positive_path.required must be true"
            )
        for path_name in ("denial", "recovery"):
            if paths[path_name]["required"] is not False:
                raise CatalogViolation(
                    f"{source_pack} {path_name}_path.required must be false"
                )
    elif not all(path["required"] is True for path in paths.values()):
        raise CatalogViolation(
            f"{source_pack} positive, denial, and recovery paths must be required"
        )
    return Capability(
        capability_id=capability_id,
        source_item_id=source_item_id,
        source_pack=source_pack,
        source_ref=_nonempty_string(item["source_ref"], f"capabilities[{index}].source_ref"),
        lane=lane,
        surface=_nonempty_string(item["surface"], f"capabilities[{index}].surface"),
        owner=_nonempty_string(item["owner"], f"capabilities[{index}].owner"),
        paths=paths,
        state_before=_nonempty_string(item["state_before"], f"capabilities[{index}].state_before"),
        state_after=_nonempty_string(item["state_after"], f"capabilities[{index}].state_after"),
        expected_trace=_string_list(item["expected_trace"], f"capabilities[{index}].expected_trace"),
        primary_proof=_string_list(item["primary_proof"], f"capabilities[{index}].primary_proof"),
        secondary_proof=_string_list(item["secondary_proof"], f"capabilities[{index}].secondary_proof"),
        repeat_policy=_validate_repeat_policy(
            item["repeat_policy"], f"capabilities[{index}].repeat_policy"
        ),
        execution_id=_nonempty_string(item["execution_id"], f"capabilities[{index}].execution_id"),
        temporal_reason=temporal_reason,
        sdk_ledger_status=sdk_ledger_status,
    )


def load_catalog(path: str | Path) -> Catalog:
    """Load and validate a parity-v3 YAML catalog without accepting drift."""

    catalog_path = Path(path).expanduser().resolve()
    if not catalog_path.is_file():
        raise CatalogViolation(f"catalog is not a regular file: {catalog_path}")
    if catalog_path.stat().st_size > _MAX_CATALOG_BYTES:
        raise CatalogViolation("catalog exceeds the bounded file size")
    try:
        loaded = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
        document = json_compatible(loaded)
    except (OSError, UnicodeError, yaml.YAMLError, TypeError) as exc:
        raise CatalogViolation(f"catalog cannot be parsed safely: {exc}") from exc
    root = _mapping(document, "catalog")
    if set(root) != {"schema_version", "contract", "capabilities"}:
        raise CatalogViolation("catalog must contain only schema_version, contract, and capabilities")
    if root["schema_version"] != 1:
        raise CatalogViolation("catalog schema_version must equal 1")
    contract = _mapping(root["contract"], "contract")
    if contract.get("name") != "Hermes Agent SDK Feature Parity":
        raise CatalogViolation("contract.name is not the pinned v3 contract")
    if contract.get("version") != "3.0.0":
        raise CatalogViolation("contract.version must equal 3.0.0")
    if _mapping(contract.get("tool_inventory"), "contract.tool_inventory").get("fail_closed") is not True:
        raise CatalogViolation("contract.tool_inventory.fail_closed must be true")
    profile_policy = _mapping(contract.get("profile_policy"), "contract.profile_policy")
    if profile_policy.get("allowed_ids") != ["fable-v3-isolated"]:
        raise CatalogViolation("contract.profile_policy must allow only fable-v3-isolated")
    if profile_policy.get("shared_profiles_forbidden") is not True:
        raise CatalogViolation("contract.profile_policy must forbid shared profiles")
    _validate_references(contract)

    raw_capabilities = root["capabilities"]
    if not isinstance(raw_capabilities, Sequence) or isinstance(
        raw_capabilities, (str, bytes, bytearray)
    ):
        raise CatalogViolation("capabilities must be a list")
    capabilities = tuple(_load_capability(item, index) for index, item in enumerate(raw_capabilities))
    if not capabilities:
        raise CatalogViolation("capabilities must not be empty")
    capability_ids = [item.capability_id for item in capabilities]
    if len(capability_ids) != len(set(capability_ids)):
        duplicates = sorted(name for name, count in Counter(capability_ids).items() if count > 1)
        raise CatalogViolation(f"capability_id values must be unique: {duplicates}")
    source_keys = [(item.source_pack, item.source_item_id) for item in capabilities]
    if len(source_keys) != len(set(source_keys)):
        raise CatalogViolation("source_pack/source_item_id pairs must be unique")
    _validate_source_manifest(contract, capabilities)

    return Catalog(
        path=catalog_path,
        contract=_freeze(contract),
        capabilities=capabilities,
        contract_hash=sha256_value(contract),
        catalog_hash=sha256_value(root),
        file_hash=sha256_file(catalog_path),
    )


__all__ = ["Capability", "Catalog", "CatalogViolation", "load_catalog"]
