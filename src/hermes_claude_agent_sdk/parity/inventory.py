"""Deterministic, fail-closed tool inventory and schema comparison."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from .hashing import json_compatible, sha256_value
from .profile import load_profile_manifest
from .tool_inventory import (
    capture_observed_tool_rows,
    declared_tool_schemas,
    inventory_rows_from_schemas,
)


class InventoryViolation(ValueError):
    """Declared and observed tool surfaces are unsafe or have drifted."""


_ALLOWED_ROOT = {
    "schema_version",
    "profile_id",
    "profile_hash",
    "declared_tools",
    "observed_tools",
}
_ALLOWED_TOOL = {"name", "input_schema"}
_MAX_INVENTORY_BYTES = 2 * 1024 * 1024


def _normalize_tools(value: Any, field: str) -> tuple[Mapping[str, str], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise InventoryViolation(f"{field} must be a list")
    normalized: list[Mapping[str, str]] = []
    names: list[str] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != _ALLOWED_TOOL:
            raise InventoryViolation(f"{field}[{index}] must contain only name and input_schema")
        name = raw.get("name")
        schema = raw.get("input_schema")
        if not isinstance(name, str) or not name.strip():
            raise InventoryViolation(f"{field}[{index}].name must be a non-empty string")
        if not isinstance(schema, Mapping):
            raise InventoryViolation(f"{field}[{index}].input_schema must be a mapping")
        try:
            schema_copy = json_compatible(schema)
        except TypeError as exc:
            raise InventoryViolation(f"{field}[{index}].input_schema is not JSON-compatible") from exc
        names.append(name)
        normalized.append(
            MappingProxyType(
                {
                    "name": name,
                    "schema_hash": sha256_value(schema_copy),
                }
            )
        )
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        raise InventoryViolation(f"{field} contains duplicate tool names: {duplicates}")
    return tuple(sorted(normalized, key=lambda item: item["name"]))


@dataclass(frozen=True, slots=True)
class ToolInventory:
    profile_id: str
    profile_hash: str
    declared_tools: tuple[Mapping[str, str], ...]
    observed_tools: tuple[Mapping[str, str], ...]
    inventory_hash: str

    @property
    def tool_count(self) -> int:
        return len(self.observed_tools)


def load_tool_inventory(
    path: str | Path,
    *,
    expected_profile: str | None = None,
    expected_profile_hash: str | None = None,
) -> ToolInventory:
    inventory_path = Path(path).expanduser().resolve()
    if not inventory_path.is_file():
        raise InventoryViolation(f"tool inventory is not a regular file: {inventory_path}")
    if inventory_path.stat().st_size > _MAX_INVENTORY_BYTES:
        raise InventoryViolation("tool inventory exceeds the bounded file size")
    try:
        loaded = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
        root = json_compatible(loaded)
    except (OSError, UnicodeError, yaml.YAMLError, TypeError) as exc:
        raise InventoryViolation(f"tool inventory cannot be parsed safely: {exc}") from exc
    if not isinstance(root, Mapping) or set(root) != _ALLOWED_ROOT:
        raise InventoryViolation(
            "tool inventory must contain only schema_version, profile_id, profile_hash, declared_tools, and observed_tools"
        )
    if root["schema_version"] != 1:
        raise InventoryViolation("tool inventory schema_version must equal 1")
    profile_id = root["profile_id"]
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise InventoryViolation("tool inventory profile_id must be a non-empty string")
    if expected_profile is not None and profile_id != expected_profile:
        raise InventoryViolation("tool inventory profile does not match the requested profile")
    profile_hash = root["profile_hash"]
    if (
        not isinstance(profile_hash, str)
        or len(profile_hash) != 64
        or any(character not in "0123456789abcdef" for character in profile_hash)
    ):
        raise InventoryViolation("tool inventory profile_hash must be a lowercase SHA-256 digest")
    if expected_profile_hash is not None and profile_hash != expected_profile_hash:
        raise InventoryViolation("tool inventory profile_hash does not match the profile manifest")
    declared = _normalize_tools(root["declared_tools"], "declared_tools")
    observed = _normalize_tools(root["observed_tools"], "observed_tools")
    if declared != observed:
        declared_by_name = {item["name"]: item["schema_hash"] for item in declared}
        observed_by_name = {item["name"]: item["schema_hash"] for item in observed}
        missing = sorted(set(declared_by_name) - set(observed_by_name))
        unknown = sorted(set(observed_by_name) - set(declared_by_name))
        schema_drift = sorted(
            name
            for name in set(declared_by_name) & set(observed_by_name)
            if declared_by_name[name] != observed_by_name[name]
        )
        raise InventoryViolation(
            f"tool inventory drift: missing={missing}, unknown={unknown}, schema_drift={schema_drift}"
        )
    payload = {
        "schema_version": 1,
        "profile_id": profile_id,
        "profile_hash": profile_hash,
        "tools": [dict(item) for item in observed],
    }
    return ToolInventory(
        profile_id=profile_id,
        profile_hash=profile_hash,
        declared_tools=declared,
        observed_tools=observed,
        inventory_hash=sha256_value(payload),
    )


def capture_tool_inventory(
    profile_manifest_path: str | Path,
    *,
    expected_profile: str,
) -> dict[str, Any]:
    """Capture declared and bridge-observed schemas for one isolated profile."""

    profile = load_profile_manifest(
        profile_manifest_path,
        expected_profile=expected_profile,
    )
    declared = inventory_rows_from_schemas(declared_tool_schemas())
    observed = capture_observed_tool_rows()
    return {
        "schema_version": 1,
        "profile_id": profile.profile_id,
        "profile_hash": profile.manifest_hash,
        "declared_tools": list(declared),
        "observed_tools": list(observed),
    }


__all__ = [
    "InventoryViolation",
    "ToolInventory",
    "capture_tool_inventory",
    "load_tool_inventory",
]
