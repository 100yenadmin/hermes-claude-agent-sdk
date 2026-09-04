"""Exact, sanitized Hermes tool inventory for parity-v4 Gateway runs."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .hashing import json_compatible, sha256_value
from .v4_gateway import HERMES_DISCOVERY_TOOLS, TOOL_PREFIX

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "profile_id",
        "profile_sha256",
        "host_sha",
        "delivered_tools",
        "executable_tools",
        "inventory_sha256",
    }
)
_ENTRY_FIELDS = frozenset({"name", "schema_sha256"})
_NATIVE_CLAUDE_TOOLS = frozenset({"agent", "bash", "read", "write", "edit", "web"})
_MAX_BYTES = 2 * 1024 * 1024


class V4GatewayInventoryViolation(ValueError):
    """A v4 inventory is malformed, stale, or admits a native Claude tool."""


@dataclass(frozen=True, slots=True)
class V4GatewayTool:
    name: str
    schema_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "schema_sha256": self.schema_sha256}


@dataclass(frozen=True, slots=True)
class V4GatewayInventory:
    profile_id: str
    profile_sha256: str
    host_sha: str
    delivered_tools: tuple[V4GatewayTool, ...]
    executable_tools: tuple[V4GatewayTool, ...]
    inventory_sha256: str

    @property
    def delivered_names(self) -> frozenset[str]:
        return frozenset(item.name for item in self.delivered_tools)

    @property
    def executable_names(self) -> frozenset[str]:
        return frozenset(item.name for item in self.executable_tools)

    @property
    def host_event_names(self) -> frozenset[str]:
        return self.delivered_names | self.executable_names

    @property
    def mcp_event_names(self) -> frozenset[str]:
        return frozenset(f"{TOOL_PREFIX}{name}" for name in self.delivered_names)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": 1,
            "profile_id": self.profile_id,
            "profile_sha256": self.profile_sha256,
            "host_sha": self.host_sha,
            "delivered_tools": [item.to_dict() for item in self.delivered_tools],
            "executable_tools": [item.to_dict() for item in self.executable_tools],
        }
        return {**payload, "inventory_sha256": self.inventory_sha256}


def _digest(value: Any, field: str, *, size: int = 64) -> str:
    pattern = _HEX40 if size == 40 else _HEX64
    if not isinstance(value, str) or pattern.fullmatch(value) is None or value == "0" * size:
        raise V4GatewayInventoryViolation(f"{field} is not a nonzero lowercase digest")
    return value


def _name(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or _SAFE_NAME.fullmatch(value) is None
        or value.casefold() in _NATIVE_CLAUDE_TOOLS
        or value.startswith("mcp__")
    ):
        raise V4GatewayInventoryViolation(f"{field} is invalid or Claude-native")
    return value


def _entries(value: Any, field: str) -> tuple[V4GatewayTool, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or not value:
        raise V4GatewayInventoryViolation(f"{field} must be a non-empty list")
    entries: list[V4GatewayTool] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != _ENTRY_FIELDS:
            raise V4GatewayInventoryViolation(f"{field}[{index}] fields are not closed")
        entries.append(
            V4GatewayTool(
                _name(raw.get("name"), f"{field}[{index}].name"),
                _digest(raw.get("schema_sha256"), f"{field}[{index}].schema_sha256"),
            )
        )
    if tuple(item.name for item in entries) != tuple(sorted(item.name for item in entries)):
        raise V4GatewayInventoryViolation(f"{field} is not sorted")
    if len({item.name for item in entries}) != len(entries):
        raise V4GatewayInventoryViolation(f"{field} contains duplicate names")
    return tuple(entries)


def _build(
    *,
    profile_id: Any,
    profile_sha256: Any,
    host_sha: Any,
    delivered_tools: Any,
    executable_tools: Any,
    claimed_sha256: Any | None = None,
) -> V4GatewayInventory:
    if not isinstance(profile_id, str) or not profile_id or len(profile_id) > 128:
        raise V4GatewayInventoryViolation("profile_id is invalid")
    profile_digest = _digest(profile_sha256, "profile_sha256")
    host_digest = _digest(host_sha, "host_sha", size=40)
    delivered = _entries(delivered_tools, "delivered_tools")
    executable = _entries(executable_tools, "executable_tools")
    delivered_by_name = {item.name: item.schema_sha256 for item in delivered}
    executable_by_name = {item.name: item.schema_sha256 for item in executable}
    unsupported_difference = set(delivered_by_name) - set(executable_by_name) - set(HERMES_DISCOVERY_TOOLS)
    if unsupported_difference:
        raise V4GatewayInventoryViolation(
            "delivered tools absent from the executable surface are not Hermes discovery bridges"
        )
    mismatched = {
        name
        for name in set(delivered_by_name) & set(executable_by_name)
        if delivered_by_name[name] != executable_by_name[name]
    }
    if mismatched:
        raise V4GatewayInventoryViolation("shared delivered/executable tool schemas differ")
    payload = {
        "schema_version": 1,
        "profile_id": profile_id,
        "profile_sha256": profile_digest,
        "host_sha": host_digest,
        "delivered_tools": [item.to_dict() for item in delivered],
        "executable_tools": [item.to_dict() for item in executable],
    }
    digest = sha256_value(payload)
    if claimed_sha256 is not None and _digest(claimed_sha256, "inventory_sha256") != digest:
        raise V4GatewayInventoryViolation("inventory_sha256 does not match the canonical inventory")
    return V4GatewayInventory(
        profile_id=profile_id,
        profile_sha256=profile_digest,
        host_sha=host_digest,
        delivered_tools=delivered,
        executable_tools=executable,
        inventory_sha256=digest,
    )


def _schema_entries(value: Any, field: str) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise V4GatewayInventoryViolation(f"{field} must be a list")
    rows: list[dict[str, str]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise V4GatewayInventoryViolation(f"{field}[{index}] must be a mapping")
        function = raw.get("function") if raw.get("type") == "function" else raw
        if not isinstance(function, Mapping):
            raise V4GatewayInventoryViolation(f"{field}[{index}] has no function schema")
        name = _name(function.get("name"), f"{field}[{index}].name")
        parameters = function.get("parameters", {})
        if not isinstance(parameters, Mapping):
            raise V4GatewayInventoryViolation(f"{field}[{index}] parameters are invalid")
        try:
            normalized = json_compatible(parameters)
        except TypeError as exc:
            raise V4GatewayInventoryViolation(f"{field}[{index}] parameters are not JSON-compatible") from exc
        rows.append({"name": name, "schema_sha256": sha256_value(normalized)})
    return sorted(rows, key=lambda item: item["name"])


def build_v4_gateway_inventory(
    *,
    profile_id: str,
    profile_sha256: str,
    host_sha: str,
    delivered_schemas: Sequence[Mapping[str, Any]],
    executable_schemas: Sequence[Mapping[str, Any]],
) -> V4GatewayInventory:
    """Build one inventory from the host's delivered and pre-assembly schemas."""

    return _build(
        profile_id=profile_id,
        profile_sha256=profile_sha256,
        host_sha=host_sha,
        delivered_tools=_schema_entries(delivered_schemas, "delivered_schemas"),
        executable_tools=_schema_entries(executable_schemas, "executable_schemas"),
    )


def load_v4_gateway_inventory(
    value: V4GatewayInventory | Mapping[str, Any] | str | Path,
    *,
    expected_profile_id: str | None = None,
    expected_profile_sha256: str | None = None,
    expected_host_sha: str | None = None,
) -> V4GatewayInventory:
    if isinstance(value, V4GatewayInventory):
        raw = value.to_dict()
    elif isinstance(value, Mapping):
        raw = dict(value)
    else:
        path = Path(value).expanduser().resolve()
        if not path.is_file() or path.stat().st_size > _MAX_BYTES:
            raise V4GatewayInventoryViolation("inventory path is not a bounded regular file")
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            raw = json_compatible(loaded)
        except (OSError, UnicodeError, yaml.YAMLError, TypeError) as exc:
            raise V4GatewayInventoryViolation("inventory cannot be parsed safely") from exc
    if not isinstance(raw, Mapping) or set(raw) != _ROOT_FIELDS or raw.get("schema_version") != 1:
        raise V4GatewayInventoryViolation("inventory envelope fields are not closed")
    inventory = _build(
        profile_id=raw.get("profile_id"),
        profile_sha256=raw.get("profile_sha256"),
        host_sha=raw.get("host_sha"),
        delivered_tools=raw.get("delivered_tools"),
        executable_tools=raw.get("executable_tools"),
        claimed_sha256=raw.get("inventory_sha256"),
    )
    if expected_profile_id is not None and inventory.profile_id != expected_profile_id:
        raise V4GatewayInventoryViolation("inventory profile_id does not match the candidate")
    if expected_profile_sha256 is not None and inventory.profile_sha256 != expected_profile_sha256:
        raise V4GatewayInventoryViolation("inventory profile_sha256 does not match the candidate")
    if expected_host_sha is not None and inventory.host_sha != expected_host_sha:
        raise V4GatewayInventoryViolation("inventory host_sha does not match the candidate")
    return inventory


__all__ = [
    "V4GatewayInventory",
    "V4GatewayInventoryViolation",
    "V4GatewayTool",
    "build_v4_gateway_inventory",
    "load_v4_gateway_inventory",
]
